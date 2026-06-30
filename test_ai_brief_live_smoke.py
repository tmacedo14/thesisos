#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_brief_live_smoke import (
    API_KEY_ENV,
    CONFIRM_ENV,
    CONFIRM_VALUE,
    LiveSmokeGuardError,
    LiveSmokeLimitError,
    SingleActualRequestTransport,
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
        "id": "resp_live_smoke_fake",
        "status": "completed",
        "model": "test-model",
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
            "input_tokens": 500,
            "input_tokens_details": {
                "cached_tokens": 25,
            },
            "output_tokens": 250,
            "output_tokens_details": {
                "reasoning_tokens": 0,
            },
            "total_tokens": 750,
        },
    }

    return TransportResponse(
        status=200,
        headers={
            "x-request-id": "request_live_smoke_fake",
        },
        body=json.dumps(payload).encode("utf-8"),
    )


class RecordingTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.calls = []

    def __call__(
        self,
        url,
        *,
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
        return self.response


def configured() -> dict:
    return {
        CONFIRM_ENV: CONFIRM_VALUE,
        API_KEY_ENV: "fake-live-smoke-secret",
        "THESISOS_AI_BRIEF_MODEL": "test-model",
        "THESISOS_AI_BRIEF_TIMEOUT_SECONDS": "15",
    }


def main() -> int:
    dry = dry_run_summary({})

    check(dry["mode"] == "dry_run", "Dry-run mode")
    check(dry["network_calls"] == 0, "Dry-run makes no calls")
    check(
        dry["strict_structured_outputs"] is True,
        "Dry-run confirms strict Structured Outputs",
    )
    check(
        dry["tools_enabled"] is False,
        "Dry-run confirms no tools",
    )
    check(
        dry["store_will_be_forced_false"] is True,
        "Dry-run declares store false",
    )
    check(
        dry["actual_request_limit"] == 1,
        "Dry-run declares one-call limit",
    )
    check(
        dry["secret_configured"] is False,
        "Dry-run does not require secret",
    )

    blocked = False

    try:
        run_live(
            {
                API_KEY_ENV: "fake-live-smoke-secret",
            },
            transport=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    AssertionError("Network should be blocked")
                )
            ),
        )
    except LiveSmokeGuardError:
        blocked = True

    check(
        blocked,
        "Live mode blocked without literal confirmation",
    )

    missing_secret = False

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
    except LiveSmokeGuardError:
        missing_secret = True

    check(
        missing_secret,
        "Live mode blocked without secret",
    )

    missing_model = False

    try:
        run_live(
            {
                CONFIRM_ENV: CONFIRM_VALUE,
                API_KEY_ENV: "fake-live-smoke-secret",
            },
            transport=lambda *args, **kwargs: (
                (_ for _ in ()).throw(
                    AssertionError("Network should be blocked")
                )
            ),
        )
    except LiveSmokeGuardError:
        missing_model = True

    check(
        missing_model,
        "Live mode blocked without explicit model",
    )

    recording = RecordingTransport(success_response())
    ticks = iter([10.0, 10.125])
    summary = run_live(
        configured(),
        transport=recording,
        clock=ticks.__next__,
    )

    check(len(recording.calls) == 1, "Exactly one fake HTTP call")
    check(summary["network_calls"] == 1, "One-call summary")
    check(summary["transport_attempts"] == 1, "Single attempt")
    check(summary["elapsed_ms"] == 125, "Elapsed time")
    check(summary["status"] == "ready", "Ready live result")
    check(summary["schema_valid"] is True, "Contract validated")
    check(summary["success"] is True, "Live smoke success")
    check(
        bool(summary["decision_action"]),
        "Decision action summarized",
    )
    check(
        isinstance(summary["confidence"], int),
        "Confidence summarized",
    )
    check(
        summary["response_id"] == "resp_live_smoke_fake",
        "Response id summarized",
    )
    check(
        summary["request_id"] == "request_live_smoke_fake",
        "Request id summarized",
    )
    check(
        summary["usage"]["total_tokens"] == 750,
        "Usage summarized",
    )
    check(
        summary["request"]["store"] is False,
        "store=false forced",
    )
    check(
        summary["request"]["strict"] is True,
        "Strict format preserved",
    )

    sent = json.loads(
        recording.calls[0]["body"].decode("utf-8")
    )
    check(sent["store"] is False, "Transmitted store=false")
    check("tools" not in sent, "No tools transmitted")
    check(
        sent["text"]["format"]["strict"] is True,
        "Strict schema transmitted",
    )

    serialized = json.dumps(summary)
    check(
        "fake-live-smoke-secret" not in serialized,
        "Secret excluded from output",
    )
    check(
        "executive_summary" not in serialized,
        "Generated content excluded from output",
    )
    check(
        "grounded_data" not in serialized,
        "Grounding excluded from output",
    )

    inner = RecordingTransport(success_response())
    limiter = SingleActualRequestTransport(inner)
    limiter(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": "Bearer fake"},
        body=json.dumps({
            "model": "test-model",
            "input": [],
            "text": {
                "format": {
                    "type": "json_schema",
                    "strict": True,
                    "schema": {
                        "type": "object",
                    },
                },
            },
            "max_output_tokens": 10,
        }).encode("utf-8"),
        timeout_seconds=1.0,
    )

    second_blocked = False

    try:
        limiter(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer fake"},
            body=b"{}",
            timeout_seconds=1.0,
        )
    except LiveSmokeLimitError:
        second_blocked = True

    check(second_blocked, "Second request blocked")
    check(len(inner.calls) == 1, "Only one inner call")

    source = (
        ROOT / "ai_brief_live_smoke.py"
    ).read_text(encoding="utf-8")

    check(
        "print(bundle" not in source
        and "print(brief" not in source,
        "No raw bundle or brief printing",
    )
    alternate_key_name = "OPENAI" + "_API_KEY"

    check(
        alternate_key_name not in source,
        "No alternate key variable embedded",
    )
    check(
        "urllib_transport" in source,
        "Existing adapter transport reused",
    )

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
