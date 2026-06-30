#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ai_brief_grounding import verify_grounding_hash
from ai_brief_openai import (
    MAX_ATTEMPTS,
    MAX_GROUNDING_BYTES,
    OPENAI_RESPONSES_URL,
    OpenAIResponsesProvider,
    TransportResponse,
    build_openai_provider,
    urllib_transport,
)
from ai_brief_provider import (
    ProviderConfig,
    generate_ai_brief,
)

ROOT = Path(__file__).resolve().parent
GROUNDING_FIXTURE = (
    ROOT
    / "contracts"
    / "examples"
    / "ai_investment_brief_grounding.ready.json"
)
CONTRACT_SCHEMA = (
    ROOT / "contracts" / "ai_investment_brief.schema.json"
)

CONFIRM_ENV = "THESISOS_AI_BRIEF_LIVE_SMOKE_CONFIRM"
CONFIRM_VALUE = "RUN_ONE_REAL_CALL"
API_KEY_ENV = "THESISOS_AI_BRIEF_API_KEY"

ALLOWED_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
)
SECRET_PATTERN = re.compile(
    r"(?:sk|sess|proj)-[A-Za-z0-9_-]{8,}",
    re.IGNORECASE,
)


class LiveSmokeGuardError(RuntimeError):
    pass


class LiveSmokeLimitError(RuntimeError):
    pass


class LiveSmokeValidationError(RuntimeError):
    pass


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(value, dict):
        raise LiveSmokeValidationError(
            f"{path.name} must contain a JSON object."
        )

    return value


def load_grounding_fixture() -> dict:
    bundle = _load_json(GROUNDING_FIXTURE)

    if bundle.get("source") != "thesisos":
        raise LiveSmokeValidationError(
            "Grounding source must be ThesisOS."
        )

    if bundle.get("coverage", {}).get("status") != "ready":
        raise LiveSmokeValidationError(
            "Grounding fixture must be ready."
        )

    if not verify_grounding_hash(bundle):
        raise LiveSmokeValidationError(
            "Grounding fixture hash is invalid."
        )

    serialized = json.dumps(
        bundle,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    if len(serialized) > MAX_GROUNDING_BYTES:
        raise LiveSmokeValidationError(
            "Grounding fixture exceeds the adapter limit."
        )

    return bundle


def live_config(
    environ: Mapping[str, str],
) -> ProviderConfig:
    source = dict(environ)
    source["THESISOS_AI_BRIEF_ENABLED"] = "true"
    source["THESISOS_AI_BRIEF_PROVIDER"] = "openai"
    return ProviderConfig.from_env(source)


def _safe_usage(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}

    usage = {}

    for field in ALLOWED_USAGE_FIELDS:
        raw = value.get(field)

        if isinstance(raw, int) and raw >= 0:
            usage[field] = raw

    input_details = value.get("input_tokens_details")

    if isinstance(input_details, Mapping):
        cached = input_details.get("cached_tokens")

        if isinstance(cached, int) and cached >= 0:
            usage["cached_tokens"] = cached

    output_details = value.get("output_tokens_details")

    if isinstance(output_details, Mapping):
        reasoning = output_details.get("reasoning_tokens")

        if isinstance(reasoning, int) and reasoning >= 0:
            usage["reasoning_tokens"] = reasoning

    return usage


def _redact_error(
    error: BaseException,
    api_key: str | None,
) -> str:
    message = str(error).strip() or error.__class__.__name__

    if api_key:
        message = message.replace(api_key, "[REDACTED]")

    message = SECRET_PATTERN.sub("[REDACTED]", message)
    message = " ".join(message.split())

    return message[:240]


class SingleActualRequestTransport:
    def __init__(
        self,
        inner: Callable[..., TransportResponse],
    ) -> None:
        self.inner = inner
        self.attempts = 0
        self.actual_calls = 0
        self.response_id: str | None = None
        self.request_id: str | None = None
        self.usage: dict = {}
        self.request_metadata: dict = {}

    def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> TransportResponse:
        self.attempts += 1

        if self.actual_calls >= 1:
            raise LiveSmokeLimitError(
                "A second real provider request was blocked."
            )

        request_payload = json.loads(body.decode("utf-8"))

        if not isinstance(request_payload, dict):
            raise LiveSmokeValidationError(
                "Provider request must be a JSON object."
            )

        request_payload["store"] = False

        if "tools" in request_payload:
            raise LiveSmokeValidationError(
                "The live smoke request must not enable tools."
            )

        response_format = (
            request_payload
            .get("text", {})
            .get("format", {})
        )

        if (
            response_format.get("type") != "json_schema"
            or response_format.get("strict") is not True
        ):
            raise LiveSmokeValidationError(
                "Strict Structured Outputs are required."
            )

        request_body = json.dumps(
            request_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.request_metadata = {
            "endpoint": url,
            "model": request_payload.get("model"),
            "store": request_payload.get("store"),
            "strict": response_format.get("strict"),
            "max_output_tokens": request_payload.get(
                "max_output_tokens"
            ),
            "request_bytes": len(request_body),
        }

        self.actual_calls += 1

        response = self.inner(
            url=url,
            headers=headers,
            body=request_body,
            timeout_seconds=timeout_seconds,
        )

        self.request_id = response.headers.get("x-request-id")

        try:
            response_payload = json.loads(
                response.body.decode("utf-8")
            )
        except Exception:
            response_payload = {}

        if isinstance(response_payload, Mapping):
            response_id = response_payload.get("id")

            if isinstance(response_id, str):
                self.response_id = response_id

            self.usage = _safe_usage(
                response_payload.get("usage")
            )

        return response


def validate_contract(
    brief: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> None:
    schema = _load_json(CONTRACT_SCHEMA)
    required = set(schema.get("required", []))
    properties = set(
        (schema.get("properties") or {}).keys()
    )

    missing = sorted(required - set(brief))

    if missing:
        raise LiveSmokeValidationError(
            "Missing contract fields: " + ", ".join(missing)
        )

    if schema.get("additionalProperties") is False:
        extras = sorted(set(brief) - properties)

        if extras:
            raise LiveSmokeValidationError(
                "Unexpected contract fields: "
                + ", ".join(extras)
            )

    statuses = set(
        schema["properties"]["status"]["enum"]
    )
    actions = set(
        schema["$defs"]["decision"][
            "properties"
        ]["action"]["enum"]
    )

    if brief.get("status") not in statuses:
        raise LiveSmokeValidationError(
            "Invalid brief status."
        )

    decision = brief.get("decision")

    if not isinstance(decision, Mapping):
        raise LiveSmokeValidationError(
            "Decision must be an object."
        )

    if decision.get("action") not in actions:
        raise LiveSmokeValidationError(
            "Invalid decision action."
        )

    confidence = decision.get("confidence")

    if (
        not isinstance(confidence, int)
        or not 0 <= confidence <= 100
    ):
        raise LiveSmokeValidationError(
            "Decision confidence is invalid."
        )

    grounding = brief.get("grounding")

    if not isinstance(grounding, Mapping):
        raise LiveSmokeValidationError(
            "Brief grounding must be an object."
        )

    if grounding.get("source") != "thesisos":
        raise LiveSmokeValidationError(
            "Brief grounding source is invalid."
        )

    if (
        grounding.get("payload_sha256")
        != bundle.get("payload_sha256")
    ):
        raise LiveSmokeValidationError(
            "Brief grounding hash does not match."
        )


def dry_run_summary(
    environ: Mapping[str, str] | None = None,
) -> dict:
    source = dict(
        os.environ if environ is None else environ
    )
    source.setdefault(
        "THESISOS_AI_BRIEF_MODEL",
        "dry-run-model",
    )
    bundle = load_grounding_fixture()
    config = live_config(source)

    def no_network(*args, **kwargs):
        raise AssertionError(
            "Dry-run attempted network access."
        )

    provider = OpenAIResponsesProvider(
        api_key="dry-run-placeholder",
        model=config.model,
        transport=no_network,
        sleep_fn=lambda _: None,
    )

    request_payload = provider._request_payload(bundle)
    response_format = (
        request_payload
        .get("text", {})
        .get("format", {})
    )
    serialized = json.dumps(
        bundle,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return {
        "mode": "dry_run",
        "network_calls": 0,
        "secret_configured": bool(
            str(source.get(API_KEY_ENV) or "").strip()
        ),
        "confirmation_present": (
            source.get(CONFIRM_ENV) == CONFIRM_VALUE
        ),
        "provider": config.provider,
        "model": config.model,
        "endpoint": OPENAI_RESPONSES_URL,
        "grounding_status": bundle["coverage"]["status"],
        "grounding_bytes": len(serialized),
        "grounding_hash_prefix": (
            bundle["payload_sha256"][:16]
        ),
        "strict_structured_outputs": (
            response_format.get("type") == "json_schema"
            and response_format.get("strict") is True
        ),
        "tools_enabled": "tools" in request_payload,
        "store_will_be_forced_false": True,
        "max_output_tokens": request_payload.get(
            "max_output_tokens"
        ),
        "adapter_max_attempts": MAX_ATTEMPTS,
        "actual_request_limit": 1,
    }


def _base_live_summary(
    *,
    bundle: Mapping[str, Any],
    config: ProviderConfig,
    transport: SingleActualRequestTransport,
    elapsed_ms: int,
) -> dict:
    return {
        "mode": "live",
        "provider": config.provider,
        "model": config.model,
        "network_calls": transport.actual_calls,
        "transport_attempts": transport.attempts,
        "elapsed_ms": elapsed_ms,
        "grounding_hash_prefix": (
            str(bundle.get("payload_sha256") or "")[:16]
        ),
        "response_id": transport.response_id,
        "request_id": transport.request_id,
        "usage": dict(transport.usage),
        "request": dict(transport.request_metadata),
    }


def run_live(
    environ: Mapping[str, str] | None = None,
    *,
    transport: Callable[..., TransportResponse] = urllib_transport,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    source = dict(
        os.environ if environ is None else environ
    )

    if source.get(CONFIRM_ENV) != CONFIRM_VALUE:
        raise LiveSmokeGuardError(
            f"Set {CONFIRM_ENV}={CONFIRM_VALUE} "
            "for one live call."
        )

    api_key = str(source.get(API_KEY_ENV) or "").strip()

    if not api_key:
        raise LiveSmokeGuardError(
            f"Replit Secret {API_KEY_ENV} is missing."
        )

    model = str(
        source.get("THESISOS_AI_BRIEF_MODEL") or ""
    ).strip()

    if not model:
        raise LiveSmokeGuardError(
            "Set THESISOS_AI_BRIEF_MODEL explicitly "
            "for the live smoke test."
        )

    bundle = load_grounding_fixture()
    config = live_config(source)

    if config.provider != "openai":
        raise LiveSmokeGuardError(
            "Live smoke provider must be openai."
        )

    single_transport = SingleActualRequestTransport(
        transport
    )
    provider = build_openai_provider(
        config,
        environ=source,
        transport=single_transport,
        sleep_fn=lambda _: None,
    )

    if provider is None:
        raise LiveSmokeGuardError(
            "OpenAI provider could not be constructed."
        )

    started = clock()

    try:
        brief = generate_ai_brief(
            bundle,
            config,
            provider,
        )
    except Exception as error:
        elapsed_ms = max(
            0,
            int((clock() - started) * 1000),
        )
        summary = _base_live_summary(
            bundle=bundle,
            config=config,
            transport=single_transport,
            elapsed_ms=elapsed_ms,
        )
        summary.update({
            "schema_valid": False,
            "status": "exception",
            "error_type": error.__class__.__name__,
            "error": _redact_error(error, api_key),
            "success": False,
        })
        return summary

    elapsed_ms = max(
        0,
        int((clock() - started) * 1000),
    )
    summary = _base_live_summary(
        bundle=bundle,
        config=config,
        transport=single_transport,
        elapsed_ms=elapsed_ms,
    )

    try:
        validate_contract(brief, bundle)
        schema_valid = True
        validation_error = None
    except Exception as error:
        schema_valid = False
        validation_error = _redact_error(
            error,
            api_key,
        )

    decision = (
        brief.get("decision")
        if isinstance(brief, Mapping)
        else {}
    )
    model = (
        brief.get("model")
        if isinstance(brief, Mapping)
        else {}
    )
    error = (
        brief.get("error")
        if isinstance(brief, Mapping)
        else {}
    )

    if not isinstance(decision, Mapping):
        decision = {}
    if not isinstance(model, Mapping):
        model = {}
    if not isinstance(error, Mapping):
        error = {}

    status = brief.get("status")
    success = bool(
        status in {"ready", "partial"}
        and schema_valid
        and single_transport.actual_calls == 1
        and single_transport.request_metadata.get("store")
        is False
    )

    summary.update({
        "status": status,
        "decision_action": decision.get("action"),
        "confidence": decision.get("confidence"),
        "model": model.get("name") or config.model,
        "provider": model.get("provider") or config.provider,
        "response_id": (
            model.get("response_id")
            or single_transport.response_id
        ),
        "error_code": error.get("code"),
        "retryable": error.get("retryable"),
        "schema_valid": schema_valid,
        "validation_error": validation_error,
        "success": success,
    })

    return summary


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ThesisOS AI Brief one-call live smoke harness."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without network access.",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Perform at most one real provider request.",
    )
    args = parser.parse_args(argv)

    if not args.live:
        _print_json(dry_run_summary())
        return 0

    try:
        summary = run_live()
    except LiveSmokeGuardError as error:
        _print_json({
            "mode": "live",
            "network_calls": 0,
            "status": "blocked",
            "error_type": error.__class__.__name__,
            "error": str(error),
            "success": False,
        })
        return 2

    _print_json(summary)
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
