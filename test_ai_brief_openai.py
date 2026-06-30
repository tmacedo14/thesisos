from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_brief_openai import (
    MAX_ATTEMPTS,
    MAX_GROUNDING_BYTES,
    OPENAI_RESPONSES_URL,
    OpenAIResponsesProvider,
    TransportResponse,
    build_openai_provider,
)
from ai_brief_provider import (
    ProviderConfig,
    ProviderGenerationError,
    ProviderUnavailableError,
)


ROOT = Path(__file__).resolve().parent
READY_BUNDLE = (
    ROOT
    / "contracts"
    / "examples"
    / "ai_investment_brief_grounding.ready.json"
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


def load_bundle() -> dict:
    return json.loads(
        READY_BUNDLE.read_text(encoding="utf-8")
    )


def valid_content() -> dict:
    return {
        "sections": {
            "executive_summary": (
                "The grounded evidence supports a watch decision."
            ),
            "thesis": [
                "The ThesisOS framework is constructive.",
            ],
            "strengths": [
                "Grounding completeness is high.",
            ],
            "risks": [
                "Valuation remains a constraint.",
            ],
            "valuation": "Entry discipline is required.",
            "technical": "Technical evidence is supportive.",
            "portfolio_fit": (
                "Respect the portfolio concentration limits."
            ),
            "catalysts": [
                "The next official filing.",
            ],
            "invalidation_signals": [
                "Material deterioration in cash conversion.",
            ],
            "next_review": "Review after the next filing.",
        },
        "decision": {
            "action": "watch",
            "confidence": 72,
            "rationale": "Evidence is constructive.",
            "position_sizing": "No full-size entry.",
            "entry_zone": "Use the ThesisOS valuation range.",
        },
        "limitations": [
            "No order execution.",
        ],
    }


def success_response() -> TransportResponse:
    payload = {
        "id": "resp_test_001",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(valid_content()),
                    }
                ],
            }
        ],
    }

    return TransportResponse(
        status=200,
        headers={"x-request-id": "request-header-001"},
        body=json.dumps(payload).encode("utf-8"),
    )


class SequenceTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def __call__(
        self,
        *,
        url,
        headers,
        body,
        timeout_seconds,
    ) -> TransportResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )

        if not self.responses:
            raise AssertionError("Unexpected transport call")

        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def error_response(
    status: int,
    message: str,
    *,
    retry_after: str | None = None,
) -> TransportResponse:
    headers = {}

    if retry_after is not None:
        headers["retry-after"] = retry_after

    return TransportResponse(
        status=status,
        headers=headers,
        body=json.dumps(
            {
                "error": {
                    "message": message,
                }
            }
        ).encode("utf-8"),
    )


def provider(
    transport,
    *,
    sleeps=None,
) -> OpenAIResponsesProvider:
    sleep_log = sleeps if sleeps is not None else []

    return OpenAIResponsesProvider(
        api_key="test-secret-key",
        model="test-model",
        transport=transport,
        sleep_fn=sleep_log.append,
    )


def configured() -> ProviderConfig:
    return ProviderConfig(
        enabled=True,
        provider="openai",
        model="test-model",
        timeout_seconds=15.0,
        api_key_configured=True,
    )


def main() -> int:
    bundle = load_bundle()
    transport = SequenceTransport([success_response()])
    selected = provider(transport)

    result = selected.generate(
        bundle,
        timeout_seconds=15.0,
    )

    check(
        result.content == valid_content(),
        "Structured output parsed",
    )

    check(
        result.provider == "openai",
        "Provider name normalized",
    )

    check(
        result.model == "test-model",
        "Model metadata preserved",
    )

    check(
        result.request_id == "resp_test_001",
        "Response id preserved",
    )

    check(
        len(transport.calls) == 1,
        "Successful request called once",
    )

    call = transport.calls[0]
    request_payload = json.loads(
        call["body"].decode("utf-8")
    )

    check(
        call["url"] == OPENAI_RESPONSES_URL,
        "Official Responses API endpoint",
    )

    check(
        call["headers"]["Authorization"]
        == "Bearer test-secret-key",
        "Bearer authentication header",
    )

    check(
        "test-secret-key"
        not in call["body"].decode("utf-8"),
        "API key excluded from request body",
    )

    check(
        request_payload["model"] == "test-model",
        "Configured model in request",
    )

    response_format = request_payload["text"]["format"]

    check(
        response_format["type"] == "json_schema"
        and response_format["strict"] is True,
        "Strict Structured Outputs enabled",
    )

    check(
        response_format["schema"][
            "additionalProperties"
        ]
        is False,
        "Provider output top-level closed",
    )

    check(
        "tools" not in request_payload,
        "No provider tools enabled",
    )

    grounding_text = request_payload["input"][1]["content"]

    check(
        bundle["payload_sha256"] in grounding_text,
        "Grounding hash sent to provider",
    )

    check(
        "Do not browse" in request_payload["input"][0]["content"],
        "System instruction forbids browsing",
    )

    check(
        "grounded_data.portfolio is absent"
        in request_payload["input"][0]["content"],
        "System instruction blocks ungrounded portfolio sizing",
    )

    check(
        "Never infer repurchases"
        in request_payload["input"][0]["content"],
        "System instruction blocks inferred corporate actions",
    )

    check(
        "[redacted]" in repr(selected)
        and "test-secret-key" not in repr(selected),
        "Provider repr redacts key",
    )

    sleeps = []
    retry_transport = SequenceTransport(
        [
            error_response(
                429,
                "rate limited",
                retry_after="0",
            ),
            success_response(),
        ]
    )

    retry_result = provider(
        retry_transport,
        sleeps=sleeps,
    ).generate(
        bundle,
        timeout_seconds=15.0,
    )

    check(
        retry_result.content == valid_content()
        and len(retry_transport.calls) == 2,
        "Transient 429 retried successfully",
    )

    check(
        sleeps == [0.0],
        "Retry-After respected",
    )

    persistent = SequenceTransport(
        [
            error_response(503, "temporary outage")
            for _ in range(MAX_ATTEMPTS)
        ]
    )

    unavailable = False

    try:
        provider(
            persistent,
            sleeps=[],
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderUnavailableError:
        unavailable = True

    check(
        unavailable
        and len(persistent.calls) == MAX_ATTEMPTS,
        "Persistent transient failure normalized",
    )

    auth_failed = False

    try:
        provider(
            SequenceTransport(
                [error_response(401, "invalid key")]
            )
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderGenerationError:
        auth_failed = True

    check(
        auth_failed,
        "Authentication error is not retried",
    )

    malformed = False

    try:
        provider(
            SequenceTransport(
                [
                    TransportResponse(
                        status=200,
                        headers={},
                        body=b"not-json",
                    )
                ]
            )
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderGenerationError:
        malformed = True

    check(
        malformed,
        "Malformed provider response rejected",
    )

    refusal_payload = {
        "id": "resp_refusal",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "refusal",
                        "refusal": "Request refused.",
                    }
                ],
            }
        ],
    }

    refusal = False

    try:
        provider(
            SequenceTransport(
                [
                    TransportResponse(
                        status=200,
                        headers={},
                        body=json.dumps(
                            refusal_payload
                        ).encode("utf-8"),
                    )
                ]
            )
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderGenerationError:
        refusal = True

    check(
        refusal,
        "Model refusal normalized",
    )

    incomplete = False

    try:
        provider(
            SequenceTransport(
                [
                    TransportResponse(
                        status=200,
                        headers={},
                        body=json.dumps(
                            {
                                "id": "resp_incomplete",
                                "status": "incomplete",
                                "output": [],
                            }
                        ).encode("utf-8"),
                    )
                ]
            )
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderGenerationError:
        incomplete = True

    check(
        incomplete,
        "Incomplete response rejected",
    )

    oversized_bundle = {
        **bundle,
        "oversized": "x" * (MAX_GROUNDING_BYTES + 1),
    }

    oversized = False

    try:
        provider(
            SequenceTransport([success_response()])
        ).generate(
            oversized_bundle,
            timeout_seconds=15.0,
        )
    except ProviderGenerationError:
        oversized = True

    check(
        oversized,
        "Oversized grounding rejected before transport",
    )

    factory_transport = SequenceTransport(
        [success_response()]
    )

    built = build_openai_provider(
        configured(),
        environ={
            "THESISOS_AI_BRIEF_API_KEY": "factory-key",
        },
        transport=factory_transport,
        sleep_fn=lambda _: None,
    )

    check(
        isinstance(built, OpenAIResponsesProvider),
        "OpenAI provider factory",
    )

    disabled = build_openai_provider(
        ProviderConfig(
            enabled=False,
            provider="openai",
            model="test-model",
            timeout_seconds=15.0,
            api_key_configured=True,
        ),
        environ={
            "THESISOS_AI_BRIEF_API_KEY": "factory-key",
        },
    )

    check(
        disabled is None,
        "Factory blocked while feature disabled",
    )

    unsupported = build_openai_provider(
        ProviderConfig(
            enabled=True,
            provider="other",
            model="test-model",
            timeout_seconds=15.0,
            api_key_configured=True,
        ),
        environ={
            "THESISOS_AI_BRIEF_API_KEY": "factory-key",
        },
    )

    check(
        unsupported is None,
        "Unsupported provider not constructed",
    )

    missing_key = build_openai_provider(
        configured(),
        environ={},
    )

    check(
        missing_key is None,
        "Missing key prevents construction",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
