from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Mapping

from ai_brief_openai import (
    HttpTransport,
    OpenAIResponsesProvider,
    TransportResponse,
    urllib_transport,
)
from ai_brief_provider import (
    ProviderConfig,
    ProviderContractError,
    ProviderGenerationError,
    ProviderResult,
    ProviderUnavailableError,
)

GROQ_PROVIDER_NAME = "groq"
GROQ_CHAT_COMPLETIONS_URL = (
    "https://api.groq.com/openai/v1/chat/completions"
)
GROQ_API_KEY_ENV = "THESISOS_AI_BRIEF_GROQ_API_KEY"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_MAX_COMPLETION_TOKENS = 3200

MAX_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = frozenset({
    408,
    409,
    422,
    429,
    500,
    502,
    503,
    504,
})


def _header_value(
    headers: Mapping[str, str],
    name: str,
) -> str | None:
    expected = name.lower()

    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value)

    return None


def _retry_delay(
    response: TransportResponse | None,
    attempt: int,
) -> float:
    if response is not None:
        raw = _header_value(
            response.headers,
            "retry-after",
        )

        if raw is not None:
            try:
                return max(0.0, min(float(raw), 5.0))
            except ValueError:
                pass

    return min(0.25 * (2 ** max(0, attempt - 1)), 2.0)


class GroqChatCompletionsProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: HttpTransport = urllib_transport,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        clean_key = str(api_key or "").strip()
        clean_model = str(model or "").strip()

        if not clean_key:
            raise ValueError("Groq API key is required.")

        if not clean_model:
            raise ValueError("Groq model is required.")

        self._api_key = clean_key
        self.model = clean_model
        self._transport = transport
        self._sleep = sleep_fn

        def no_network(*args, **kwargs):
            raise AssertionError(
                "The request builder must not access the network."
            )

        self._request_builder = OpenAIResponsesProvider(
            api_key="local-request-builder",
            model=clean_model,
            transport=no_network,
            sleep_fn=lambda _: None,
        )

    def __repr__(self) -> str:
        return (
            "GroqChatCompletionsProvider("
            f"model={self.model!r}, api_key='[REDACTED]')"
        )

    def _request_payload(
        self,
        grounding_bundle: Mapping[str, Any],
    ) -> dict:
        source = self._request_builder._request_payload(
            grounding_bundle
        )
        source_input = source.get("input")
        text_format = (
            source.get("text", {}).get("format", {})
        )

        if not isinstance(source_input, list):
            raise ProviderContractError(
                "provider request input must be a list"
            )

        if (
            text_format.get("type") != "json_schema"
            or text_format.get("strict") is not True
            or not isinstance(
                text_format.get("schema"),
                Mapping,
            )
        ):
            raise ProviderContractError(
                "strict provider schema is unavailable"
            )

        messages = []

        for item in source_input:
            if not isinstance(item, Mapping):
                raise ProviderContractError(
                    "provider request message is invalid"
                )

            role = item.get("role")
            content = item.get("content")

            if role == "developer":
                role = "system"

            if role not in {
                "system",
                "user",
                "assistant",
            }:
                raise ProviderContractError(
                    "provider request role is invalid"
                )

            if not isinstance(content, str) or not content:
                raise ProviderContractError(
                    "provider request content is invalid"
                )

            messages.append({
                "role": role,
                "content": content,
            })

        return {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": text_format.get("name"),
                    "strict": True,
                    "schema": text_format.get("schema"),
                },
            },
            "max_completion_tokens": (
                GROQ_MAX_COMPLETION_TOKENS
            ),
            "reasoning_effort": "low",
            "include_reasoning": False,
        }

    def _parse_response(
        self,
        response: TransportResponse,
    ) -> ProviderResult:
        try:
            payload = json.loads(
                response.body.decode("utf-8")
            )
        except Exception as error:
            raise ProviderContractError(
                "Groq returned invalid JSON."
            ) from error

        if not isinstance(payload, Mapping):
            raise ProviderContractError(
                "Groq returned an invalid response object."
            )

        choices = payload.get("choices")

        if not isinstance(choices, list) or not choices:
            raise ProviderContractError(
                "Groq response has no choices."
            )

        first = choices[0]

        if not isinstance(first, Mapping):
            raise ProviderContractError(
                "Groq response choice is invalid."
            )

        message = first.get("message")

        if not isinstance(message, Mapping):
            raise ProviderContractError(
                "Groq response message is invalid."
            )

        refusal = message.get("refusal")

        if isinstance(refusal, str) and refusal.strip():
            raise ProviderGenerationError(
                "Groq refused the generation."
            )

        content = message.get("content")

        if not isinstance(content, str) or not content.strip():
            raise ProviderContractError(
                "Groq response content is missing."
            )

        try:
            provider_content = json.loads(content)
        except Exception as error:
            raise ProviderContractError(
                "Groq returned invalid structured content."
            ) from error

        if not isinstance(provider_content, Mapping):
            raise ProviderContractError(
                "Groq structured content must be an object."
            )

        returned_model = payload.get("model")

        if not isinstance(returned_model, str) or not returned_model:
            returned_model = self.model

        response_id = payload.get("id")

        if not isinstance(response_id, str) or not response_id:
            response_id = _header_value(
                response.headers,
                "x-request-id",
            )

        return ProviderResult(
            content=dict(provider_content),
            provider=GROQ_PROVIDER_NAME,
            model=returned_model,
            request_id=response_id,
        )

    def generate(
        self,
        grounding_bundle: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> ProviderResult:
        request_payload = self._request_payload(
            grounding_bundle
        )
        request_body = json.dumps(
            request_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ThesisOS-AI-Brief/0.5",
        }

        last_response: TransportResponse | None = None
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._transport(
                    url=GROQ_CHAT_COMPLETIONS_URL,
                    headers=headers,
                    body=request_body,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as error:
                last_error = error

                if attempt < MAX_ATTEMPTS:
                    self._sleep(
                        _retry_delay(None, attempt)
                    )
                    continue

                raise ProviderUnavailableError(
                    "Groq Chat Completions API is unavailable."
                ) from error

            last_response = response

            if (
                response.status
                in RETRYABLE_HTTP_STATUSES
                and attempt < MAX_ATTEMPTS
            ):
                self._sleep(
                    _retry_delay(response, attempt)
                )
                continue

            if response.status >= 400:
                raise ProviderGenerationError(
                    "Groq returned an error."
                )

            return self._parse_response(response)

        if last_error is not None:
            raise ProviderUnavailableError(
                "Groq Chat Completions API is unavailable."
            ) from last_error

        if last_response is not None:
            raise ProviderGenerationError(
                "Groq returned an error."
            )

        raise ProviderUnavailableError(
            "Groq Chat Completions API is unavailable."
        )


GroqResponsesProvider = GroqChatCompletionsProvider


def build_groq_provider(
    config: ProviderConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transport: HttpTransport = urllib_transport,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GroqChatCompletionsProvider | None:
    source = os.environ if environ is None else environ
    provider_name = str(config.provider or "").strip().lower()

    if (
        not config.enabled
        or provider_name != GROQ_PROVIDER_NAME
    ):
        return None

    api_key = str(
        source.get(GROQ_API_KEY_ENV) or ""
    ).strip()
    model = str(config.model or "").strip()

    if not api_key or not model:
        return None

    return GroqChatCompletionsProvider(
        api_key=api_key,
        model=model,
        transport=transport,
        sleep_fn=sleep_fn,
    )
