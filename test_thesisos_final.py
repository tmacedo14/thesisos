#!/usr/bin/env python3
"""ThesisOS final demonstration and validation test.

Quick mode validates the repository, JavaScript, Python, local API and
Supabase configuration status without writing data.

Full mode also executes real AAPL analysis, valuation, evidence and Radar.

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
        "supabase_auth_migration.sql",
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
    schema = read_text(ROOT / "supabase_auth_migration.sql").lower()
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
        "/api/auth/config",
        "/api/auth/session",
        "/api/user/state",
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
        "create table if not exists public.thesisos_user_state",
        "payload jsonb not null",
        "primary key (user_id, namespace)",
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
        status, data = json_request(
            endpoint(base_url, "/api/auth/config"),
            timeout=10,
        )
        configured = (
            status == 200
            and data.get("status") == "ok"
            and data.get("configured") is True
            and bool(data.get("url"))
            and bool(data.get("publishable_key"))
        )
        suite.check(
            "Supabase Auth configuration",
            configured,
            "configured" if configured else f"HTTP {status}: {data}",
        )
    except Exception as error:
        suite.fail("Supabase Auth configuration", str(error))


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



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Final ThesisOS validation test")
    parser.add_argument("--full", action="store_true", help="run live provider, analysis and Radar tests")
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
