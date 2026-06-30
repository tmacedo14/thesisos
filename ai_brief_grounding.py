from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Mapping, Sequence


GROUNDING_BUNDLE_VERSION = "1.0"
MAX_DEPTH = 7
MAX_LIST_ITEMS = 40
MAX_STRING_CHARS = 4000

ASSET_TYPES = {
    "stock",
    "etf",
    "commodity",
    "fund",
    "bond",
    "crypto",
    "other",
}

SECTION_CANDIDATES = {
    "fundamentals": (
        "fundamentals",
        "stock_fundamentals",
        "etf_profile",
        "official_profile",
        "profile",
    ),
    "framework": (
        "framework_engine",
        "framework",
    ),
    "valuation": (
        "valuation",
        "valuation_snapshot",
    ),
    "technical": (
        "technical",
        "technical_snapshot",
    ),
    "evidence": (
        "evidence",
        "evidence_snapshot",
    ),
    "portfolio": (
        "portfolio_fit",
        "portfolio_context",
        "decision_gate",
        "portfolio",
    ),
    "market_context": (
        "market_context",
        "macro",
        "macro_context",
    ),
}

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "bearer_token",
    "cookie",
    "cookies",
    "headers",
    "password",
    "passwd",
    "prompt",
    "raw",
    "raw_prompt",
    "raw_response",
    "refresh_token",
    "request_headers",
    "response_body",
    "secret",
    "set_cookie",
    "system_prompt",
    "token",
    "access_token",
}

SENSITIVE_SUFFIXES = (
    "_api_key",
    "_password",
    "_secret",
    "_token",
)

EVIDENCE_EVENT_COLLECTIONS = (
    ("events", "evidence.events"),
    ("material_events", "evidence.material_events"),
)

COMPLETENESS_PATHS = (
    "data_completeness_pct",
    "data_completeness",
    "data_quality.completeness_percentage",
    "analysis_quality.completeness_percentage",
    "framework_engine.data_completeness_pct",
    "framework_engine.coverage_percentage",
)


class GroundingError(ValueError):
    pass


def _meaningful(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)

    return True


def _get_path(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload

    for component in path.split("."):
        if not isinstance(current, Mapping):
            return None

        if component not in current:
            return None

        current = current[component]

    return current


def _first_value(
    payload: Mapping[str, Any],
    paths: Sequence[str],
) -> Any:
    for path in paths:
        value = _get_path(payload, path)

        if _meaningful(value):
            return value

    return None


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")

    if normalized in SENSITIVE_KEYS:
        return True

    return any(
        normalized.endswith(suffix)
        for suffix in SENSITIVE_SUFFIXES
    )


AI_BRIEF_DECISION_ACTION_ALIASES = {
    "monitor": "watch",
}


def normalize_ai_brief_decision_action(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    normalized = value.strip().casefold()

    return AI_BRIEF_DECISION_ACTION_ALIASES.get(
        normalized,
        normalized,
    )


def _normalize_framework_decision_action(
    sections: dict[str, Any],
) -> None:
    framework = sections.get("framework")

    if not isinstance(framework, dict):
        return

    decision = framework.get("decision")

    if not isinstance(decision, dict):
        return

    action = normalize_ai_brief_decision_action(
        decision.get("action")
    )

    if isinstance(action, str) and action:
        decision["action"] = action

def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "[truncated:max_depth]"

    if value is None or isinstance(value, (bool, int)):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            return None

        return value

    if isinstance(value, str):
        return value[:MAX_STRING_CHARS]

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}

        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)

            if _sensitive_key(key):
                continue

            sanitized[key] = _sanitize_value(
                value[raw_key],
                depth + 1,
            )

        return sanitized

    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(item, depth + 1)
            for item in list(value)[:MAX_LIST_ITEMS]
        ]

    if isinstance(value, set):
        ordered = sorted(value, key=lambda item: repr(item))

        return [
            _sanitize_value(item, depth + 1)
            for item in ordered[:MAX_LIST_ITEMS]
        ]

    return str(value)[:MAX_STRING_CHARS]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _normalize_asset_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()

    aliases = {
        "equity": "stock",
        "share": "stock",
        "shares": "stock",
        "exchange_traded_fund": "etf",
        "exchange-traded fund": "etf",
        "etc": "commodity",
        "etn": "other",
    }

    normalized = aliases.get(normalized, normalized)

    return normalized if normalized in ASSET_TYPES else "other"


def _build_asset(payload: Mapping[str, Any]) -> tuple[dict, bool]:
    raw_type = _first_value(
        payload,
        (
            "asset.asset_type",
            "asset.type",
            "asset_type",
            "type",
        ),
    )

    asset = {
        "symbol": _first_value(
            payload,
            (
                "asset.symbol",
                "symbol",
                "ticker",
            ),
        ),
        "name": _first_value(
            payload,
            (
                "asset.name",
                "asset.description",
                "name",
                "description",
            ),
        ),
        "asset_type": _normalize_asset_type(raw_type),
        "isin": _first_value(
            payload,
            (
                "asset.isin",
                "isin",
            ),
        ),
        "exchange": _first_value(
            payload,
            (
                "asset.exchange",
                "asset.exchange_code",
                "exchange",
            ),
        ),
        "currency": _first_value(
            payload,
            (
                "asset.currency",
                "currency",
            ),
        ),
    }

    sanitized = _sanitize_value(asset)

    if sanitized["symbol"] is not None:
        sanitized["symbol"] = str(
            sanitized["symbol"]
        ).strip().upper() or None

    for key in ("name", "isin", "exchange", "currency"):
        if sanitized[key] is not None:
            sanitized[key] = str(sanitized[key]).strip() or None

    return sanitized, _meaningful(raw_type)


def _build_sections(payload: Mapping[str, Any]) -> dict:
    sections = {}

    for normalized_name, candidates in SECTION_CANDIDATES.items():
        value = _first_value(payload, candidates)

        if _meaningful(value):
            sections[normalized_name] = _sanitize_value(value)

    return sections


def _completeness(
    payload: Mapping[str, Any],
    sections: Mapping[str, Any],
) -> float:
    explicit = _first_value(payload, COMPLETENESS_PATHS)

    if isinstance(explicit, (int, float)) and math.isfinite(float(explicit)):
        return round(
            min(100.0, max(0.0, float(explicit))),
            2,
        )

    available = len(sections)
    total = len(SECTION_CANDIDATES)

    return round((available / total) * 100, 2)


def _event_source_type(event: Mapping[str, Any]) -> str:
    source_kind = str(
        event.get("source_kind")
        or event.get("source_type")
        or ""
    ).strip().lower()

    category = str(
        event.get("category")
        or event.get("form")
        or ""
    ).strip().lower()

    if (
        source_kind in {"official", "regulatory", "sec"}
        or category in {
            "10-k",
            "10-q",
            "8-k",
            "6-k",
            "20-f",
            "annual_results",
            "quarterly_results",
            "material_update",
        }
    ):
        return "official_filing"

    if source_kind in {
        "company",
        "company_release",
        "press_release",
    }:
        return "company_release"

    if source_kind in {"market", "market_data"}:
        return "market_data"

    if source_kind in {"technical", "technical_data"}:
        return "technical_data"

    if source_kind in {"portfolio", "portfolio_state"}:
        return "portfolio_state"

    return "other"


def _event_title(event: Mapping[str, Any]) -> str:
    value = (
        event.get("headline")
        or event.get("title")
        or event.get("form")
        or event.get("category")
        or "ThesisOS evidence event"
    )

    return str(value).strip()[:500]


def _event_as_of(event: Mapping[str, Any]) -> str | None:
    value = (
        event.get("event_timestamp")
        or event.get("event_date")
        or event.get("filed_at")
        or event.get("published_at")
        or event.get("as_of")
    )

    return str(value).strip() if _meaningful(value) else None


def _event_url(event: Mapping[str, Any]) -> str | None:
    value = (
        event.get("source_url")
        or event.get("url")
        or event.get("document_url")
        or event.get("link")
    )

    return str(value).strip() if _meaningful(value) else None


def _reference_id(
    source_type: str,
    title: str,
    as_of: str | None,
    field_path: str,
) -> str:
    digest = canonical_sha256(
        {
            "source_type": source_type,
            "title": title,
            "as_of": as_of,
            "field_path": field_path,
        }
    )

    return f"evidence-{digest[:16]}"


def _event_references(
    sections: Mapping[str, Any],
) -> list[dict]:
    evidence = sections.get("evidence")

    if not isinstance(evidence, Mapping):
        return []

    references = []

    for collection_name, base_path in EVIDENCE_EVENT_COLLECTIONS:
        events = evidence.get(collection_name)

        if not isinstance(events, list):
            continue

        for index, raw_event in enumerate(events[:MAX_LIST_ITEMS]):
            if not isinstance(raw_event, Mapping):
                continue

            event = _sanitize_value(raw_event)
            source_type = _event_source_type(event)
            title = _event_title(event)
            as_of = _event_as_of(event)
            field_path = f"{base_path}[{index}]"

            references.append(
                {
                    "id": _reference_id(
                        source_type,
                        title,
                        as_of,
                        field_path,
                    ),
                    "source_type": source_type,
                    "title": title,
                    "source_url": _event_url(event),
                    "as_of": as_of,
                    "field_paths": [field_path],
                }
            )

    return references


def _engine_references(
    sections: Mapping[str, Any],
) -> list[dict]:
    source_type_by_section = {
        "technical": "technical_data",
        "portfolio": "portfolio_state",
    }

    references = []

    for section_name in sorted(sections):
        value = sections[section_name]

        if not _meaningful(value):
            continue

        source_type = source_type_by_section.get(
            section_name,
            "thesisos_engine",
        )
        title = f"ThesisOS {section_name.replace('_', ' ')}"
        field_path = f"grounded_data.{section_name}"

        references.append(
            {
                "id": _reference_id(
                    source_type,
                    title,
                    None,
                    field_path,
                ),
                "source_type": source_type,
                "title": title,
                "source_url": None,
                "as_of": None,
                "field_paths": [field_path],
            }
        )

    return references


def _deduplicate_references(
    references: Sequence[Mapping[str, Any]],
) -> list[dict]:
    observed = set()
    result = []

    for reference in references:
        identifier = reference.get("id")

        if not identifier or identifier in observed:
            continue

        observed.add(identifier)
        result.append(dict(reference))

    return sorted(
        result,
        key=lambda item: (
            item["source_type"],
            item["id"],
        ),
    )


def _coverage(
    asset: Mapping[str, Any],
    asset_type_declared: bool,
    sections: Mapping[str, Any],
    completeness: float,
) -> dict:
    missing_critical = []

    if not _meaningful(asset.get("symbol")):
        missing_critical.append("asset.symbol")

    if not asset_type_declared:
        missing_critical.append("asset.asset_type")

    if "framework" not in sections:
        missing_critical.append("grounded_data.framework")

    if (
        asset.get("asset_type") in {"stock", "etf", "fund"}
        and "fundamentals" not in sections
    ):
        missing_critical.append("grounded_data.fundamentals")

    optional_fields = (
        "valuation",
        "technical",
        "evidence",
        "portfolio",
        "market_context",
    )

    missing_optional = [
        f"grounded_data.{name}"
        for name in optional_fields
        if name not in sections
    ]

    if missing_critical:
        status = "insufficient_data"
        uncertainty = "high"
    elif missing_optional:
        status = "partial"
        uncertainty = "medium"
    else:
        status = "ready"
        uncertainty = "low"

    return {
        "status": status,
        "data_completeness_pct": completeness,
        "uncertainty_level": uncertainty,
        "missing_critical_fields": sorted(missing_critical),
        "missing_optional_fields": sorted(missing_optional),
    }


def build_grounding_bundle(
    analysis_payload: Mapping[str, Any],
) -> dict:
    if not isinstance(analysis_payload, Mapping):
        raise GroundingError(
            "analysis_payload must be a mapping"
        )

    payload_copy = deepcopy(dict(analysis_payload))
    asset, asset_type_declared = _build_asset(payload_copy)
    sections = _build_sections(payload_copy)

    _normalize_framework_decision_action(sections)
    completeness = _completeness(payload_copy, sections)

    evidence_refs = _deduplicate_references(
        [
            *_engine_references(sections),
            *_event_references(sections),
        ]
    )

    source_types = sorted(
        {
            reference["source_type"]
            for reference in evidence_refs
        }
    )

    bundle = {
        "bundle_version": GROUNDING_BUNDLE_VERSION,
        "source": "thesisos",
        "asset": asset,
        "analysis_generated_at": _first_value(
            payload_copy,
            (
                "generated_at",
                "analysis_generated_at",
                "framework_engine.generated_at",
            ),
        ),
        "coverage": _coverage(
            asset,
            asset_type_declared,
            sections,
            completeness,
        ),
        "source_types": source_types,
        "evidence_refs": evidence_refs,
        "grounded_data": sections,
    }

    bundle = _sanitize_value(bundle)
    bundle["payload_sha256"] = canonical_sha256(bundle)

    return bundle


def verify_grounding_hash(bundle: Mapping[str, Any]) -> bool:
    if not isinstance(bundle, Mapping):
        return False

    expected = bundle.get("payload_sha256")

    if not isinstance(expected, str):
        return False

    unsigned = {
        key: deepcopy(value)
        for key, value in bundle.items()
        if key != "payload_sha256"
    }

    return canonical_sha256(unsigned) == expected


def generation_allowed(bundle: Mapping[str, Any]) -> bool:
    coverage = bundle.get("coverage")

    if not isinstance(coverage, Mapping):
        return False

    return coverage.get("status") in {"ready", "partial"}


def brief_grounding_metadata(
    bundle: Mapping[str, Any],
) -> dict:
    if not verify_grounding_hash(bundle):
        raise GroundingError(
            "grounding bundle hash is invalid"
        )

    coverage = bundle.get("coverage") or {}
    evidence_refs = bundle.get("evidence_refs") or []

    return {
        "source": "thesisos",
        "bundle_version": str(
            bundle.get("bundle_version")
            or GROUNDING_BUNDLE_VERSION
        ),
        "analysis_generated_at": bundle.get(
            "analysis_generated_at"
        ),
        "data_completeness_pct": coverage.get(
            "data_completeness_pct",
            0,
        ),
        "evidence_count": len(evidence_refs),
        "source_types": list(
            bundle.get("source_types") or []
        ),
        "evidence_refs": deepcopy(evidence_refs),
        "payload_sha256": bundle["payload_sha256"],
    }
