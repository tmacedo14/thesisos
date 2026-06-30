#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_brief_groq import (
    GROQ_API_KEY_ENV,
    GROQ_CHAT_COMPLETIONS_URL,
    GROQ_DEFAULT_MODEL,
    GROQ_MAX_COMPLETION_TOKENS,
    GROQ_PROVIDER_NAME,
    RETRYABLE_HTTP_STATUSES,
    GroqChatCompletionsProvider,
    GroqResponsesProvider,
    build_groq_provider,
)
from ai_brief_openai import TransportResponse
from ai_brief_provider import (
    ProviderConfig,
    ProviderContractError,
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
    return json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "ai_investment_brief_grounding.ready.json"
        ).read_text(encoding="utf-8")
    )


def valid_content() -> dict:
    example = json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "ai_investment_brief.ready.json"
        ).read_text(encoding="utf-8")
    )

    return {
        "sections": example["sections"],
        "decision": example["decision"],
        "limitations": example["limitations"],
    }


def success_response() -> TransportResponse:
    payload = {
        "id": "chatcmpl_groq_fake_001",
        "model": GROQ_DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        valid_content()
                    ),
                },
            }
        ],
        "usage": {
            "prompt_tokens": 600,
            "completion_tokens": 300,
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
    provider = GroqChatCompletionsProvider(
        api_key="groq-test-secret",
        model=GROQ_DEFAULT_MODEL,
        transport=transport,
        sleep_fn=lambda _: None,
    )

    result = provider.generate(
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
        result.request_id == "chatcmpl_groq_fake_001",
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
    response_format = request["response_format"]
    schema_config = response_format["json_schema"]

    check(
        call["url"] == GROQ_CHAT_COMPLETIONS_URL,
        "Official Groq Chat Completions endpoint",
    )
    check(
        call["headers"]["Authorization"]
        == "Bearer groq-test-secret",
        "Bearer authentication header",
    )
    check(
        call["headers"]["User-Agent"]
        == "ThesisOS-AI-Brief/0.5",
        "Stable ThesisOS User-Agent header",
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
        request["max_completion_tokens"]
        == GROQ_MAX_COMPLETION_TOKENS,
        "Groq output budget increased",
    )
    check(
        request["reasoning_effort"] == "low",
        "Groq reasoning effort reduced",
    )
    check(
        request["include_reasoning"] is False,
        "Groq reasoning excluded from response",
    )
    check(
        422 in RETRYABLE_HTTP_STATUSES,
        "Groq semantic generation errors retryable",
    )
    check(
        response_format["type"] == "json_schema"
        and schema_config["strict"] is True,
        "Strict Structured Outputs enabled",
    )
    check(
        schema_config["schema"][
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
        "store" not in request,
        "No storage parameter sent",
    )
    check(
        bundle["payload_sha256"]
        in request["messages"][1]["content"],
        "Grounding hash sent to Groq",
    )
    check(
        "Do not browse"
        in request["messages"][0]["content"],
        "System instruction forbids browsing",
    )

    check(
        "grounded_data.portfolio is absent"
        in request["messages"][0]["content"],
        "System instruction blocks ungrounded portfolio sizing",
    )

    check(
        "Never infer repurchases"
        in request["messages"][0]["content"],
        "System instruction blocks inferred corporate actions",
    )
    check(
        "groq-test-secret" not in repr(provider),
        "Groq provider repr redacts key",
    )
    check(
        GroqResponsesProvider
        is GroqChatCompletionsProvider,
        "Legacy Groq class alias preserved",
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
    retry_provider = GroqChatCompletionsProvider(
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
            call["url"]
            == GROQ_CHAT_COMPLETIONS_URL
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
        GroqChatCompletionsProvider(
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

    malformed = TransportResponse(
        status=200,
        headers={},
        body=json.dumps({
            "id": "chatcmpl_bad",
            "model": GROQ_DEFAULT_MODEL,
            "choices": [{
                "message": {
                    "content": "not-json",
                }
            }],
        }).encode("utf-8"),
    )
    malformed_failed = False

    try:
        GroqChatCompletionsProvider(
            api_key="groq-test-secret",
            model=GROQ_DEFAULT_MODEL,
            transport=SequenceTransport([malformed]),
            sleep_fn=lambda _: None,
        ).generate(
            bundle,
            timeout_seconds=15.0,
        )
    except ProviderContractError:
        malformed_failed = True

    check(
        malformed_failed,
        "Malformed structured content rejected",
    )

    built = build_groq_provider(
        config(),
        environ={
            GROQ_API_KEY_ENV: "factory-groq-key",
        },
        transport=SequenceTransport([
            success_response()
        ]),
        sleep_fn=lambda _: None,
    )

    check(
        isinstance(
            built,
            GroqChatCompletionsProvider,
        ),
        "Groq provider factory",
    )
    check(
        build_groq_provider(
            config(enabled=False),
            environ={
                GROQ_API_KEY_ENV: "factory-groq-key",
            },
        )
        is None,
        "Factory blocked while feature disabled",
    )
    check(
        build_groq_provider(
            config(provider="openai"),
            environ={
                GROQ_API_KEY_ENV: "factory-groq-key",
            },
        )
        is None,
        "Unsupported provider not constructed",
    )
    check(
        build_groq_provider(
            config(),
            environ={},
        )
        is None,
        "Missing Groq key prevents construction",
    )
    check(
        build_groq_provider(
            config(model=""),
            environ={
                GROQ_API_KEY_ENV: "factory-groq-key",
            },
        )
        is None,
        "Missing Groq model prevents construction",
    )

    source = (
        ROOT / "ai_brief_groq.py"
    ).read_text(encoding="utf-8")

    check(
        "api.groq.com/openai/v1/chat/completions"
        in source,
        "Groq Chat endpoint is explicit",
    )
    check(
        "api.groq.com/openai/v1/responses"
        not in source,
        "Groq Responses endpoint removed",
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
