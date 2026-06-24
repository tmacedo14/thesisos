#!/usr/bin/env python3
"""ThesisOS final demonstration and validation test.

Quick mode validates the repository, JavaScript, Python, local API and
Supabase configuration status without writing data.

Full mode also executes real AAPL analysis, valuation, evidence, Radar and
read-only Cloud Sync authentication when THESISOS_SYNC_PASSWORD is available.

Usage:
    python3 test_thesisos_final.py
    python3 test_thesisos_final.py --full
    python3 test_thesisos_final.py --base-url http://localhost:3000
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "http://localhost:3000"


@dataclass
class Result:
    level: str
    name: str
    detail: str = ""


class Suite:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def pass_(self, name: str, detail: str = "") -> None:
        self.results.append(Result("PASS", name, detail))

    def warn(self, name: str, detail: str = "") -> None:
        self.results.append(Result("WARN", name, detail))

    def fail(self, name: str, detail: str = "") -> None:
        self.results.append(Result("FAIL", name, detail))

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        (self.pass_ if condition else self.fail)(name, detail)

    def print_report(self, mode: str) -> int:
        print("=" * 64)
        print("THESISOS — FINAL DEMONSTRATION TEST")
        print(f"Mode: {mode}")
        print("=" * 64)
        for item in self.results:
            suffix = f" — {item.detail}" if item.detail else ""
            print(f"[{item.level}] {item.name}{suffix}")
        counts = {
            level: sum(result.level == level for result in self.results)
            for level in ("PASS", "WARN", "FAIL")
        }
        print("-" * 64)
        print(
            f"Result: {counts['PASS']} passed, "
            f"{counts['WARN']} warnings, {counts['FAIL']} failed"
        )
        if counts["FAIL"]:
            print("ThesisOS requires attention before demonstration.")
            return 1
        print("ThesisOS is ready for demonstration.")
        return 0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
    opener=None,
) -> tuple[int, dict[str, Any]]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=headers)
    runner = opener.open if opener is not None else urlopen
    try:
        with runner(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"error": raw[:500]}
        return error.code, data


def endpoint(base_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    return url


def local_server_available(base_url: str) -> bool:
    try:
        status, data = json_request(endpoint(base_url, "/api/health"), timeout=3)
        return status == 200 and data.get("status") == "ok"
    except (URLError, TimeoutError, json.JSONDecodeError):
        return False


def start_local_server(base_url: str) -> subprocess.Popen | None:
    if base_url.rstrip("/") != DEFAULT_BASE_URL:
        return None
    if local_server_available(base_url):
        return None
    log = open(tempfile.gettempdir() + "/thesisos_final_test_server.log", "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    for _ in range(30):
        if process.poll() is not None:
            break
        if local_server_available(base_url):
            return process
        time.sleep(0.5)
    return process


def test_static(suite: Suite) -> None:
    required = [
        "index.html",
        "server.py",
        "requirements.txt",
        "README.md",
        "supabase_schema.sql",
        ".replit",
        ".gitignore",
    ]
    missing = [name for name in required if not (ROOT / name).exists()]
    suite.check(
        "Required project files",
        not missing,
        "all present" if not missing else "missing: " + ", ".join(missing),
    )
    if missing:
        return

    index = read_text(ROOT / "index.html")
    server = read_text(ROOT / "server.py")
    readme = read_text(ROOT / "README.md")
    requirements = read_text(ROOT / "requirements.txt")
    schema = read_text(ROOT / "supabase_schema.sql").lower()
    replit = read_text(ROOT / ".replit")

    try:
        compile(server, "server.py", "exec")
        suite.pass_("Python syntax", "server.py compiles")
    except SyntaxError as error:
        suite.fail("Python syntax", f"line {error.lineno}: {error.msg}")

    scripts = re.findall(r"<script>(.*?)</script>", index, flags=re.DOTALL)
    suite.check("Inline JavaScript extraction", len(scripts) == 1, f"{len(scripts)} script block(s)")
    if scripts:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write("\n".join(scripts))
            js_path = handle.name
        try:
            result = subprocess.run(
                ["node", "--check", js_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                suite.pass_("JavaScript syntax", "node --check")
            else:
                suite.fail("JavaScript syntax", (result.stderr or result.stdout).strip()[:500])
        except FileNotFoundError:
            suite.warn("JavaScript syntax", "Node.js not available")
        finally:
            Path(js_path).unlink(missing_ok=True)

    modules = {
        "Live Dashboard": "dashboardRefreshAllBtn",
        "Opportunity Radar": "dynamicRadarCards",
        "Technical Engine": "technicalEngineCard",
        "Valuation Engine": "stockValuationPanel",
        "Evidence & Events Engine": "evidenceEventsCard",
        "Portfolio Manager": "portfolioTransactionModal",
        "Decision Journal": "decisionJournalModal",
        "Monitoring Alerts": "monitoringAlertModal",
        "Investor Policy": "policyPositionSizingCard",
        "Portfolio Construction": "constructionPlanList",
        "Cloud Sync": "cloudSyncCard",
    }
    absent = [name for name, marker in modules.items() if f'id="{marker}"' not in index]
    suite.check(
        "Frontend modules",
        not absent,
        f"{len(modules)} modules detected" if not absent else "missing: " + ", ".join(absent),
    )

    routes = [
        "/api/health",
        "/api/analysis/",
        "/api/radar",
        "/api/compare",
        "/api/technical/",
        "/api/valuation/",
        "/api/evidence/",
        "/api/sync/status",
        "/api/sync/login",
        "/api/sync/state",
    ]
    absent_routes = [route for route in routes if route not in server]
    suite.check(
        "Backend API routes",
        not absent_routes,
        f"{len(routes)} routes detected" if not absent_routes else "missing: " + ", ".join(absent_routes),
    )

    suite.check(
        "Framework Engine version",
        "FRAMEWORK_ENGINE_VERSION = \"0.9\"" in server,
        "expected 0.9",
    )

    dependency_ok = "pypdf" in requirements.lower() and "yfinance" in requirements.lower()
    suite.check("Runtime dependencies", dependency_ok, "pypdf and yfinance")

    deployment_ok = bool(
        re.search(
            r"\[deployment\][\s\S]*?run\s*=\s*(?:\[\s*[\"']python3[\"']\s*,\s*[\"']server\.py[\"']\s*\]|[\"']python3 server\.py[\"'])",
            replit,
        )
    )
    suite.check("Replit deployment command", deployment_ok, "python3 server.py")
    port_ok = "localPort = 3000" in replit and "externalPort = 80" in replit
    suite.check("Replit port mapping", port_ok, "3000 → 80")

    schema_checks = [
        "create table if not exists public.thesisos_state",
        "payload jsonb not null",
        "primary key (workspace_id, namespace)",
        "enable row level security",
        "revoke all",
    ]
    absent_schema = [item for item in schema_checks if item not in schema]
    suite.check(
        "Supabase schema and security",
        not absent_schema,
        "RLS, NOT NULL and composite key" if not absent_schema else "missing: " + ", ".join(absent_schema),
    )

    exposed_patterns = [
        r"sb_secret_[A-Za-z0-9_-]{8,}",
        r"service_role\s*[:=]\s*[\"'][^\"']+",
        r"FINNHUB_API_KEY\s*=\s*[\"'][^\"']+",
        r"SUPABASE_SECRET_KEY\s*=\s*[\"'][^\"']+",
    ]
    exposed = [pattern for pattern in exposed_patterns if re.search(pattern, index, flags=re.IGNORECASE)]
    suite.check("Frontend secret scan", not exposed, "no embedded server secrets")

    dashboard_match = re.search(
        r'<section id="dashboard".*?</section>', index, flags=re.DOTALL
    )
    dashboard = dashboard_match.group(0) if dashboard_match else ""
    old_demo = [value for value in ("€18.450", "Supabase + motor próprio") if value in dashboard]
    suite.check("Dashboard demo-data removal", not old_demo, "live dashboard markers")

    readme_markers = [
        "Valuation Engine",
        "Evidence",
        "Investor Policy",
        "Portfolio Construction",
        "Supabase",
    ]
    missing_docs = [marker for marker in readme_markers if marker.lower() not in readme.lower()]
    suite.check(
        "README module documentation",
        not missing_docs,
        "final architecture documented" if not missing_docs else "missing: " + ", ".join(missing_docs),
    )


def test_runtime(suite: Suite, base_url: str) -> None:
    try:
        status, data = json_request(endpoint(base_url, "/api/health"), timeout=10)
        suite.check(
            "API health",
            status == 200 and data.get("status") == "ok",
            data.get("service", f"HTTP {status}"),
        )
        providers = data.get("providers") or []
        suite.check("API provider registry", len(providers) >= 7, f"{len(providers)} providers")
    except Exception as error:
        suite.fail("API health", str(error))
        return

    try:
        status, data = json_request(endpoint(base_url, "/api/sync/status"), timeout=10)
        if status != 200 or data.get("status") != "ok":
            suite.fail("Cloud Sync status", f"HTTP {status}: {data}")
        elif data.get("configured"):
            suite.pass_(
                "Cloud Sync configuration",
                f"workspace={data.get('workspace_id')}, provider={data.get('provider')}",
            )
        else:
            suite.warn(
                "Cloud Sync configuration",
                "missing: " + ", ".join(data.get("missing") or []),
            )
    except Exception as error:
        suite.fail("Cloud Sync status", str(error))


def test_full(suite: Suite, base_url: str) -> None:
    try:
        status, analysis = json_request(
            endpoint(base_url, "/api/analysis/AAPL"), timeout=120
        )
        keys = {"asset", "market", "framework_engine", "technical", "valuation", "evidence"}
        missing = sorted(keys - set(analysis)) if isinstance(analysis, dict) else sorted(keys)
        suite.check(
            "AAPL integrated analysis",
            status == 200 and not missing,
            "all engines returned" if not missing else "missing: " + ", ".join(missing),
        )
        if status == 200:
            completeness = analysis.get("data_quality", {}).get("completeness_percentage")
            suite.pass_("AAPL data completeness", f"{completeness if completeness is not None else 'n/a'}%")
    except Exception as error:
        suite.fail("AAPL integrated analysis", str(error))

    for label, path, expected in (
        ("Valuation endpoint", "/api/valuation/AAPL", {"status", "score"}),
        ("Evidence endpoint", "/api/evidence/AAPL", {"status", "events"}),
    ):
        try:
            status, data = json_request(endpoint(base_url, path), timeout=120)
            available = status == 200 and isinstance(data, dict)
            detail = data.get("status", f"HTTP {status}") if isinstance(data, dict) else f"HTTP {status}"
            suite.check(label, available, str(detail))
        except Exception as error:
            suite.fail(label, str(error))

    try:
        status, radar = json_request(
            endpoint(base_url, "/api/radar", {"universe": "core_us", "limit": 2}),
            timeout=180,
        )
        ok = status == 200 and isinstance(radar.get("results"), list)
        suite.check(
            "Opportunity Radar live run",
            ok,
            f"{radar.get('analysed_count', 0)} analysed, {radar.get('error_count', 0)} errors",
        )
    except Exception as error:
        suite.fail("Opportunity Radar live run", str(error))

    password = os.getenv("THESISOS_SYNC_PASSWORD")
    if not password:
        suite.warn("Cloud Sync read test", "THESISOS_SYNC_PASSWORD unavailable in this shell")
        return

    # Read the Set-Cookie response header explicitly. The production cookie is
    # intentionally Secure, so automatic cookie jars will not resend it over
    # plain HTTP localhost. Forwarding the server-issued cookie only inside
    # this local, read-only test preserves the application's security policy.
    try:
        login_body = json.dumps({"password": password}).encode("utf-8")
        login_request = Request(
            endpoint(base_url, "/api/sync/login"),
            data=login_body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(login_request, timeout=30) as response:
                login_status = response.status
                login = json.loads(response.read().decode("utf-8"))
                set_cookie = response.headers.get("Set-Cookie", "")
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                login = json.loads(raw)
            except json.JSONDecodeError:
                login = {"error": raw[:500]}
            login_status = error.code
            set_cookie = ""

        if login_status != 200 or not login.get("authenticated"):
            suite.fail(
                "Cloud Sync authentication",
                f"HTTP {login_status}: {login.get('error', 'failed')}",
            )
            return
        suite.pass_(
            "Cloud Sync authentication",
            f"workspace={login.get('workspace_id')}",
        )

        cookie_pair = set_cookie.split(";", 1)[0].strip()
        if not cookie_pair or "=" not in cookie_pair:
            suite.fail(
                "Cloud Sync read-only restore",
                "login succeeded but no session cookie was returned",
            )
            return

        state_request = Request(
            endpoint(base_url, "/api/sync/state"),
            method="GET",
            headers={
                "Accept": "application/json",
                "Cookie": cookie_pair,
            },
        )
        try:
            with urlopen(state_request, timeout=30) as response:
                state_status = response.status
                state = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                state = {"error": raw[:500]}
            state_status = error.code

        cloud_state = state.get("state") if isinstance(state, dict) else None
        row_count = state.get("row_count") if isinstance(state, dict) else None
        if row_count is None and isinstance(cloud_state, dict):
            row_count = len(cloud_state)

        restore_ok = (
            state_status == 200
            and isinstance(state, dict)
            and isinstance(cloud_state, dict)
            and not state.get("error")
        )
        if restore_ok:
            suite.pass_(
                "Cloud Sync read-only restore",
                f"{row_count or 0} namespaces",
            )
        else:
            detail = state.get("error") or state.get("detail") or repr(state)[:500]
            suite.fail(
                "Cloud Sync read-only restore",
                f"HTTP {state_status}: {detail}",
            )
    except Exception as error:
        suite.fail("Cloud Sync read test", str(error))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Final ThesisOS validation test")
    parser.add_argument("--full", action="store_true", help="run live provider, Radar and Cloud Sync tests")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="ThesisOS API base URL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite = Suite()
    process = None
    try:
        test_static(suite)
        process = start_local_server(args.base_url)
        if not local_server_available(args.base_url):
            suite.fail("Local server startup", "API did not become available; see /tmp/thesisos_final_test_server.log")
        else:
            suite.pass_("Local server startup", args.base_url)
            test_runtime(suite, args.base_url)
            if args.full:
                test_full(suite, args.base_url)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return suite.print_report("FULL" if args.full else "QUICK")


if __name__ == "__main__":
    raise SystemExit(main())
