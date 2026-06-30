from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path

from ai_brief_grounding import (
    GroundingError,
    MAX_LIST_ITEMS,
    MAX_STRING_CHARS,
    brief_grounding_metadata,
    build_grounding_bundle,
    canonical_sha256,
    generation_allowed,
    verify_grounding_hash,
)


ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "contracts" / "examples"
SCHEMA = (
    ROOT
    / "contracts"
    / "ai_investment_brief_grounding.schema.json"
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


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def contains_sensitive_key(value) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")

            if normalized in {
                "api_key",
                "authorization",
                "cookie",
                "headers",
                "password",
                "prompt",
                "raw_response",
                "secret",
                "token",
            }:
                return True

            if contains_sensitive_key(child):
                return True

    if isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)

    return False


def ready_payload() -> dict:
    return {
        "status": "ok",
        "generated_at": "2026-06-29T15:30:00+00:00",
        "asset": {
            "symbol": "demo",
            "name": "Demonstration Asset",
            "asset_type": "stock",
            "exchange": "DEMO",
            "currency": "USD",
        },
        "data_quality": {
            "completeness_percentage": 94,
        },
        "fundamentals": {
            "status": "ok",
            "revenue": 125000000,
            "free_cash_flow": 17000000,
            "api_key": "must-never-leak",
        },
        "framework_engine": {
            "status": "ok",
            "score": 78,
        },
        "valuation": {
            "status": "partial",
            "score": 62,
        },
        "technical": {
            "status": "ok",
            "trend": "positive",
        },
        "evidence": {
            "status": "ok",
            "events": [
                {
                    "source_kind": "official",
                    "form": "10-Q",
                    "headline": "Quarterly filing",
                    "event_date": "2026-06-28",
                    "document_url": (
                        "https://example.invalid/filing"
                    ),
                }
            ],
        },
        "portfolio_fit": {
            "status": "ok",
            "decision": "watch",
        },
        "market_context": {
            "status": "ok",
            "rates": "restrictive",
        },
        "unexpected_provider_blob": {
            "secret": "not-whitelisted",
        },
    }


def main() -> int:
    schema = load_json(SCHEMA)

    check(
        schema.get("$schema")
        == "https://json-schema.org/draft/2020-12/schema",
        "Grounding JSON Schema draft",
    )

    check(
        schema.get("additionalProperties") is False,
        "Grounding top-level additional properties disabled",
    )

    ready_example = load_json(
        EXAMPLES / "ai_investment_brief_grounding.ready.json"
    )
    insufficient_example = load_json(
        EXAMPLES
        / "ai_investment_brief_grounding.insufficient_data.json"
    )

    check(
        ready_example["coverage"]["status"] == "ready",
        "Ready example coverage",
    )

    check(
        insufficient_example["coverage"]["status"]
        == "insufficient_data",
        "Insufficient-data example coverage",
    )

    check(
        verify_grounding_hash(ready_example),
        "Ready example hash",
    )

    check(
        verify_grounding_hash(insufficient_example),
        "Insufficient-data example hash",
    )

    payload = ready_payload()
    original = deepcopy(payload)
    bundle = build_grounding_bundle(payload)

    check(
        payload == original,
        "Input payload is not mutated",
    )

    monitor_payload = ready_payload()
    monitor_payload["framework_engine"]["decision"] = {
        "status": "awaiting_full_assessment",
        "action": "monitor",
        "label": "Await qualitative review",
        "buy_hold_avoid_sell": None,
        "entry_zone": None,
        "position_size": None,
    }

    monitor_bundle = build_grounding_bundle(
        monitor_payload
    )

    check(
        monitor_bundle["grounded_data"]["framework"][
            "decision"
        ]["action"]
        == "watch",
        "Framework monitor action normalized to watch",
    )

    check(
        monitor_payload["framework_engine"]["decision"][
            "action"
        ]
        == "monitor",
        "Decision normalization does not mutate input",
    )

    check(
        verify_grounding_hash(monitor_bundle),
        "Normalized decision included in canonical hash",
    )

    hold_payload = ready_payload()
    hold_payload["framework_engine"]["decision"] = {
        "action": "hold",
    }

    hold_bundle = build_grounding_bundle(hold_payload)

    check(
        hold_bundle["grounded_data"]["framework"][
            "decision"
        ]["action"]
        == "hold",
        "Supported framework action remains unchanged",
    )

    check(
        bundle["source"] == "thesisos",
        "ThesisOS-only source",
    )

    check(
        bundle["bundle_version"] == "1.0",
        "Grounding bundle version",
    )

    check(
        bundle["asset"]["symbol"] == "DEMO",
        "Asset symbol normalization",
    )

    check(
        bundle["coverage"]["status"] == "ready",
        "Ready payload classification",
    )

    check(
        bundle["coverage"]["uncertainty_level"] == "low",
        "Ready uncertainty level",
    )

    check(
        bundle["coverage"]["data_completeness_pct"] == 94,
        "Explicit completeness preserved",
    )

    check(
        verify_grounding_hash(bundle),
        "Generated bundle hash",
    )

    reordered = {
        key: deepcopy(payload[key])
        for key in reversed(list(payload))
    }

    reordered["asset"] = {
        key: payload["asset"][key]
        for key in reversed(list(payload["asset"]))
    }

    reordered_bundle = build_grounding_bundle(reordered)

    check(
        reordered_bundle["payload_sha256"]
        == bundle["payload_sha256"],
        "Hash independent of dictionary key order",
    )

    changed = deepcopy(payload)
    changed["framework_engine"]["score"] = 79
    changed_bundle = build_grounding_bundle(changed)

    check(
        changed_bundle["payload_sha256"]
        != bundle["payload_sha256"],
        "Hash changes when grounded data changes",
    )

    check(
        "unexpected_provider_blob"
        not in bundle["grounded_data"],
        "Unknown top-level section excluded",
    )

    check(
        not contains_sensitive_key(bundle),
        "Sensitive keys removed",
    )

    check(
        "api_key"
        not in bundle["grounded_data"]["fundamentals"],
        "Nested API key removed",
    )

    evidence_ids = [
        reference["id"]
        for reference in bundle["evidence_refs"]
    ]

    check(
        len(evidence_ids) == len(set(evidence_ids)),
        "Evidence reference ids are unique",
    )

    check(
        "official_filing" in bundle["source_types"],
        "Official filing source type detected",
    )

    check(
        "thesisos_engine" in bundle["source_types"],
        "ThesisOS engine source type detected",
    )

    check(
        generation_allowed(bundle),
        "Generation allowed for ready bundle",
    )

    metadata = brief_grounding_metadata(bundle)

    check(
        metadata["evidence_count"]
        == len(bundle["evidence_refs"]),
        "Brief metadata evidence count",
    )

    check(
        metadata["payload_sha256"]
        == bundle["payload_sha256"],
        "Brief metadata preserves hash",
    )

    partial_payload = ready_payload()
    partial_payload.pop("market_context")
    partial_bundle = build_grounding_bundle(partial_payload)

    check(
        partial_bundle["coverage"]["status"] == "partial",
        "Partial payload classification",
    )

    check(
        generation_allowed(partial_bundle),
        "Generation allowed for partial bundle",
    )

    insufficient_payload = {
        "asset": {
            "name": "Unknown",
        },
        "technical": {
            "status": "ok",
        },
    }

    insufficient_bundle = build_grounding_bundle(
        insufficient_payload
    )

    check(
        insufficient_bundle["coverage"]["status"]
        == "insufficient_data",
        "Missing critical data classification",
    )

    check(
        not generation_allowed(insufficient_bundle),
        "Generation blocked for insufficient data",
    )

    missing = set(
        insufficient_bundle["coverage"][
            "missing_critical_fields"
        ]
    )

    check(
        {
            "asset.symbol",
            "asset.asset_type",
            "grounded_data.framework",
        }.issubset(missing),
        "Explicit missing critical fields",
    )

    long_payload = ready_payload()
    long_payload["framework_engine"]["long_text"] = (
        "x" * (MAX_STRING_CHARS + 100)
    )
    long_payload["framework_engine"]["long_list"] = list(
        range(MAX_LIST_ITEMS + 10)
    )
    limited_bundle = build_grounding_bundle(long_payload)

    check(
        len(
            limited_bundle["grounded_data"]["framework"][
                "long_text"
            ]
        )
        == MAX_STRING_CHARS,
        "String length limit",
    )

    check(
        len(
            limited_bundle["grounded_data"]["framework"][
                "long_list"
            ]
        )
        == MAX_LIST_ITEMS,
        "List length limit",
    )

    tampered = deepcopy(bundle)
    tampered["grounded_data"]["framework"]["score"] = -1

    check(
        not verify_grounding_hash(tampered),
        "Tampering invalidates hash",
    )

    error_raised = False

    try:
        brief_grounding_metadata(tampered)
    except GroundingError:
        error_raised = True

    check(
        error_raised,
        "Invalid hash blocks metadata export",
    )

    non_mapping_error = False

    try:
        build_grounding_bundle(["invalid"])
    except GroundingError:
        non_mapping_error = True

    check(
        non_mapping_error,
        "Non-mapping payload rejected",
    )

    check(
        bool(
            re.fullmatch(
                r"[a-f0-9]{64}",
                canonical_sha256({"b": 2, "a": 1}),
            )
        ),
        "Canonical SHA-256 format",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
