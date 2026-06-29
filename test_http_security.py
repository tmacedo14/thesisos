from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from http_security import (
    ALLOWED_METHODS,
    BASE_SECURITY_HEADERS,
    HSTS_HEADER,
    TRANSITIONAL_CONTENT_SECURITY_POLICY,
    security_headers_for_request,
)


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:3000"
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


def http_request(
    path: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = Request(
        BASE_URL + path,
        headers=headers or {},
        method=method,
    )

    try:
        with urlopen(request, timeout=5) as response:
            return (
                response.status,
                {
                    key.lower(): value
                    for key, value in response.headers.items()
                },
                response.read(),
            )
    except HTTPError as error:
        return (
            error.code,
            {
                key.lower(): value
                for key, value in error.headers.items()
            },
            error.read(),
        )


def static_checks() -> None:
    check(
        BASE_SECURITY_HEADERS["X-Content-Type-Options"]
        == "nosniff",
        "MIME sniffing disabled",
    )

    check(
        BASE_SECURITY_HEADERS["X-Frame-Options"] == "DENY",
        "Legacy framing protection",
    )

    check(
        "frame-ancestors 'none'"
        in TRANSITIONAL_CONTENT_SECURITY_POLICY,
        "CSP framing protection",
    )

    check(
        "object-src 'none'"
        in TRANSITIONAL_CONTENT_SECURITY_POLICY,
        "CSP object blocking",
    )

    check(
        "'unsafe-eval'"
        not in TRANSITIONAL_CONTENT_SECURITY_POLICY,
        "CSP excludes unsafe-eval",
    )

    check(
        "'unsafe-inline'"
        in TRANSITIONAL_CONTENT_SECURITY_POLICY,
        "CSP marked as transitional",
    )

    check(
        "upgrade-insecure-requests"
        in TRANSITIONAL_CONTENT_SECURITY_POLICY,
        "CSP upgrades mixed content",
    )

    local_headers = security_headers_for_request({})

    check(
        "Strict-Transport-Security" not in local_headers,
        "HSTS omitted on plain HTTP",
    )

    proxy_headers = security_headers_for_request(
        {"X-Forwarded-Proto": "https"}
    )

    check(
        proxy_headers.get("Strict-Transport-Security")
        == HSTS_HEADER,
        "HSTS enabled behind HTTPS proxy",
    )

    server_source = (ROOT / "server.py").read_text(
        encoding="utf-8"
    )

    check(
        'server_version = "ThesisOS"' in server_source
        and 'sys_version = ""' in server_source,
        "Python server banner reduced",
    )

    check(
        "def end_headers(" in server_source
        and "security_headers_for_request(" in server_source,
        "Central security headers wired",
    )

    check(
        "def do_OPTIONS(" in server_source,
        "OPTIONS explicitly implemented",
    )

    for method in ("TRACE", "PUT", "PATCH", "DELETE"):
        check(
            f"def do_{method}(" in server_source,
            f"{method} explicitly blocked",
        )

    workflow = (
        ROOT / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    check(
        "permissions:" in workflow
        and "contents: read" in workflow,
        "CI least-privilege permissions",
    )

    check(
        "secrets." not in workflow,
        "CI uses no repository secrets",
    )

    check(
        "test_http_security.py --live" in workflow,
        "CI runs live security tests",
    )

    check(
        "test_thesisos_final.py --full"
        not in workflow,
        "CI excludes network-dependent FULL suite",
    )

    check(
        'THESISOS_AI_BRIEF_ENABLED: "false"'
        in workflow,
        "CI keeps AI provider disabled",
    )


def live_checks() -> None:
    required = {
        name.lower(): value
        for name, value in BASE_SECURITY_HEADERS.items()
    }

    for path in ("/", "/api/health", "/api/ai-brief/config"):
        status, headers, _ = http_request(path)

        check(
            status == 200,
            f"Live {path} HTTP 200",
        )

        for name, expected in required.items():
            check(
                headers.get(name) == expected,
                f"Live {path} header {name}",
            )

        check(
            "python" not in headers.get("server", "").lower()
            and headers.get("server", "").startswith("ThesisOS"),
            f"Live {path} banner reduced",
        )

    status, headers, body = http_request(
        "/api/health",
        headers={"X-Forwarded-Proto": "https"},
    )

    check(
        status == 200
        and headers.get("strict-transport-security")
        == HSTS_HEADER,
        "Live HSTS behind proxy",
    )

    status, headers, body = http_request(
        "/api/health",
        method="OPTIONS",
    )

    check(
        status == 204,
        "Live OPTIONS returns 204",
    )

    check(
        headers.get("allow") == ALLOWED_METHODS,
        "Live OPTIONS Allow header",
    )

    for method in ("TRACE", "PUT", "PATCH", "DELETE"):
        status, headers, body = http_request(
            "/api/health",
            method=method,
        )

        check(
            status == 405,
            f"Live {method} returns 405",
        )

        check(
            headers.get("allow") == ALLOWED_METHODS,
            f"Live {method} Allow header",
        )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}

        check(
            payload.get("error") == "method_not_allowed",
            f"Live {method} normalized error",
        )

    status, headers, _ = http_request("/api/health")

    check(
        headers.get("cache-control") == "no-store",
        "API no-store preserved",
    )


def main() -> int:
    static_checks()

    if "--live" in sys.argv:
        live_checks()

    print("-" * 64)
    print(f"Result: {PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
