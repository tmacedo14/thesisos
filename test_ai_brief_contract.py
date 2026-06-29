from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "contracts" / "ai_investment_brief.schema.json"
EXAMPLE_DIR = ROOT / "contracts" / "examples"

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


def validate_common(
    payload: dict,
    required_fields: set[str],
    statuses: set[str],
    decision_actions: set[str],
) -> None:
    check(
        required_fields.issubset(payload),
        f"{payload.get('brief_id', 'unknown')} — required top-level fields",
    )

    check(
        payload.get("schema_version") == "1.0",
        f"{payload.get('brief_id', 'unknown')} — schema version",
    )

    check(
        payload.get("status") in statuses,
        f"{payload.get('brief_id', 'unknown')} — valid status",
    )

    decision = payload.get("decision") or {}

    check(
        decision.get("action") in decision_actions,
        f"{payload.get('brief_id', 'unknown')} — valid decision action",
    )

    confidence = decision.get("confidence")

    check(
        isinstance(confidence, int) and 0 <= confidence <= 100,
        f"{payload.get('brief_id', 'unknown')} — confidence range",
    )

    grounding = payload.get("grounding") or {}
    evidence_refs = grounding.get("evidence_refs") or []

    check(
        grounding.get("source") == "thesisos",
        f"{payload.get('brief_id', 'unknown')} — ThesisOS-only grounding",
    )

    check(
        grounding.get("bundle_version") == "1.0",
        f"{payload.get('brief_id', 'unknown')} — grounding bundle version",
    )

    completeness = grounding.get("data_completeness_pct")

    check(
        isinstance(completeness, (int, float))
        and 0 <= completeness <= 100,
        f"{payload.get('brief_id', 'unknown')} — completeness range",
    )

    check(
        grounding.get("evidence_count") == len(evidence_refs),
        f"{payload.get('brief_id', 'unknown')} — evidence count consistency",
    )

    payload_hash = grounding.get("payload_sha256", "")

    check(
        bool(re.fullmatch(r"[a-f0-9]{64}", payload_hash)),
        f"{payload.get('brief_id', 'unknown')} — SHA-256 format",
    )

    evidence_ids = [reference.get("id") for reference in evidence_refs]

    check(
        len(evidence_ids) == len(set(evidence_ids)),
        f"{payload.get('brief_id', 'unknown')} — unique evidence ids",
    )

    evidence_paths_valid = all(
        isinstance(reference.get("field_paths"), list)
        and bool(reference["field_paths"])
        for reference in evidence_refs
    )

    check(
        evidence_paths_valid,
        f"{payload.get('brief_id', 'unknown')} — evidence field paths",
    )


def main() -> int:
    schema = load_json(SCHEMA_PATH)

    required_fields = set(schema.get("required", []))
    statuses = set(schema["properties"]["status"]["enum"])
    decision_actions = set(
        schema["$defs"]["decision"]["properties"]["action"]["enum"]
    )

    expected_statuses = {
        "ready",
        "partial",
        "insufficient_data",
        "provider_unavailable",
        "disabled",
        "generation_failed",
    }

    expected_actions = {
        "buy",
        "initiate_small",
        "accumulate",
        "hold",
        "watch",
        "reduce",
        "avoid",
        "sell",
        "unavailable",
    }

    check(
        schema.get("$schema")
        == "https://json-schema.org/draft/2020-12/schema",
        "JSON Schema draft",
    )

    check(
        schema.get("additionalProperties") is False,
        "Top-level additional properties disabled",
    )

    check(statuses == expected_statuses, "Explicit brief states")
    check(decision_actions == expected_actions, "Explicit decision actions")

    ready = load_json(EXAMPLE_DIR / "ai_investment_brief.ready.json")
    insufficient = load_json(
        EXAMPLE_DIR / "ai_investment_brief.insufficient_data.json"
    )

    validate_common(ready, required_fields, statuses, decision_actions)

    check(ready["status"] == "ready", "Ready example status")
    check(
        isinstance(ready.get("model"), dict),
        "Ready example includes model metadata",
    )
    check(ready.get("error") is None, "Ready example has no error")
    check(
        bool(ready["sections"].get("executive_summary")),
        "Ready example contains executive summary",
    )
    check(
        ready["grounding"]["evidence_count"] > 0,
        "Ready example contains grounded evidence",
    )

    validate_common(insufficient, required_fields, statuses, decision_actions)

    check(
        insufficient["status"] == "insufficient_data",
        "Insufficient-data example status",
    )
    check(
        insufficient.get("model") is None,
        "Insufficient-data example does not call a model",
    )
    check(
        insufficient["decision"]["action"] == "unavailable",
        "Insufficient-data decision unavailable",
    )
    check(
        bool(insufficient["uncertainty"]["missing_fields"]),
        "Insufficient-data example lists missing fields",
    )
    check(
        insufficient["sections"]["executive_summary"] is None,
        "Insufficient-data example has no generated summary",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
