from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

from ai_brief_cache import (
    CACHEABLE_STATUSES,
    AiBriefCache,
    AiBriefCacheConfig,
    ai_brief_cache_key,
    build_ai_brief_event,
    cache_configuration,
    emit_ai_brief_event,
    generate_ai_brief_with_cache,
)
from ai_brief_provider import (
    ProviderConfig,
    ProviderResult,
)


ROOT = Path(__file__).resolve().parent
READY_BUNDLE_PATH = (
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


def ready_bundle() -> dict:
    return json.loads(
        READY_BUNDLE_PATH.read_text(
            encoding="utf-8"
        )
    )


def valid_content() -> dict:
    return {
        "sections": {
            "executive_summary": (
                "Grounded evidence supports a watch decision."
            ),
            "thesis": ["Constructive framework."],
            "strengths": ["High coverage."],
            "risks": ["Valuation risk."],
            "valuation": "Entry discipline required.",
            "technical": "Supportive.",
            "portfolio_fit": "Respect concentration.",
            "catalysts": ["Next filing."],
            "invalidation_signals": [
                "Cash conversion deterioration."
            ],
            "next_review": "After the next filing.",
        },
        "decision": {
            "action": "watch",
            "confidence": 70,
            "rationale": "Constructive but incomplete.",
            "position_sizing": "No full-size entry.",
            "entry_zone": "Use ThesisOS valuation.",
        },
        "limitations": ["No order execution."],
    }


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RecordingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self,
        grounding_bundle,
        *,
        timeout_seconds,
    ) -> ProviderResult:
        self.calls += 1

        return ProviderResult(
            content=valid_content(),
            provider="openai",
            model="test-model",
            request_id=f"request-{self.calls}",
        )


def configured() -> ProviderConfig:
    return ProviderConfig(
        enabled=True,
        provider="openai",
        model="test-model",
        timeout_seconds=20.0,
        api_key_configured=True,
    )


def cache_config(
    *,
    enabled: bool = True,
    ttl: int = 900,
    max_entries: int = 128,
    log_events: bool = False,
) -> AiBriefCacheConfig:
    return AiBriefCacheConfig(
        enabled=enabled,
        ttl_seconds=ttl,
        max_entries=max_entries,
        log_events=log_events,
    )


def main() -> int:
    defaults = AiBriefCacheConfig.from_env({})

    check(
        defaults.enabled is True,
        "Cache enabled by default",
    )

    check(
        defaults.ttl_seconds == 900,
        "Default cache TTL",
    )

    check(
        defaults.max_entries == 128,
        "Default cache capacity",
    )

    bounded = AiBriefCacheConfig.from_env(
        {
            "THESISOS_AI_BRIEF_CACHE_TTL_SECONDS": "999999",
            "THESISOS_AI_BRIEF_CACHE_MAX_ENTRIES": "999999",
            "THESISOS_AI_BRIEF_LOG_EVENTS": "true",
        }
    )

    check(
        bounded.ttl_seconds == 86400,
        "TTL capped",
    )

    check(
        bounded.max_entries == 512,
        "Capacity capped",
    )

    check(
        bounded.log_events is True,
        "Event logging configuration",
    )

    public = cache_configuration({})

    check(
        public["cacheable_statuses"]
        == sorted(CACHEABLE_STATUSES),
        "Public cacheable statuses",
    )

    check(
        public["storage"] == "process_memory"
        and public["shared_across_instances"] is False,
        "Public cache storage semantics",
    )

    bundle = ready_bundle()
    config = configured()
    key_one = ai_brief_cache_key(bundle, config)
    key_two = ai_brief_cache_key(
        deepcopy(bundle),
        config,
    )

    check(
        key_one == key_two and len(key_one) == 64,
        "Deterministic cache key",
    )

    different_model = ProviderConfig(
        enabled=True,
        provider="openai",
        model="different-model",
        timeout_seconds=20.0,
        api_key_configured=True,
    )

    check(
        ai_brief_cache_key(
            bundle,
            different_model,
        )
        != key_one,
        "Model included in cache key",
    )

    different_provider = ProviderConfig(
        enabled=True,
        provider="other",
        model="test-model",
        timeout_seconds=20.0,
        api_key_configured=True,
    )

    check(
        ai_brief_cache_key(
            bundle,
            different_provider,
        )
        != key_one,
        "Provider included in cache key",
    )

    clock = FakeClock()
    cache = AiBriefCache(
        cache_config(ttl=10, max_entries=2),
        clock=clock,
    )

    ready_value = {
        "status": "ready",
        "sections": {
            "executive_summary": "Cached.",
        },
    }

    check(
        cache.put("a", ready_value) is True,
        "Ready value cached",
    )

    returned = cache.get("a")

    check(
        returned == ready_value,
        "Cached value retrieved",
    )

    returned["sections"]["executive_summary"] = (
        "Mutated client copy."
    )

    check(
        cache.get("a")["sections"]["executive_summary"]
        == "Cached.",
        "Cache returns defensive copies",
    )

    ready_value["sections"]["executive_summary"] = (
        "Mutated source."
    )

    check(
        cache.get("a")["sections"]["executive_summary"]
        == "Cached.",
        "Cache stores defensive copies",
    )

    check(
        cache.put(
            "disabled",
            {"status": "disabled"},
        )
        is False,
        "Disabled state not cached",
    )

    check(
        cache.put(
            "failure",
            {"status": "generation_failed"},
        )
        is False,
        "Failure state not cached",
    )

    clock.advance(11)

    check(
        cache.get("a") is None,
        "Expired value removed",
    )

    stats = cache.stats()

    check(
        stats["expirations"] >= 1,
        "Expiration tracked",
    )

    cache.put("one", {"status": "ready"})
    cache.put("two", {"status": "partial"})
    cache.get("one")
    cache.put("three", {"status": "ready"})

    check(
        cache.get("two") is None
        and cache.get("one") is not None
        and cache.get("three") is not None,
        "LRU eviction",
    )

    check(
        cache.stats()["evictions"] == 1,
        "Eviction tracked",
    )

    disabled_cache = AiBriefCache(
        cache_config(enabled=False),
        clock=clock,
    )

    check(
        disabled_cache.put(
            "x",
            {"status": "ready"},
        )
        is False
        and disabled_cache.get("x") is None,
        "Disabled cache bypass",
    )

    service_clock = FakeClock()
    service_cache = AiBriefCache(
        cache_config(
            ttl=900,
            max_entries=10,
            log_events=True,
        ),
        clock=service_clock,
    )
    provider = RecordingProvider()
    events = []

    first = generate_ai_brief_with_cache(
        bundle,
        config,
        provider,
        cache=service_cache,
        event_sink=events.append,
        clock=service_clock,
    )

    second = generate_ai_brief_with_cache(
        bundle,
        config,
        provider,
        cache=service_cache,
        event_sink=events.append,
        clock=service_clock,
    )

    check(
        first["status"] == "ready"
        and second["status"] == "ready",
        "Service returns valid briefs",
    )

    check(
        provider.calls == 1,
        "Cache prevents duplicate provider call",
    )

    check(
        first is not second,
        "Cached service response is copied",
    )

    event_objects = [
        json.loads(line)
        for line in events
    ]

    check(
        [event["event"] for event in event_objects]
        == ["cache_write", "cache_hit"],
        "Cache events emitted",
    )

    serialized_events = json.dumps(
        event_objects
    )

    check(
        "api_key" not in serialized_events
        and "Authorization" not in serialized_events
        and "THESISOS_GROUNDING_BUNDLE"
        not in serialized_events,
        "Events exclude secrets and payloads",
    )

    check(
        bundle["payload_sha256"]
        not in serialized_events,
        "Events expose only hash prefixes",
    )

    check(
        bundle["payload_sha256"][:16]
        in serialized_events,
        "Grounding hash prefix observable",
    )

    disabled_provider = RecordingProvider()
    disabled_brief = generate_ai_brief_with_cache(
        bundle,
        ProviderConfig(
            enabled=False,
            provider="openai",
            model="test-model",
            timeout_seconds=20.0,
            api_key_configured=True,
        ),
        disabled_provider,
        cache=service_cache,
        clock=service_clock,
    )

    check(
        disabled_brief["status"] == "disabled"
        and disabled_provider.calls == 0,
        "Disabled feature bypasses provider and cache",
    )

    no_provider_brief = generate_ai_brief_with_cache(
        bundle,
        config,
        None,
        cache=service_cache,
        clock=service_clock,
    )

    check(
        no_provider_brief["status"]
        == "provider_unavailable",
        "Missing provider not cached",
    )

    check(
        service_cache.stats()["writes"] == 1,
        "Only successful generated brief written",
    )

    safe_event = build_ai_brief_event(
        event="manual",
        grounding_bundle=bundle,
        provider_config=config,
        cache_key=key_one,
        brief={
            "status": "generation_failed",
            "error": {
                "code": "invalid_provider_response",
                "message": "sensitive raw response",
            },
        },
        elapsed_ms=12,
    )

    check(
        safe_event["error_code"]
        == "invalid_provider_response"
        and "message" not in safe_event,
        "Event keeps code but drops error message",
    )

    sink = []
    emit_ai_brief_event(
        safe_event,
        enabled=False,
        sink=sink.append,
    )

    check(
        sink == [],
        "Disabled event logging emits nothing",
    )

    emit_ai_brief_event(
        safe_event,
        enabled=True,
        sink=sink.append,
    )

    check(
        len(sink) == 1
        and json.loads(sink[0])["event"] == "manual",
        "Enabled event logging emits JSON",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
