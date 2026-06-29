from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


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


def ready_analysis_payload() -> dict:
    return {
        "generated_at": "2026-06-29T16:30:00Z",
        "asset": {
            "symbol": "DEMO",
            "name": "Demonstration Asset",
            "asset_type": "stock",
            "exchange": "DEMO",
            "currency": "USD",
        },
        "data_completeness_pct": 95,
        "fundamentals": {
            "status": "ok",
            "revenue": 100,
        },
        "framework_engine": {
            "status": "ok",
            "score": 76,
        },
        "valuation": {"status": "partial"},
        "technical": {"status": "ok"},
        "evidence": {"status": "ok"},
        "portfolio_fit": {"status": "ok"},
        "market_context": {"status": "ok"},
    }


class RecordingResolver:
    def __init__(
        self,
        payload: dict | None = None,
        status: int = 200,
    ) -> None:
        self.payload = payload or ready_analysis_payload()
        self.status = status
        self.calls = []

    def __call__(
        self,
        identifier,
        *,
        base_currency,
        exchange,
        ticker,
    ):
        self.calls.append(
            {
                "identifier": identifier,
                "base_currency": base_currency,
                "exchange": exchange,
                "ticker": ticker,
            }
        )
        return self.payload, self.status


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict]:
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=10) as response:
            body = json.loads(
                response.read().decode("utf-8")
            )
            return response.status, body
    except HTTPError as error:
        body = json.loads(
            error.read().decode("utf-8")
        )
        return error.code, body


def run_pure_tests() -> None:
    config = server.public_ai_brief_configuration({})

    check(
        config["enabled"] is False,
        "Runtime feature disabled by default",
    )

    check(
        config["http_provider_implemented"] is False,
        "HTTP provider explicitly unavailable",
    )

    check(
        config["authentication_required"] is True,
        "Runtime authentication requirement",
    )

    check(
        config["request_limit_bytes"] == 16384,
        "Runtime request-size limit",
    )

    serialized_config = json.dumps(config)

    check(
        "THESISOS_AI_BRIEF_API_KEY"
        not in serialized_config,
        "Configuration excludes API-key name",
    )

    check(
        "secret" not in serialized_config.lower(),
        "Configuration excludes secret material",
    )

    normalized = server.normalize_ai_brief_request(
        {
            "identifier": " aapl ",
            "base_currency": "eur",
            "exchange": "",
            "ticker": "aapl",
        }
    )

    check(
        normalized == {
            "identifier": "aapl",
            "base_currency": "EUR",
            "exchange": None,
            "ticker": "AAPL",
        },
        "Runtime request normalization",
    )

    rejected_grounding = False

    try:
        server.normalize_ai_brief_request(
            {
                "identifier": "AAPL",
                "grounding": {},
            }
        )
    except ValueError:
        rejected_grounding = True

    check(
        rejected_grounding,
        "Client grounding injection rejected",
    )

    invalid_currency = False

    try:
        server.normalize_ai_brief_request(
            {
                "identifier": "AAPL",
                "base_currency": "EURO",
            }
        )
    except ValueError:
        invalid_currency = True

    check(
        invalid_currency,
        "Invalid base currency rejected",
    )

    resolver = RecordingResolver()

    brief, status = server.build_ai_brief_runtime_response(
        {
            "identifier": "DEMO",
            "base_currency": "EUR",
        },
        environ={},
        analysis_resolver=resolver,
    )

    check(
        status == 200,
        "Disabled runtime response HTTP status",
    )

    check(
        brief["status"] == "disabled",
        "Disabled runtime contract state",
    )

    check(
        len(resolver.calls) == 1,
        "Analysis resolver called exactly once",
    )

    check(
        brief["grounding"]["source"] == "thesisos",
        "Grounding constructed server-side",
    )

    check(
        len(brief["grounding"]["payload_sha256"]) == 64,
        "Runtime grounding hash",
    )

    configured_without_provider, configured_status = (
        server.build_ai_brief_runtime_response(
            {"identifier": "DEMO"},
            environ={
                "THESISOS_AI_BRIEF_ENABLED": "true",
                "THESISOS_AI_BRIEF_PROVIDER": "example",
                "THESISOS_AI_BRIEF_MODEL": "example-model",
                "THESISOS_AI_BRIEF_API_KEY": "not-exposed",
            },
            analysis_resolver=RecordingResolver(),
        )
    )

    check(
        configured_status == 200
        and configured_without_provider["status"]
        == "provider_unavailable",
        "Enabled runtime without HTTP adapter",
    )

    failed_payload, failed_status = (
        server.build_ai_brief_runtime_response(
            {"identifier": "UNKNOWN"},
            environ={},
            analysis_resolver=RecordingResolver(
                payload={"error": "Ativo não encontrado."},
                status=404,
            ),
        )
    )

    check(
        failed_status == 404,
        "Analysis client error status preserved",
    )

    check(
        failed_payload["error"] == "Ativo não encontrado.",
        "Analysis client error normalized",
    )

    unique_user = "runtime-test-user"
    server._AI_BRIEF_RATE_LIMIT_STATE.pop(
        unique_user,
        None,
    )

    decisions = [
        server.consume_ai_brief_rate_limit(
            unique_user,
            now=1000.0 + index,
        )
        for index in range(5)
    ]

    blocked, retry_after = (
        server.consume_ai_brief_rate_limit(
            unique_user,
            now=1005.0,
        )
    )

    check(
        all(allowed for allowed, _ in decisions),
        "First five runtime requests allowed",
    )

    check(
        blocked is False and retry_after > 0,
        "Sixth runtime request rate-limited",
    )

    server._AI_BRIEF_RATE_LIMIT_STATE.pop(
        unique_user,
        None,
    )

    server_source = Path("server.py").read_text(
        encoding="utf-8"
    )

    check(
        'parsed_url.path == "/api/ai-brief/config"'
        in server_source,
        "Runtime config route present",
    )

    check(
        'parsed_url.path == "/api/ai-brief"'
        in server_source,
        "Runtime POST route present",
    )

    check(
        "build_grounding_bundle(" in server_source,
        "Server-side grounding integration",
    )

    check(
        "provider=None" in server_source,
        "No concrete HTTP provider wired",
    )


def run_live_tests(base_url: str) -> None:
    status, config = json_request(
        f"{base_url}/api/ai-brief/config"
    )

    check(
        status == 200,
        "Live config endpoint HTTP 200",
    )

    check(
        config.get("enabled") is False,
        "Live config feature disabled",
    )

    check(
        "api_key" not in {
            key
            for key in config
            if key != "api_key_configured"
        },
        "Live config exposes no API key",
    )

    status, payload = json_request(
        f"{base_url}/api/ai-brief",
        method="POST",
        payload={"identifier": "AAPL"},
    )

    check(
        status == 401,
        "Anonymous AI Brief request denied",
    )

    check(
        "Supabase" in str(payload.get("error") or ""),
        "Anonymous denial is explicit",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:3000",
    )
    args = parser.parse_args()

    run_pure_tests()

    if args.live:
        run_live_tests(args.base_url.rstrip("/"))

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
