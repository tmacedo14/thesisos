from __future__ import annotations

import json
from copy import deepcopy

from ai_brief_groq import (
    GROQ_CHAT_COMPLETIONS_URL,
    GroqChatCompletionsProvider,
)
from ai_brief_groq_projection import (
    GROQ_MAX_REQUEST_BYTES,
    GROQ_PROJECTED_GROUNDING_MAX_BYTES,
    GROQ_PROJECTION_VERSION,
    project_grounding_for_groq,
)


passed = 0
failed = 0


def check(condition: bool, label: str) -> None:
    global passed, failed

    if condition:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label}")


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def oversized_bundle() -> dict:
    events = []

    for index in range(50):
        events.append({
            "id": f"event-{index}",
            "event_date": "2026-06-30",
            "event_timestamp": (
                "2026-06-30T12:00:00+00:00"
            ),
            "form": "8-K",
            "category": "operations",
            "category_label": "Operations",
            "direction": "mixed",
            "materiality": "high",
            "materiality_score": 90 - index,
            "source_kind": "official_filing",
            "source_confidence": "high",
            "headline": "H" * 800,
            "summary": "S" * 1800,
            "why_it_matters": "W" * 1400,
            "url": "https://example.com/" + "u" * 500,
        })

    evidence = {
        "status": "ready",
        "coverage_percentage": 92,
        "filings_status": "ready",
        "news_status": "ready",
        "official_filings_count": 12,
        "news_count": 30,
        "material_events_count": 12,
        "review_required": True,
        "review_reason": "R" * 900,
        "latest_event": events[0],
        "latest_official_filing": events[1],
        "source_hierarchy": ["official", "engine"],
        "thesis_implications": ["I" * 900] * 20,
        "methodology_note": "M" * 1600,
        "errors": [],
        "events": events,
        "material_events": events[:12],
    }

    technical = {
        "classification": "positive",
        "coverage_percentage": 90,
        "indicators": {
            f"indicator_{index}": {
                "value": index,
                "note": "N" * 900,
            }
            for index in range(30)
        },
        "positive_signals": ["P" * 1000] * 20,
        "warning_signals": ["W" * 1000] * 20,
        "rules": [
            {
                "id": f"technical-{index}",
                "description": "D" * 1000,
            }
            for index in range(20)
        ],
        "score": 74,
    }

    valuation = {
        "status": "ready",
        "classification": "fair",
        "coverage_percentage": 88,
        "currency": "USD",
        "metrics": {
            f"metric_{index}": {
                "value": index,
                "note": "V" * 1000,
            }
            for index in range(30)
        },
        "reverse_dcf": {
            "status": "ready",
            "details": "D" * 6000,
        },
        "positive_signals": ["P" * 1000] * 20,
        "warning_signals": ["W" * 1000] * 20,
        "rules": [
            {
                "id": f"valuation-{index}",
                "description": "D" * 1000,
            }
            for index in range(20)
        ],
        "score": 61,
    }

    fundamentals = {
        "entity_name": "Example",
        "ticker": "TEST",
        "latest_period": "2026-Q2",
        "instant_metrics": {
            f"instant_{index}": {
                "value": index,
                "note": "I" * 1000,
            }
            for index in range(40)
        },
        "derived_metrics": {
            f"derived_{index}": {
                "value": index,
                "note": "D" * 1000,
            }
            for index in range(40)
        },
        "duration_metrics": {
            f"duration_{index}": {
                "values": ["X" * 1000] * 20,
            }
            for index in range(20)
        },
    }

    framework = {
        "version": "0.9",
        "status": "ready",
        "decision": {
            "action": "watch",
            "rationale": "R" * 5000,
        },
        "data_quality": {
            "status": "partial",
            "notes": ["N" * 1000] * 20,
        },
        "frameworks_applied": ["quality", "valuation"],
        "next_required_data": ["N" * 1000] * 20,
        "framework_checklist": {
            "items": [
                {
                    "id": f"check-{index}",
                    "description": "C" * 1000,
                }
                for index in range(30)
            ]
        },
        "quantitative_snapshot": {
            "rules": [
                {
                    "id": f"rule-{index}",
                    "description": "Q" * 1000,
                }
                for index in range(30)
            ]
        },
        "evidence_snapshot": evidence,
        "technical_snapshot": technical,
        "valuation_snapshot": valuation,
    }

    refs = [
        {
            "id": f"ref-{index}",
            "source_type": "official_filing",
            "as_of": "2026-06-30",
            "title": "T" * 1000,
            "source_url": (
                "https://example.com/" + "u" * 800
            ),
            "field_paths": [
                f"grounded_data.path.{item}"
                for item in range(20)
            ],
        }
        for index in range(60)
    ]

    return {
        "bundle_version": "1.0",
        "source": "thesisos",
        "analysis_generated_at": (
            "2026-06-30T12:00:00+00:00"
        ),
        "asset": {
            "symbol": "TEST",
            "name": "Example",
            "asset_type": "equity",
            "currency": "USD",
        },
        "coverage": {
            "status": "partial",
            "uncertainty_level": "medium",
            "missing_fields": [
                "grounded_data.market_context",
                "grounded_data.portfolio",
            ],
        },
        "source_types": [
            "official_filing",
            "thesisos_engine",
        ],
        "payload_sha256": "a" * 64,
        "grounded_data": {
            "fundamentals": fundamentals,
            "technical": technical,
            "valuation": valuation,
            "framework": framework,
            "evidence": evidence,
        },
        "evidence_refs": refs,
    }


bundle = oversized_bundle()
before = deepcopy(bundle)
full_size = len(canonical_bytes(bundle))
projection = project_grounding_for_groq(bundle)
projection_size = len(canonical_bytes(projection))

check(full_size > 100000, "Synthetic canonical bundle is oversized")
check(bundle == before, "Projection does not mutate canonical bundle")
check(
    projection_size
    <= GROQ_PROJECTED_GROUNDING_MAX_BYTES,
    "Projected grounding respects byte budget",
)
check(
    GROQ_PROJECTED_GROUNDING_MAX_BYTES == 11000,
    "Projection budget supports compact real-world payloads",
)
check(
    projection["payload_sha256"]
    == bundle["payload_sha256"],
    "Canonical grounding hash preserved",
)
check(
    projection["transport_projection"]["version"]
    == GROQ_PROJECTION_VERSION,
    "Projection version exposed",
)
check(
    (
        projection["transport_projection"]
        ["canonical_payload_sha256"]
        == bundle["payload_sha256"]
    ),
    "Projection identifies canonical payload",
)

projected_framework = (
    projection
    .get("grounded_data", {})
    .get("framework", {})
)

check(
    "evidence_snapshot" not in projected_framework,
    "Duplicate evidence snapshot removed",
)
check(
    "technical_snapshot" not in projected_framework,
    "Duplicate technical snapshot removed",
)
check(
    "valuation_snapshot" not in projected_framework,
    "Duplicate valuation snapshot removed",
)
check(
    (
        projection["transport_projection"]
        ["omitted_counts"]["evidence_refs"]
        > 0
    ),
    "Omitted evidence references counted",
)
check(
    (
        projection["transport_projection"]
        ["omitted_counts"]["events"]
        > 0
    ),
    "Omitted events counted",
)

network_calls = 0


def forbidden_transport(**kwargs):
    global network_calls
    network_calls += 1
    raise AssertionError("Network call is forbidden")


provider = GroqChatCompletionsProvider(
    api_key="gsk_test_only",
    model="openai/gpt-oss-120b",
    transport=forbidden_transport,
    sleep_fn=lambda _: None,
)

request_payload = provider._request_payload(bundle)
request_body = json.dumps(
    request_payload,
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")

check(
    len(request_body) <= GROQ_MAX_REQUEST_BYTES,
    "Complete Groq request respects byte ceiling",
)
check(network_calls == 0, "Projection test makes no network calls")
check(
    request_payload["response_format"]["type"]
    == "json_schema",
    "Strict response format retained",
)
check(
    (
        request_payload["response_format"]
        ["json_schema"]["strict"]
        is True
    ),
    "Strict schema retained",
)
check(
    request_payload["messages"][0]["role"] == "system",
    "System instruction retained",
)
check(
    request_payload["messages"][1]["role"] == "user",
    "Projected grounding sent as user message",
)

prefix = "THESISOS_GROUNDING_BUNDLE\n"
user_content = request_payload["messages"][1]["content"]

check(
    user_content.startswith(prefix),
    "Grounding marker retained",
)

transmitted = json.loads(user_content[len(prefix):])

check(
    transmitted["payload_sha256"]
    == bundle["payload_sha256"],
    "Transmitted projection preserves canonical hash",
)
check(
    (
        transmitted["transport_projection"]["version"]
        == GROQ_PROJECTION_VERSION
    ),
    "Transmitted payload is explicitly projected",
)
check(
    "X" * 500 not in user_content,
    "Oversized raw content not transmitted",
)
check(
    provider._request_payload(bundle) == request_payload,
    "Projection and request are deterministic",
)
check(
    GROQ_CHAT_COMPLETIONS_URL.endswith(
        "/chat/completions"
    ),
    "Groq endpoint unchanged",
)

print("-" * 64)
print(f"Canonical bytes: {full_size}")
print(f"Projected bytes: {projection_size}")
print(f"Request bytes: {len(request_body)}")
print(f"Result: {passed} passed, {failed} failed")

if failed:
    raise SystemExit(1)
