from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from ai_brief_provider import ProviderGenerationError


GROQ_PROJECTION_VERSION = "groq-compact-v1"
GROQ_PROJECTED_GROUNDING_MAX_BYTES = 11000
GROQ_MAX_REQUEST_BYTES = 16000


_PROFILES = (
    {
        "string_limit": 180,
        "list_limit": 5,
        "dict_limit": 12,
        "depth": 4,
        "refs": 10,
        "material_events": 4,
        "events": 2,
    },
    {
        "string_limit": 140,
        "list_limit": 4,
        "dict_limit": 8,
        "depth": 3,
        "refs": 8,
        "material_events": 3,
        "events": 1,
    },
    {
        "string_limit": 96,
        "list_limit": 3,
        "dict_limit": 6,
        "depth": 2,
        "refs": 5,
        "material_events": 2,
        "events": 0,
    },
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _compact_scalar(
    value: Any,
    *,
    string_limit: int,
) -> Any:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        normalized = " ".join(value.split())

        if len(normalized) <= string_limit:
            return normalized

        return normalized[: max(1, string_limit - 1)] + "…"

    return None


def _compact_value(
    value: Any,
    *,
    profile: Mapping[str, int],
    depth: int | None = None,
) -> Any:
    remaining = (
        int(profile["depth"])
        if depth is None
        else depth
    )

    scalar = _compact_scalar(
        value,
        string_limit=int(profile["string_limit"]),
    )

    if scalar is not None:
        return scalar

    if remaining <= 0:
        return None

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}

        for key, child in list(value.items())[
            : int(profile["dict_limit"])
        ]:
            compacted = _compact_value(
                child,
                profile=profile,
                depth=remaining - 1,
            )

            if compacted in (None, {}, []):
                continue

            result[str(key)] = compacted

        return result

    if isinstance(value, list):
        result = []

        for child in value[: int(profile["list_limit"])]:
            compacted = _compact_value(
                child,
                profile=profile,
                depth=remaining - 1,
            )

            if compacted in (None, {}, []):
                continue

            result.append(compacted)

        return result

    return None


def _selected(
    source: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    profile: Mapping[str, int],
    depth: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key in keys:
        if key not in source:
            continue

        compacted = _compact_value(
            source.get(key),
            profile=profile,
            depth=depth,
        )

        if compacted in (None, {}, []):
            continue

        result[key] = compacted

    return result


def _compact_event(
    event: Mapping[str, Any],
    *,
    profile: Mapping[str, int],
) -> dict[str, Any]:
    return _selected(
        event,
        (
            "id",
            "event_date",
            "event_timestamp",
            "form",
            "category",
            "category_label",
            "direction",
            "materiality",
            "materiality_score",
            "source_kind",
            "source_confidence",
            "headline",
            "summary",
            "why_it_matters",
        ),
        profile=profile,
        depth=2,
    )


def _event_identity(event: Mapping[str, Any]) -> str:
    for key in ("id", "accession_number", "headline"):
        value = event.get(key)

        if isinstance(value, str) and value.strip():
            return f"{key}:{value.strip()}"

    return json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )[:300]


def _compact_events(
    material_events: list[Any],
    events: list[Any],
    *,
    profile: Mapping[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    compact_material: list[dict[str, Any]] = []
    compact_other: list[dict[str, Any]] = []
    seen: set[str] = set()

    for value in material_events[
        : int(profile["material_events"])
    ]:
        if not isinstance(value, Mapping):
            continue

        identity = _event_identity(value)

        if identity in seen:
            continue

        compacted = _compact_event(
            value,
            profile=profile,
        )

        if compacted:
            seen.add(identity)
            compact_material.append(compacted)

    for value in events:
        if len(compact_other) >= int(profile["events"]):
            break

        if not isinstance(value, Mapping):
            continue

        identity = _event_identity(value)

        if identity in seen:
            continue

        compacted = _compact_event(
            value,
            profile=profile,
        )

        if compacted:
            seen.add(identity)
            compact_other.append(compacted)

    return compact_material, compact_other


def _compact_reference(
    reference: Mapping[str, Any],
    *,
    profile: Mapping[str, int],
) -> dict[str, Any]:
    compacted = _selected(
        reference,
        (
            "id",
            "source_type",
            "as_of",
            "title",
            "field_paths",
        ),
        profile=profile,
        depth=2,
    )

    field_paths = compacted.get("field_paths")

    if isinstance(field_paths, list):
        compacted["field_paths"] = field_paths[:3]

    return compacted


def _build_projection(
    bundle: Mapping[str, Any],
    *,
    profile: Mapping[str, int],
) -> dict[str, Any]:
    grounded = _mapping(bundle.get("grounded_data"))
    framework = _mapping(grounded.get("framework"))
    fundamentals = _mapping(grounded.get("fundamentals"))
    technical = _mapping(grounded.get("technical"))
    valuation = _mapping(grounded.get("valuation"))
    evidence = _mapping(grounded.get("evidence"))

    source_events = _list(evidence.get("events"))
    source_material = _list(
        evidence.get("material_events")
    )
    source_refs = _list(bundle.get("evidence_refs"))

    material_events, other_events = _compact_events(
        source_material,
        source_events,
        profile=profile,
    )

    compact_refs = []

    for value in source_refs[: int(profile["refs"])]:
        if not isinstance(value, Mapping):
            continue

        compacted = _compact_reference(
            value,
            profile=profile,
        )

        if compacted:
            compact_refs.append(compacted)

    projected_fundamentals = _selected(
        fundamentals,
        (
            "entity_name",
            "name",
            "ticker",
            "latest_period",
            "updated_at",
            "source",
            "instant_metrics",
            "derived_metrics",
            "duration_metrics",
            "annual_cash_flow_reference",
            "cash_flow_and_allocation",
            "debt_components",
        ),
        profile=profile,
    )

    projected_technical = _selected(
        technical,
        (
            "as_of",
            "classification",
            "coverage_percentage",
            "history_sessions",
            "indicators",
            "positive_signals",
            "warning_signals",
            "rules",
            "score",
            "methodology_note",
        ),
        profile=profile,
    )

    projected_valuation = _selected(
        valuation,
        (
            "status",
            "classification",
            "coverage_percentage",
            "currency",
            "metrics",
            "reverse_dcf",
            "positive_signals",
            "warning_signals",
            "rules",
            "score",
            "achieved_points",
            "available_max_points",
            "methodology_note",
        ),
        profile=profile,
    )

    projected_framework = _selected(
        framework,
        (
            "version",
            "status",
            "scope",
            "asset_type",
            "data_quality",
            "decision",
            "frameworks_applied",
            "next_required_data",
            "framework_checklist",
            "quantitative_snapshot",
        ),
        profile=profile,
    )

    projected_evidence = _selected(
        evidence,
        (
            "status",
            "coverage_percentage",
            "filings_status",
            "news_status",
            "official_filings_count",
            "news_count",
            "material_events_count",
            "review_required",
            "review_reason",
            "latest_event",
            "latest_official_filing",
            "source_hierarchy",
            "thesis_implications",
            "methodology_note",
            "errors",
        ),
        profile=profile,
    )

    if material_events:
        projected_evidence["material_events"] = (
            material_events
        )

    if other_events:
        projected_evidence["events"] = other_events

    projected_grounded: dict[str, Any] = {}

    for key, value in (
        ("fundamentals", projected_fundamentals),
        ("technical", projected_technical),
        ("valuation", projected_valuation),
        ("framework", projected_framework),
        ("evidence", projected_evidence),
    ):
        if value:
            projected_grounded[key] = value

    projection = {
        "bundle_version": bundle.get("bundle_version"),
        "source": bundle.get("source"),
        "analysis_generated_at": bundle.get(
            "analysis_generated_at"
        ),
        "asset": _compact_value(
            bundle.get("asset"),
            profile=profile,
        ),
        "coverage": _compact_value(
            bundle.get("coverage"),
            profile=profile,
        ),
        "source_types": _compact_value(
            bundle.get("source_types"),
            profile=profile,
        ),
        "payload_sha256": bundle.get("payload_sha256"),
        "transport_projection": {
            "version": GROQ_PROJECTION_VERSION,
            "canonical_payload_sha256": bundle.get(
                "payload_sha256"
            ),
            "canonical_bundle_bytes": len(
                _canonical_bytes(bundle)
            ),
            "policy": (
                "Deterministic transport subset of the "
                "canonical ThesisOS grounding. Do not infer "
                "values that are not present."
            ),
            "duplicates_removed": [
                "framework.evidence_snapshot",
                "framework.technical_snapshot",
                "framework.valuation_snapshot",
            ],
            "retained_counts": {
                "material_events": len(material_events),
                "events": len(other_events),
                "evidence_refs": len(compact_refs),
            },
            "omitted_counts": {
                "material_events": max(
                    0,
                    len(source_material)
                    - len(material_events),
                ),
                "events": max(
                    0,
                    len(source_events)
                    - len(other_events),
                ),
                "evidence_refs": max(
                    0,
                    len(source_refs)
                    - len(compact_refs),
                ),
            },
        },
        "grounded_data": projected_grounded,
        "evidence_refs": compact_refs,
    }

    return {
        key: value
        for key, value in projection.items()
        if value not in (None, {}, [])
    }


def _minimal_projection(
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    profile = {
        "string_limit": 80,
        "list_limit": 2,
        "dict_limit": 5,
        "depth": 2,
        "refs": 4,
        "material_events": 1,
        "events": 0,
    }

    return _build_projection(
        bundle,
        profile=profile,
    )


def project_grounding_for_groq(
    grounding_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(grounding_bundle, Mapping):
        raise ProviderGenerationError(
            "Groq grounding must be a mapping."
        )

    original = deepcopy(dict(grounding_bundle))

    for profile in _PROFILES:
        projection = _build_projection(
            original,
            profile=profile,
        )

        if (
            len(_canonical_bytes(projection))
            <= GROQ_PROJECTED_GROUNDING_MAX_BYTES
        ):
            return projection

    projection = _minimal_projection(original)

    if (
        len(_canonical_bytes(projection))
        <= GROQ_PROJECTED_GROUNDING_MAX_BYTES
    ):
        return projection

    raise ProviderGenerationError(
        "Groq grounding projection exceeds the "
        "configured input budget."
    )
