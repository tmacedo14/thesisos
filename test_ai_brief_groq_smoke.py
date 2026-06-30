#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_brief_groq import (
    GROQ_API_KEY_ENV,
    GROQ_DEFAULT_MODEL,
    GROQ_PROVIDER_NAME,
    GROQ_RESPONSES_URL,
)
from ai_brief_groq_smoke import (
    CONFIRM_ENV,
    CONFIRM_VALUE,
    GroqSmokeGuardError,
    dry_run_summary,
    run_live,
)
from ai_brief_openai import TransportResponse

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


def provider_content() -> dict:
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
        "id": "resp_groq_smoke_fake",
        "status": "completed",
        "model": GROQ_DEFAULT_MODEL,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            provider_content()
                        ),
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 700,
            "output_tokens": 350,
            "total_tokens": 1050,
        },
    }

    return TransportResponse(
        status=200,
        headers={
            "x-request-id": "request_groq_smoke_fake",
        },
        body=json.dumps(payload).encode("utf-8"),
    )


class RecordingTransport:
    def __init__(self) -> None:
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
        return success_response()


def configured() -> dict:
    return {
        CONFIRM_ENV: CONFIRM_VALUE,
        GROQ_API_KEY_ENV: "fake-groq-smoke-secret",
        "THESISOS_AI_BRIEF_MODEL": GROQ_DEFAULT_MODEL,
        "THESISOS_AI_BRIEF_TIMEOUT_SECONDS": "15",
    }


def main() -> int:
    dry = dry_run_summary({})

    check(
        dry["mode"] == "groq_dry_run",
        "Groq dry-run mode",
    )
    check(
        dry["network_calls"] == 0,
        "Groq dry-run makes no calls",
    )
    check(
        dry["provider"] == GROQ_PROVIDER_NAME,
        "Groq dry-run provider",
    )
    check(
        dry["model"] == GROQ_DEFAULT_MODEL,
        "Groq default model",
    )
    check(
        dry["endpoint"] == GROQ_RESPONSES_URL,
        "Groq dry-run endpoint",
    )
    check(
        dry["strict_structured_outputs"] is True,
        "Groq strict Structured Outputs",
    )
    check(
        dry["tools_enabled"] is False,
        "Groq dry-run confirms no tools",
    )
    check(
        dry["store_will_be_forced_false"] is True,
        "Groq dry-run declares store false",
    )
    check(
        dry["actual_request_limit"] == 1,
        "Groq one-call limit",
    )
    check(
        dry["runtime_activated"] is False,
        "Groq runtime remains inactive",
    )

    blocked = False

    try:
        run_live(
            {
                GROQ_API_KEY_ENV: "fake-groq-smoke-secret",
            },
            transport=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    AssertionError("Network should be blocked")
                )
            ),
        )
    except GroqSmokeGuardError:
        blocked = True

    check(
        blocked,
        "Groq live blocked without confirmation",
    )

    missing_key = False

    try:
        run_live(
            {
                CONFIRM_ENV: CONFIRM_VALUE,
            },
            transport=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    AssertionError("Network should be blocked")
                )
            ),
        )
    except GroqSmokeGuardError:
        missing_key = True

    check(
        missing_key,
        "Groq live blocked without secret",
    )

    transport = RecordingTransport()
    ticks = iter([10.0, 10.150])
    summary = run_live(
        configured(),
        transport=transport,
        clock=ticks.__next__,
    )

    check(
        len(transport.calls) == 1,
        "Exactly one fake Groq HTTP call",
    )
    check(
        summary["network_calls"] == 1,
        "Groq one-call summary",
    )
    check(
        summary["transport_attempts"] == 1,
        "Groq single attempt",
    )
    check(
        summary["elapsed_ms"] == 150,
        "Groq elapsed time",
    )
    check(
        summary["status"] == "ready",
        "Groq ready result",
    )
    check(
        summary["schema_valid"] is True,
        "Groq contract validated",
    )
    check(
        summary["success"] is True,
        "Groq smoke success",
    )
    check(
        summary["provider"] == GROQ_PROVIDER_NAME,
        "Groq provider preserved",
    )
    check(
        summary["model"] == GROQ_DEFAULT_MODEL,
        "Groq model preserved",
    )
    check(
        summary["response_id"]
        == "resp_groq_smoke_fake",
        "Groq response id summarized",
    )
    check(
        summary["request_id"]
        == "request_groq_smoke_fake",
        "Groq request id summarized",
    )
    check(
        summary.get("usage", {}).get("total_tokens")
        == 1050,
        "Groq usage summarized",
    )
    check(
        summary["request"]["endpoint"]
        == GROQ_RESPONSES_URL,
        "Groq endpoint transmitted",
    )
    check(
        summary["request"]["store"] is False,
        "Groq store=false forced",
    )
    check(
        summary["request"]["strict"] is True,
        "Groq strict format preserved",
    )
    check(
        summary["runtime_activated"] is False,
        "Runtime still inactive after smoke",
    )

    sent = json.loads(
        transport.calls[0]["body"].decode("utf-8")
    )

    check(
        sent["store"] is False,
        "Groq transmitted store=false",
    )
    check(
        "tools" not in sent,
        "Groq transmitted no tools",
    )
    check(
        sent["text"]["format"]["strict"] is True,
        "Groq transmitted strict schema",
    )

    serialized = json.dumps(summary)

    check(
        "fake-groq-smoke-secret"
        not in serialized,
        "Groq secret excluded from output",
    )
    check(
        "executive_summary" not in serialized,
        "Groq generated content excluded",
    )
    check(
        "grounded_data" not in serialized,
        "Groq grounding excluded",
    )

    source = (
        ROOT / "ai_brief_groq_smoke.py"
    ).read_text(encoding="utf-8")
    shared_smoke_source = (
        ROOT / "ai_brief_live_smoke.py"
    ).read_text(encoding="utf-8")

    check(
        "self.inner(\n            url=url,"
        in shared_smoke_source,
        "Shared transport uses keyword-only url",
    )
    check(
        GROQ_API_KEY_ENV
        == "THESISOS_AI_BRIEF_GROQ_API_KEY"
        and "GROQ_API_KEY_ENV" in source,
        "Groq smoke uses dedicated secret",
    )
    check(
        "THESISOS_AI_BRIEF_API_KEY"
        not in source,
        "Groq smoke does not reuse OpenAI secret",
    )
    check(
        "print(bundle" not in source
        and "print(brief" not in source,
        "Groq smoke prints no raw data",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
