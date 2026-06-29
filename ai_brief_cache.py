from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ai_brief_grounding import (
    generation_allowed,
    verify_grounding_hash,
)
from ai_brief_provider import (
    AiBriefProvider,
    ProviderConfig,
    generate_ai_brief,
)


CACHE_ENABLED_ENV = "THESISOS_AI_BRIEF_CACHE_ENABLED"
CACHE_TTL_ENV = "THESISOS_AI_BRIEF_CACHE_TTL_SECONDS"
CACHE_MAX_ENTRIES_ENV = "THESISOS_AI_BRIEF_CACHE_MAX_ENTRIES"
LOG_EVENTS_ENV = "THESISOS_AI_BRIEF_LOG_EVENTS"

DEFAULT_CACHE_TTL_SECONDS = 900
MAX_CACHE_TTL_SECONDS = 86_400
DEFAULT_CACHE_MAX_ENTRIES = 128
MAX_CACHE_ENTRIES = 512
CACHE_NAMESPACE = "thesisos-ai-brief-cache-v1"
CACHEABLE_STATUSES = frozenset({"ready", "partial"})


@dataclass(frozen=True)
class AiBriefCacheConfig:
    enabled: bool
    ttl_seconds: int
    max_entries: int
    log_events: bool

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "AiBriefCacheConfig":
        source = os.environ if environ is None else environ

        return cls(
            enabled=_parse_bool(
                source.get(CACHE_ENABLED_ENV),
                default=True,
            ),
            ttl_seconds=_parse_bounded_int(
                source.get(CACHE_TTL_ENV),
                default=DEFAULT_CACHE_TTL_SECONDS,
                minimum=1,
                maximum=MAX_CACHE_TTL_SECONDS,
            ),
            max_entries=_parse_bounded_int(
                source.get(CACHE_MAX_ENTRIES_ENV),
                default=DEFAULT_CACHE_MAX_ENTRIES,
                minimum=1,
                maximum=MAX_CACHE_ENTRIES,
            ),
            log_events=_parse_bool(
                source.get(LOG_EVENTS_ENV),
                default=False,
            ),
        )


@dataclass
class _CacheEntry:
    value: dict
    expires_at: float


def _parse_bool(
    value: Any,
    *,
    default: bool,
) -> bool:
    if value is None or str(value).strip() == "":
        return default

    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _parse_bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None or str(value).strip() == "":
        return default

    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default

    return min(max(parsed, minimum), maximum)


def cache_configuration(
    environ: Mapping[str, str] | None = None,
) -> dict:
    config = AiBriefCacheConfig.from_env(environ)

    return {
        "enabled": config.enabled,
        "ttl_seconds": config.ttl_seconds,
        "max_entries": config.max_entries,
        "cacheable_statuses": sorted(CACHEABLE_STATUSES),
        "storage": "process_memory",
        "shared_across_instances": False,
        "log_events": config.log_events,
    }


def ai_brief_cache_key(
    grounding_bundle: Mapping[str, Any],
    provider_config: ProviderConfig,
) -> str:
    if not isinstance(grounding_bundle, Mapping):
        raise ValueError("grounding bundle must be a mapping")

    if not verify_grounding_hash(grounding_bundle):
        raise ValueError("grounding bundle hash is invalid")

    material = {
        "namespace": CACHE_NAMESPACE,
        "grounding_schema_version": grounding_bundle.get(
            "schema_version"
        ),
        "grounding_bundle_version": grounding_bundle.get(
            "grounding_bundle_version"
        ),
        "grounding_payload_sha256": grounding_bundle.get(
            "payload_sha256"
        ),
        "provider": provider_config.provider,
        "model": provider_config.model,
    }

    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


class AiBriefCache:
    def __init__(
        self,
        config: AiBriefCacheConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = (
            OrderedDict()
        )
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._evictions = 0
        self._expirations = 0

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> "AiBriefCache":
        return cls(
            AiBriefCacheConfig.from_env(environ),
            clock=clock,
        )

    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.expires_at <= now
        ]

        for key in expired:
            self._entries.pop(key, None)
            self._expirations += 1

    def get(self, key: str) -> dict | None:
        if not self.config.enabled:
            return None

        normalized = str(key or "").strip()

        if not normalized:
            return None

        with self._lock:
            now = self._clock()
            self._purge_expired_locked(now)
            entry = self._entries.get(normalized)

            if entry is None:
                self._misses += 1
                return None

            self._entries.move_to_end(normalized)
            self._hits += 1
            return deepcopy(entry.value)

    def put(
        self,
        key: str,
        value: Mapping[str, Any],
    ) -> bool:
        if not self.config.enabled:
            return False

        normalized = str(key or "").strip()

        if not normalized or not isinstance(value, Mapping):
            return False

        status = value.get("status")

        if status not in CACHEABLE_STATUSES:
            return False

        with self._lock:
            now = self._clock()
            self._purge_expired_locked(now)
            self._entries[normalized] = _CacheEntry(
                value=deepcopy(dict(value)),
                expires_at=(
                    now + self.config.ttl_seconds
                ),
            )
            self._entries.move_to_end(normalized)
            self._writes += 1

            while (
                len(self._entries)
                > self.config.max_entries
            ):
                self._entries.popitem(last=False)
                self._evictions += 1

        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            self._purge_expired_locked(
                self._clock()
            )

            return {
                "entries": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "writes": self._writes,
                "evictions": self._evictions,
                "expirations": self._expirations,
            }


def build_ai_brief_event(
    *,
    event: str,
    grounding_bundle: Mapping[str, Any],
    provider_config: ProviderConfig,
    cache_key: str | None,
    brief: Mapping[str, Any] | None = None,
    elapsed_ms: int | None = None,
) -> dict:
    grounding_hash = str(
        grounding_bundle.get("payload_sha256")
        or ""
    )
    status = (
        str(brief.get("status"))
        if isinstance(brief, Mapping)
        and brief.get("status") is not None
        else None
    )
    error_code = None

    if isinstance(brief, Mapping):
        error = brief.get("error")

        if isinstance(error, Mapping):
            value = error.get("code")
            error_code = (
                str(value)[:80]
                if value is not None
                else None
            )

    return {
        "component": "ai_investment_brief",
        "event": str(event)[:80],
        "status": status,
        "provider": (
            str(provider_config.provider)[:80]
            if provider_config.provider
            else None
        ),
        "model": (
            str(provider_config.model)[:120]
            if provider_config.model
            else None
        ),
        "grounding_hash_prefix": (
            grounding_hash[:16]
            if grounding_hash
            else None
        ),
        "cache_key_prefix": (
            str(cache_key)[:16]
            if cache_key
            else None
        ),
        "elapsed_ms": (
            max(0, int(elapsed_ms))
            if elapsed_ms is not None
            else None
        ),
        "error_code": error_code,
    }


def emit_ai_brief_event(
    event: Mapping[str, Any],
    *,
    enabled: bool,
    sink: Callable[[str], None] | None = None,
) -> None:
    if not enabled:
        return

    line = json.dumps(
        dict(event),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    if sink is not None:
        sink(line)
        return

    print(line, file=sys.stderr, flush=True)


def generate_ai_brief_with_cache(
    grounding_bundle: Mapping[str, Any],
    provider_config: ProviderConfig,
    provider: AiBriefProvider | None,
    *,
    cache: AiBriefCache,
    event_sink: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    use_cache = (
        cache.config.enabled
        and provider_config.enabled
        and provider is not None
        and generation_allowed(grounding_bundle)
    )
    key = None

    if use_cache:
        key = ai_brief_cache_key(
            grounding_bundle,
            provider_config,
        )
        cached = cache.get(key)

        if cached is not None:
            emit_ai_brief_event(
                build_ai_brief_event(
                    event="cache_hit",
                    grounding_bundle=grounding_bundle,
                    provider_config=provider_config,
                    cache_key=key,
                    brief=cached,
                    elapsed_ms=0,
                ),
                enabled=cache.config.log_events,
                sink=event_sink,
            )
            return cached

    started = clock()
    brief = generate_ai_brief(
        grounding_bundle,
        provider_config,
        provider=provider,
    )
    elapsed_ms = round(
        max(0.0, clock() - started) * 1000
    )

    cached_write = False

    if use_cache and key is not None:
        cached_write = cache.put(key, brief)

    emit_ai_brief_event(
        build_ai_brief_event(
            event=(
                "cache_write"
                if cached_write
                else "generated_not_cached"
            ),
            grounding_bundle=grounding_bundle,
            provider_config=provider_config,
            cache_key=key,
            brief=brief,
            elapsed_ms=elapsed_ms,
        ),
        enabled=cache.config.log_events,
        sink=event_sink,
    )

    return brief
