#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_brief_groq import (
    GROQ_API_KEY_ENV,
    GROQ_DEFAULT_MODEL,
    GROQ_PROVIDER_NAME,
    GROQ_RESPONSES_URL,
    GroqResponsesProvider,
    build_groq_provider,
)
from ai_brief_openai import TransportResponse
from ai_brief_provider import (
    ProviderConfig,
    ProviderGenerationError,
)

ROOT = Path(__file__).resolve().parent
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


def grounding_bundle() -> dict:
    path = (
        ROOT
        / "contracts"
        / "examples"
        / "ai_investment_brief_grounding.ready.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def valid_content() -> dict:
    path = (
        ROOT
        / "contracts"
        / "examples"
        / "ai_investment_brief.ready.json"
    )
    example = json.loads(path.read_text(encoding="utf-8"))

    return {
        "sections": example["sections"],
        "decision": example["decision"],
        "limitations": example["limitations"],
    }


def success_response() -> TransportResponse:
    payload = {
        "id": "resp_groq_fake_001",
        "status": "completed",
        "model": GROQ_DEFAULT_MODEL,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            valid_content()
                        ),
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 600,
            "output_tokens": 300,
            "total_tokens": 900,
        },
    }

    return TransportResponse(
        status=200,
        headers={
            "x-request-id": "request_groq_fake_001",
        },
        body=json.dumps(payload).encode("utf-8"),
    )


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
        body=json.dumps({
            "error": {
                "message": message,
                "type": "test_error",
            }
        }).encode("utf-8"),
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
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout_seconds": timeout_seconds,
        })

        if not self.responses:
            raise AssertionError(
                "Unexpected transport call."
            )

        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def config(
    *,
    enabled: bool = True,
    provider: str = GROQ_PROVIDER_NAME,
    model: str = GROQ_DEFAULT_MODEL,
) -> ProviderConfig:
    return ProviderConfig(
        enabled=enabled,
        provider=provider,
        model=model,
        timeout_seconds=15.0,
        api_key_configured=True,
    )


def main() -> int:
    bundle = grounding_bundle()
    transport = SequenceTransport([success_response()])
    selected = GroqResponsesProvider(
        api_key="groq-test-secret",
        model=GROQ_DEFAULT_MODEL,
        transport=transport,
        sleep_fn=lambda _: None,
    )

    result = selected.generate(
        bundle,
        timeout_seconds=15.0,
    )

    check(
        result.content == valid_content(),
        "Structured output parsed",
    )
    check(
        result.provider == GROQ_PROVIDER_NAME,
        "Groq provider name normalized",
    )
    check(
        result.model == GROQ_DEFAULT_MODEL,
        "Groq model metadata preserved",
    )
    check(
        result.request_id == "resp_groq_fake_001",
        "Groq response id preserved",
    )
    check(
        len(transport.calls) == 1,
        "Successful request called once",
    )

    call = transport.calls[0]
    request = json.loads(
        call["body"].decode("utf-8")
    )

    check(
        call["url"] == GROQ_RESPONSES_URL,
        "Official Groq Responses endpoint",
    )
    check(
        call["headers"]["Authorization"]
        == "Bearer groq-test-secret",
        "Bearer authentication header",
    )
    check(
        "groq-test-secret"
        not in call["body"].decode("utf-8"),
        "Groq API key excluded from request body",
    )
    check(
        request["model"] == GROQ_DEFAULT_MODEL,
        "Configured Groq model in request",
    )
    check(
        request["text"]["format"]["type"]
        == "json_schema"
        and request["text"]["format"]["strict"]
        is True,
        "Strict Structured Outputs enabled",
    )
    check(
        request["text"]["format"]["schema"][
            "additionalProperties"
        ]
        is False,
        "Provider output top-level closed",
    )
    check(
        "tools" not in request,
        "No Groq tools enabled",
    )
    check(
        bundle["payload_sha256"]
        in request["input"][1]["content"],
        "Grounding hash sent to Groq",
    )
    check(
        "Do not browse" in request["input"][0]["content"],
        "System instruction forbids browsing",
    )
    check(
        "groq-test-secret" not in repr(selected),
        "Groq provider repr redacts key",
    )
    check(
        "OpenAIResponsesProvider" not in repr(selected),
        "Groq repr exposes no delegate",
    )

    sleeps = []
    retry_transport = SequenceTransport([
        error_response(
            429,
            "rate limited",
            retry_after="0",
        ),
        success_response(),
    ])
    retry_provider = GroqResponsesProvider(
        api_key="groq-test-secret",
        model=GROQ_DEFAULT_MODEL,
        transport=retry_transport,
        sleep_fn=sleeps.append,
    )
    retry_result = retry_provider.generate(
        bundle,
        timeout_seconds=15.0,
    )

    check(
        retry_result.content == valid_content()
        and len(retry_transport.calls) == 2,
        "Transient 429 retried successfully",
    )
    check(
        all(
            call["url"] == GROQ_RESPONSES_URL
            for call in retry_transport.calls
        ),
        "Retries remain on Groq endpoint",
    )
    check(
        sleeps == [0.0],
        "Retry-After respected",
    )

    auth_transport = SequenceTransport([
        error_response(401, "invalid key")
    ])
    auth_failed = False

    try:
        GroqResponsesProvider(
            api_key="groq-test-secret",
            model=GROQ_DEFAULT_MODEL,
            transport=auth_transport,
            sleep_fn=lambda _: None,
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderGenerationError:
        auth_failed = True

    check(
        auth_failed,
        "Authentication error normalized",
    )
    check(
        len(auth_transport.calls) == 1,
        "Authentication error is not retried",
    )

    factory_transport = SequenceTransport([
        success_response()
    ])
    built = build_groq_provider(
        config(),
        environ={
            GROQ_API_KEY_ENV: "factory-groq-key",
        },
        transport=factory_transport,
        sleep_fn=lambda _: None,
    )

    check(
        isinstance(built, GroqResponsesProvider),
        "Groq provider factory",
    )

    disabled = build_groq_provider(
        config(enabled=False),
        environ={
            GROQ_API_KEY_ENV: "factory-groq-key",
        },
    )
    check(
        disabled is None,
        "Factory blocked while feature disabled",
    )

    unsupported = build_groq_provider(
        config(provider="openai"),
        environ={
            GROQ_API_KEY_ENV: "factory-groq-key",
        },
    )
    check(
        unsupported is None,
        "Unsupported provider not constructed",
    )

    missing_key = build_groq_provider(
        config(),
        environ={},
    )
    check(
        missing_key is None,
        "Missing Groq key prevents construction",
    )

    missing_model = build_groq_provider(
        config(model=""),
        environ={
            GROQ_API_KEY_ENV: "factory-groq-key",
        },
    )
    check(
        missing_model is None,
        "Missing Groq model prevents construction",
    )

    source = (
        ROOT / "ai_brief_groq.py"
    ).read_text(encoding="utf-8")

    check(
        "api.groq.com/openai/v1/responses"
        in source,
        "Groq endpoint is explicit",
    )
    check(
        "THESISOS_AI_BRIEF_GROQ_API_KEY"
        in source,
        "Dedicated Groq secret name",
    )
    check(
        "THESISOS_AI_BRIEF_API_KEY"
        not in source,
        "Generic OpenAI secret not reused",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
