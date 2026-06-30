from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

from ai_brief_grounding import (
    build_grounding_bundle,
    verify_grounding_hash,
)
from ai_brief_provider import (
    AiBriefProviderError,
    ProviderConfig,
    ProviderContractError,
    ProviderResult,
    ProviderUnavailableError,
    MISSING_PORTFOLIO_LIMITATION,
    NEXT_REVIEW_UNAVAILABLE,
    PORTFOLIO_FIT_UNAVAILABLE,
    POSITION_SIZING_UNAVAILABLE,
    UNSUPPORTED_CATALYST_LIMITATION,
    UNSUPPORTED_NEXT_REVIEW_LIMITATION,
    UNSUPPORTED_CLAIM_FALLBACK,
    UNSUPPORTED_NARRATIVE_CLAIM_LIMITATION,
    UNSUPPORTED_PORTFOLIO_LANGUAGE_LIMITATION,
    generate_ai_brief,
    provider_configuration,
)


ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "contracts" / "examples"
CONTRACT_SCHEMA = (
    ROOT / "contracts" / "ai_investment_brief.schema.json"
)

PASSED = 0
FAILED = 0


def check(condition: bool, label: str) -> None:
    global PASSED, FAILED

    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILED += 1
        print(f"[FAIL] {label}")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def analysis_payload(*, complete: bool = True) -> dict:
    payload = {
        "generated_at": "2026-06-29T16:00:00Z",
        "asset": {
            "symbol": "DEMO",
            "name": "Demonstration Asset",
            "asset_type": "stock",
            "exchange": "DEMO",
            "currency": "USD",
        },
        "data_completeness_pct": 93,
        "fundamentals": {
            "status": "ok",
            "revenue": 100,
        },
        "framework_engine": {
            "status": "ok",
            "score": 75,
        },
        "valuation": {
            "status": "partial",
        },
        "technical": {
            "status": "ok",
        },
        "evidence": {
            "status": "ok",
            "events": [
                {
                    "headline": "Next official results.",
                    "summary": "Next official filing.",
                    "event_date": "2026-07-31",
                    "source_kind": "official",
                },
            ],
        },
        "portfolio_fit": {
            "status": "ok",
        },
        "market_context": {
            "status": "ok",
        },
    }

    if not complete:
        payload.pop("market_context")

    return payload


def valid_content() -> dict:
    return {
        "sections": {
            "executive_summary": (
                "Grounded evidence supports a watch decision."
            ),
            "thesis": [
                "The ThesisOS framework is constructive.",
            ],
            "strengths": [
                "Grounding coverage is high.",
            ],
            "risks": [
                "Valuation remains a constraint.",
            ],
            "valuation": "Entry discipline is required.",
            "technical": "Technical evidence is supportive.",
            "portfolio_fit": (
                "Position sizing must respect concentration limits."
            ),
            "catalysts": [
                "Next official results.",
            ],
            "invalidation_signals": [
                "Material deterioration in cash conversion.",
            ],
            "next_review": "Review after the next official filing.",
        },
        "decision": {
            "action": "watch",
            "confidence": 72,
            "rationale": "Evidence is constructive but incomplete.",
            "position_sizing": "No full-size entry.",
            "entry_zone": "Use the ThesisOS valuation range.",
        },
        "limitations": [
            "No order execution.",
        ],
    }


class RecordingProvider:
    def __init__(
        self,
        *,
        content: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.content = content or valid_content()
        self.error = error
        self.calls = 0
        self.last_bundle = None
        self.last_timeout = None

    def generate(
        self,
        grounding_bundle,
        *,
        timeout_seconds,
    ) -> ProviderResult:
        self.calls += 1
        self.last_bundle = grounding_bundle
        self.last_timeout = timeout_seconds

        if self.error is not None:
            raise self.error

        return ProviderResult(
            content=deepcopy(self.content),
            provider="test-provider",
            model="test-model",
            request_id="request-001",
        )


def configured() -> ProviderConfig:
    return ProviderConfig(
        enabled=True,
        provider="test-provider",
        model="test-model",
        timeout_seconds=12.0,
        api_key_configured=True,
    )


def common_contract_checks(payload: dict) -> bool:
    schema = load_json(CONTRACT_SCHEMA)
    required = set(schema["required"])
    statuses = set(
        schema["properties"]["status"]["enum"]
    )
    actions = set(
        schema["$defs"]["decision"][
            "properties"
        ]["action"]["enum"]
    )

    return (
        required.issubset(payload)
        and payload["schema_version"] == "1.0"
        and payload["status"] in statuses
        and payload["decision"]["action"] in actions
        and 0 <= payload["decision"]["confidence"] <= 100
        and payload["grounding"]["source"] == "thesisos"
        and len(payload["grounding"]["payload_sha256"]) == 64
    )


def main() -> int:
    default_config = ProviderConfig.from_env({})

    check(
        default_config.enabled is False,
        "Feature flag disabled by default",
    )

    check(
        default_config.timeout_seconds == 20.0,
        "Default timeout",
    )

    enabled_config = ProviderConfig.from_env(
        {
            "THESISOS_AI_BRIEF_ENABLED": "true",
            "THESISOS_AI_BRIEF_PROVIDER": "example",
            "THESISOS_AI_BRIEF_MODEL": "example-model",
            "THESISOS_AI_BRIEF_API_KEY": "super-secret",
            "THESISOS_AI_BRIEF_TIMEOUT_SECONDS": "90",
        }
    )

    check(
        enabled_config.enabled is True,
        "Truthy feature flag parsing",
    )

    check(
        enabled_config.timeout_seconds == 60.0,
        "Timeout capped",
    )

    check(
        enabled_config.api_key_configured is True,
        "API key presence recorded",
    )

    check(
        "super-secret" not in repr(enabled_config),
        "API key value not stored in config",
    )

    public_config = provider_configuration(
        {
            "THESISOS_AI_BRIEF_ENABLED": "yes",
            "THESISOS_AI_BRIEF_PROVIDER": "example",
            "THESISOS_AI_BRIEF_MODEL": "example-model",
            "THESISOS_AI_BRIEF_API_KEY": "hidden-value",
        }
    )

    check(
        "hidden-value" not in json.dumps(public_config),
        "Public configuration excludes API key",
    )

    ready_bundle = build_grounding_bundle(
        analysis_payload()
    )

    check(
        ready_bundle["coverage"]["status"] == "ready",
        "Ready grounding fixture",
    )

    disabled_provider = RecordingProvider()
    disabled = generate_ai_brief(
        ready_bundle,
        ProviderConfig(
            enabled=False,
            provider=None,
            model=None,
            timeout_seconds=20.0,
            api_key_configured=False,
        ),
    )

    check(
        disabled["status"] == "disabled",
        "Disabled state",
    )

    check(
        disabled_provider.calls == 0,
        "Disabled state makes no provider call",
    )

    check(
        disabled["model"] is None
        and disabled["error"] is None,
        "Disabled model and error semantics",
    )

    check(
        common_contract_checks(disabled),
        "Disabled output matches common contract",
    )

    insufficient_payload = analysis_payload()
    insufficient_payload["asset"].pop("symbol")
    insufficient_bundle = build_grounding_bundle(
        insufficient_payload
    )
    insufficient_provider = RecordingProvider()

    insufficient = generate_ai_brief(
        insufficient_bundle,
        configured(),
        insufficient_provider,
    )

    check(
        insufficient["status"] == "insufficient_data",
        "Insufficient-data state",
    )

    check(
        insufficient_provider.calls == 0,
        "Insufficient data makes no provider call",
    )

    check(
        insufficient["decision"]["action"]
        == "unavailable",
        "Insufficient-data decision unavailable",
    )

    unavailable = generate_ai_brief(
        ready_bundle,
        ProviderConfig(
            enabled=True,
            provider="test-provider",
            model="test-model",
            timeout_seconds=12.0,
            api_key_configured=False,
        ),
        RecordingProvider(),
    )

    check(
        unavailable["status"] == "provider_unavailable",
        "Incomplete configuration state",
    )

    check(
        unavailable["error"]["code"]
        == "provider_not_configured",
        "Incomplete configuration error code",
    )

    unavailable_provider = RecordingProvider(
        error=ProviderUnavailableError(
            "temporary outage"
        )
    )

    temporary = generate_ai_brief(
        ready_bundle,
        configured(),
        unavailable_provider,
    )

    check(
        temporary["status"] == "provider_unavailable",
        "Provider unavailable exception normalized",
    )

    check(
        temporary["error"]["retryable"] is True,
        "Provider unavailable is retryable",
    )

    failing_provider = RecordingProvider(
        error=AiBriefProviderError("provider failure")
    )

    failed = generate_ai_brief(
        ready_bundle,
        configured(),
        failing_provider,
    )

    check(
        failed["status"] == "generation_failed",
        "Provider generation failure normalized",
    )

    check(
        failed["error"]["code"] == "generation_failed",
        "Generation failure error code",
    )

    success_provider = RecordingProvider()
    success = generate_ai_brief(
        ready_bundle,
        configured(),
        success_provider,
    )

    check(
        success["status"] == "ready",
        "Ready provider result",
    )

    check(
        success_provider.calls == 1,
        "Provider called exactly once",
    )

    check(
        success_provider.last_timeout == 12.0,
        "Configured timeout passed to provider",
    )

    check(
        success_provider.last_bundle is not ready_bundle,
        "Provider receives a copy of the bundle",
    )

    check(
        verify_grounding_hash(
            success_provider.last_bundle
        ),
        "Provider receives valid grounding",
    )

    check(
        success["grounding"]["payload_sha256"]
        == ready_bundle["payload_sha256"],
        "Final brief preserves grounding hash",
    )

    check(
        success["model"] == {
            "provider": "test-provider",
            "model": "test-model",
            "request_id": "request-001",
        },
        "Provider metadata normalized",
    )

    check(
        success["sections"]["executive_summary"]
        == valid_content()["sections"][
            "executive_summary"
        ],
        "Provider sections accepted",
    )

    check(
        success["decision"]["action"] == "watch",
        "Provider decision accepted",
    )

    check(
        success["sections"]["catalysts"]
        == valid_content()["sections"]["catalysts"],
        "Grounded catalyst retained",
    )

    check(
        success["sections"]["next_review"]
        == valid_content()["sections"]["next_review"],
        "Grounded next review retained",
    )

    check(
        common_contract_checks(success),
        "Ready output matches common contract",
    )

    partial_bundle = build_grounding_bundle(
        analysis_payload(complete=False)
    )

    partial = generate_ai_brief(
        partial_bundle,
        configured(),
        RecordingProvider(),
    )

    check(
        partial["status"] == "partial",
        "Partial grounding produces partial brief",
    )

    missing_context_payload = analysis_payload()
    missing_context_payload.pop("portfolio_fit")
    missing_context_payload.pop("market_context")
    missing_context_bundle = build_grounding_bundle(
        missing_context_payload
    )
    unsupported_content = valid_content()
    unsupported_content["sections"]["portfolio_fit"] = (
        "Use a defensive allocation with cautious sizing."
    )
    unsupported_content["sections"]["catalysts"] = [
        (
            "Potential share repurchase program or dividend "
            "increase."
        ),
        "Next official results.",
    ]
    unsupported_content["sections"]["next_review"] = (
        "Review after a dividend increase."
    )
    unsupported_content["decision"]["position_sizing"] = (
        "Allocate five percent."
    )

    unsupported_content["sections"]["risks"].append(
        (
            "The capital allocation event may signal an upcoming "
            "share-repurchase program or dividend increase."
        )
    )
    unsupported_content["decision"]["rationale"] = (
        "Valuation suggests avoiding new sizable positions "
        "until further data is available."
    )

    normalized = generate_ai_brief(
        missing_context_bundle,
        configured(),
        RecordingProvider(content=unsupported_content),
    )

    check(
        normalized["status"] == "partial",
        "Missing context remains a partial brief",
    )

    check(
        normalized["sections"]["portfolio_fit"]
        == PORTFOLIO_FIT_UNAVAILABLE,
        "Portfolio fit normalized when grounding is missing",
    )

    check(
        normalized["decision"]["position_sizing"]
        == POSITION_SIZING_UNAVAILABLE,
        "Position sizing normalized when grounding is missing",
    )

    check(
        normalized["sections"]["catalysts"]
        == ["Next official results."],
        "Unsupported catalyst removed and grounded catalyst kept",
    )

    check(
        normalized["sections"]["next_review"]
        == NEXT_REVIEW_UNAVAILABLE,
        "Unsupported next review normalized",
    )

    check(
        MISSING_PORTFOLIO_LIMITATION
        in normalized["limitations"],
        "Missing portfolio limitation exposed",
    )

    check(
        UNSUPPORTED_CATALYST_LIMITATION
        in normalized["limitations"],
        "Unsupported catalyst limitation exposed",
    )

    check(
        UNSUPPORTED_NEXT_REVIEW_LIMITATION
        in normalized["limitations"],
        "Unsupported next review limitation exposed",
    )

    check(
        not any(
            "repurchase" in risk.casefold()
            or "dividend increase" in risk.casefold()
            for risk in normalized["sections"]["risks"]
        ),
        "Unsupported corporate-action risk removed",
    )

    check(
        normalized["decision"]["rationale"]
        == UNSUPPORTED_CLAIM_FALLBACK,
        "Ungrounded portfolio language removed from rationale",
    )

    check(
        UNSUPPORTED_NARRATIVE_CLAIM_LIMITATION
        in normalized["limitations"],
        "Narrative corporate-action limitation exposed",
    )

    check(
        UNSUPPORTED_PORTFOLIO_LANGUAGE_LIMITATION
        in normalized["limitations"],
        "Narrative portfolio-language limitation exposed",
    )

    supported_action_payload = analysis_payload()
    supported_action_payload["evidence"]["events"].append(
        {
            "headline": "Board approves share repurchase program.",
            "summary": (
                "The board approved a share repurchase program."
            ),
            "event_date": "2026-07-01",
            "source_kind": "official",
        }
    )
    supported_action_bundle = build_grounding_bundle(
        supported_action_payload
    )
    supported_action_content = valid_content()
    supported_action_content["sections"]["risks"] = [
        "The board approved a share repurchase program.",
    ]

    supported_action = generate_ai_brief(
        supported_action_bundle,
        configured(),
        RecordingProvider(
            content=supported_action_content,
        ),
    )

    check(
        supported_action["sections"]["risks"]
        == ["The board approved a share repurchase program."],
        "Explicitly grounded corporate action retained",
    )

    invalid_content = valid_content()
    invalid_content["unexpected"] = "not allowed"

    invalid = generate_ai_brief(
        ready_bundle,
        configured(),
        RecordingProvider(content=invalid_content),
    )

    check(
        invalid["status"] == "generation_failed",
        "Unsupported provider field rejected",
    )

    check(
        invalid["error"]["code"]
        == "invalid_provider_response",
        "Invalid provider response error code",
    )

    invalid_action = valid_content()
    invalid_action["decision"]["action"] = "strong_buy"

    invalid_decision = generate_ai_brief(
        ready_bundle,
        configured(),
        RecordingProvider(content=invalid_action),
    )

    check(
        invalid_decision["status"]
        == "generation_failed",
        "Invalid provider decision rejected",
    )

    no_summary = valid_content()
    no_summary["sections"]["executive_summary"] = ""

    invalid_summary = generate_ai_brief(
        ready_bundle,
        configured(),
        RecordingProvider(content=no_summary),
    )

    check(
        invalid_summary["status"]
        == "generation_failed",
        "Missing executive summary rejected",
    )

    tampered = deepcopy(ready_bundle)
    tampered["asset"]["symbol"] = "TAMPERED"

    contract_error = False

    try:
        generate_ai_brief(
            tampered,
            configured(),
            RecordingProvider(),
        )
    except ProviderContractError:
        contract_error = True

    check(
        contract_error,
        "Invalid grounding hash rejected before provider",
    )

    disabled_example = load_json(
        EXAMPLES
        / "ai_investment_brief.provider_disabled.json"
    )
    unavailable_example = load_json(
        EXAMPLES
        / "ai_investment_brief.provider_unavailable.json"
    )

    check(
        disabled_example["status"] == "disabled",
        "Disabled example status",
    )

    check(
        unavailable_example["status"]
        == "provider_unavailable",
        "Provider-unavailable example status",
    )

    check(
        common_contract_checks(disabled_example),
        "Disabled example contract",
    )

    check(
        common_contract_checks(unavailable_example),
        "Provider-unavailable example contract",
    )

    check(
        "super-secret"
        not in json.dumps(success)
        and "hidden-value"
        not in json.dumps(success),
        "No configured secret in final brief",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
