#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Mapping

from ai_brief_groq import (
    GROQ_API_KEY_ENV,
    GROQ_CHAT_COMPLETIONS_URL,
    GROQ_DEFAULT_MODEL,
    GROQ_PROVIDER_NAME,
    GroqChatCompletionsProvider,
    build_groq_provider,
)
from ai_brief_live_smoke import (
    LiveSmokeLimitError,
    LiveSmokeValidationError,
    _redact_error,
    load_grounding_fixture,
    validate_contract,
)
from ai_brief_openai import (
    TransportResponse,
    urllib_transport,
)
from ai_brief_provider import (
    ProviderConfig,
    generate_ai_brief,
)

CONFIRM_ENV = "THESISOS_AI_BRIEF_GROQ_SMOKE_CONFIRM"
CONFIRM_VALUE = "RUN_ONE_GROQ_CALL"


class GroqSmokeGuardError(RuntimeError):
    pass


def _timeout(source: Mapping[str, str]) -> float:
    raw = str(
        source.get(
            "THESISOS_AI_BRIEF_TIMEOUT_SECONDS",
            "20",
        )
        or "20"
    ).strip()

    try:
        value = float(raw)
    except ValueError:
        value = 20.0

    return max(1.0, min(value, 60.0))


def _config(
    source: Mapping[str, str],
    *,
    key_configured: bool,
) -> ProviderConfig:
    model = str(
        source.get("THESISOS_AI_BRIEF_MODEL")
        or GROQ_DEFAULT_MODEL
    ).strip()

    return ProviderConfig(
        enabled=True,
        provider=GROQ_PROVIDER_NAME,
        model=model,
        timeout_seconds=_timeout(source),
        api_key_configured=key_configured,
    )


def _safe_usage(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}

    result = {}

    for field in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ):
        raw = value.get(field)

        if isinstance(raw, int) and raw >= 0:
            result[field] = raw

    return result


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
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> TransportResponse:
        self.attempts += 1

        if self.actual_calls >= 1:
            raise LiveSmokeLimitError(
                "A second real Groq request was blocked."
            )

        try:
            request_payload = json.loads(
                body.decode("utf-8")
            )
        except Exception as error:
            raise LiveSmokeValidationError(
                "Groq request must be valid JSON."
            ) from error

        if not isinstance(request_payload, dict):
            raise LiveSmokeValidationError(
                "Groq request must be a JSON object."
            )

        if "tools" in request_payload:
            raise LiveSmokeValidationError(
                "Groq tools are not allowed."
            )

        if "store" in request_payload:
            raise LiveSmokeValidationError(
                "Groq Chat request must not send store."
            )

        response_format = request_payload.get(
            "response_format"
        )

        if not isinstance(response_format, Mapping):
            raise LiveSmokeValidationError(
                "Groq response_format is missing."
            )

        schema_config = response_format.get(
            "json_schema"
        )

        if not isinstance(schema_config, Mapping):
            raise LiveSmokeValidationError(
                "Groq json_schema is missing."
            )

        if (
            response_format.get("type") != "json_schema"
            or schema_config.get("strict") is not True
        ):
            raise LiveSmokeValidationError(
                "Groq strict Structured Outputs are required."
            )

        self.request_metadata = {
            "endpoint": url,
            "model": request_payload.get("model"),
            "store_parameter_sent": (
                "store" in request_payload
            ),
            "strict": schema_config.get("strict"),
            "format_type": response_format.get("type"),
            "max_completion_tokens": (
                request_payload.get(
                    "max_completion_tokens"
                )
            ),
            "tools_enabled": (
                "tools" in request_payload
            ),
            "request_bytes": len(body),
        }
        self.actual_calls += 1

        response = self.inner(
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )

        try:
            payload = json.loads(
                response.body.decode("utf-8")
            )
        except Exception:
            payload = {}

        if isinstance(payload, Mapping):
            response_id = payload.get("id")

            if isinstance(response_id, str):
                self.response_id = response_id

            self.usage = _safe_usage(
                payload.get("usage")
            )

        for name, value in response.headers.items():
            if str(name).lower() == "x-request-id":
                self.request_id = str(value)
                break

        return response


def dry_run_summary(
    environ: Mapping[str, str] | None = None,
) -> dict:
    source = dict(
        os.environ if environ is None else environ
    )
    source.setdefault(
        "THESISOS_AI_BRIEF_MODEL",
        GROQ_DEFAULT_MODEL,
    )
    bundle = load_grounding_fixture()
    config = _config(
        source,
        key_configured=bool(
            str(source.get(GROQ_API_KEY_ENV) or "").strip()
        ),
    )

    def no_network(*args, **kwargs):
        raise AssertionError(
            "Groq dry-run attempted network access."
        )

    provider = GroqChatCompletionsProvider(
        api_key="groq-dry-run-placeholder",
        model=config.model or GROQ_DEFAULT_MODEL,
        transport=no_network,
        sleep_fn=lambda _: None,
    )
    request = provider._request_payload(bundle)
    response_format = request.get("response_format", {})
    schema_config = response_format.get(
        "json_schema",
        {},
    )

    return {
        "mode": "groq_dry_run",
        "network_calls": 0,
        "secret_configured": bool(
            str(source.get(GROQ_API_KEY_ENV) or "").strip()
        ),
        "confirmation_present": (
            source.get(CONFIRM_ENV) == CONFIRM_VALUE
        ),
        "provider": GROQ_PROVIDER_NAME,
        "model": config.model,
        "endpoint": GROQ_CHAT_COMPLETIONS_URL,
        "grounding_status": bundle["coverage"]["status"],
        "grounding_hash_prefix": (
            bundle["payload_sha256"][:16]
        ),
        "strict_structured_outputs": (
            response_format.get("type")
            == "json_schema"
            and schema_config.get("strict") is True
        ),
        "tools_enabled": "tools" in request,
        "store_parameter_sent": "store" in request,
        "max_completion_tokens": request.get(
            "max_completion_tokens"
        ),
        "actual_request_limit": 1,
        "runtime_activated": False,
    }


def _base_summary(
    *,
    bundle: Mapping[str, Any],
    config: ProviderConfig,
    transport: SingleActualRequestTransport,
    elapsed_ms: int,
) -> dict:
    return {
        "mode": "groq_live",
        "provider": GROQ_PROVIDER_NAME,
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
        "runtime_activated": False,
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
        raise GroqSmokeGuardError(
            f"Set {CONFIRM_ENV}={CONFIRM_VALUE} "
            "for one Groq call."
        )

    api_key = str(
        source.get(GROQ_API_KEY_ENV) or ""
    ).strip()

    if not api_key:
        raise GroqSmokeGuardError(
            f"Replit Secret {GROQ_API_KEY_ENV} is missing."
        )

    model = str(
        source.get("THESISOS_AI_BRIEF_MODEL")
        or GROQ_DEFAULT_MODEL
    ).strip()

    if not model:
        raise GroqSmokeGuardError(
            "A Groq model is required."
        )

    source["THESISOS_AI_BRIEF_MODEL"] = model
    bundle = load_grounding_fixture()
    config = _config(source, key_configured=True)
    single_transport = SingleActualRequestTransport(
        transport
    )
    provider = build_groq_provider(
        config,
        environ=source,
        transport=single_transport,
        sleep_fn=lambda _: None,
    )

    if provider is None:
        raise GroqSmokeGuardError(
            "Groq provider could not be constructed."
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
        summary = _base_summary(
            bundle=bundle,
            config=config,
            transport=single_transport,
            elapsed_ms=elapsed_ms,
        )
        summary.update({
            "status": "exception",
            "schema_valid": False,
            "error_type": error.__class__.__name__,
            "error": _redact_error(error, api_key),
            "success": False,
        })
        return summary

    elapsed_ms = max(
        0,
        int((clock() - started) * 1000),
    )
    summary = _base_summary(
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

    decision = brief.get("decision")
    model_metadata = brief.get("model")
    error_metadata = brief.get("error")

    if not isinstance(decision, Mapping):
        decision = {}
    if not isinstance(model_metadata, Mapping):
        model_metadata = {}
    if not isinstance(error_metadata, Mapping):
        error_metadata = {}

    status = brief.get("status")
    request_metadata = (
        single_transport.request_metadata
    )
    success = bool(
        status in {"ready", "partial"}
        and schema_valid
        and single_transport.actual_calls == 1
        and request_metadata.get("endpoint")
        == GROQ_CHAT_COMPLETIONS_URL
        and request_metadata.get(
            "store_parameter_sent"
        )
        is False
        and request_metadata.get("strict") is True
        and request_metadata.get(
            "tools_enabled"
        )
        is False
    )

    summary.update({
        "status": status,
        "decision_action": decision.get("action"),
        "confidence": decision.get("confidence"),
        "model": (
            model_metadata.get("name")
            or config.model
        ),
        "provider": (
            model_metadata.get("provider")
            or GROQ_PROVIDER_NAME
        ),
        "response_id": (
            model_metadata.get("response_id")
            or single_transport.response_id
        ),
        "error_code": error_metadata.get("code"),
        "retryable": error_metadata.get("retryable"),
        "schema_valid": schema_valid,
        "validation_error": validation_error,
        "success": success,
    })

    return summary


def _print(payload: Mapping[str, Any]) -> None:
    print(json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ThesisOS Groq one-call smoke harness."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
    )
    mode.add_argument(
        "--live",
        action="store_true",
    )
    args = parser.parse_args(argv)

    if not args.live:
        _print(dry_run_summary())
        return 0

    try:
        summary = run_live()
    except GroqSmokeGuardError as error:
        _print({
            "mode": "groq_live",
            "network_calls": 0,
            "status": "blocked",
            "error_type": error.__class__.__name__,
            "error": str(error),
            "success": False,
            "runtime_activated": False,
        })
        return 2

    _print(summary)
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
