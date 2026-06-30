from __future__ import annotations

import os
import time
from typing import Callable, Mapping

from ai_brief_openai import (
    HttpTransport,
    OpenAIResponsesProvider,
    TransportResponse,
    urllib_transport,
)
from ai_brief_provider import (
    ProviderConfig,
    ProviderResult,
)

GROQ_PROVIDER_NAME = "groq"
GROQ_RESPONSES_URL = (
    "https://api.groq.com/openai/v1/responses"
)
GROQ_API_KEY_ENV = "THESISOS_AI_BRIEF_GROQ_API_KEY"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqResponsesProvider:
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

        def redirected_transport(
            *,
            url: str,
            headers: Mapping[str, str],
            body: bytes,
            timeout_seconds: float,
        ) -> TransportResponse:
            del url

            return self._transport(
                url=GROQ_RESPONSES_URL,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )

        self._delegate = OpenAIResponsesProvider(
            api_key=clean_key,
            model=clean_model,
            transport=redirected_transport,
            sleep_fn=sleep_fn,
        )

    def __repr__(self) -> str:
        return (
            "GroqResponsesProvider("
            f"model={self.model!r}, api_key='[REDACTED]')"
        )

    def _request_payload(
        self,
        grounding_bundle: Mapping,
    ) -> dict:
        return self._delegate._request_payload(
            grounding_bundle
        )

    def generate(
        self,
        grounding_bundle: Mapping,
        *,
        timeout_seconds: float,
    ) -> ProviderResult:
        result = self._delegate.generate(
            grounding_bundle,
            timeout_seconds=timeout_seconds,
        )

        return ProviderResult(
            content=result.content,
            provider=GROQ_PROVIDER_NAME,
            model=result.model,
            request_id=result.request_id,
        )


def build_groq_provider(
    config: ProviderConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transport: HttpTransport = urllib_transport,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GroqResponsesProvider | None:
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

    return GroqResponsesProvider(
        api_key=api_key,
        model=model,
        transport=transport,
        sleep_fn=sleep_fn,
    )
