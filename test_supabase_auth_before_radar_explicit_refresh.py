#!/usr/bin/env python3
"""Read-only validation for ThesisOS Supabase Auth.

Usage:
    python3 test_supabase_auth.py
    THESISOS_TEST_EMAIL="..." THESISOS_TEST_PASSWORD="..." \
      python3 test_supabase_auth.py --login

The optional login mode signs in through Supabase Auth and reads only the
current user's ThesisOS cloud state. It never writes or deletes data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def request_json(url, method="GET", payload=None, headers=None, timeout=30):
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"error": raw[:500]}
        return error.code, data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
    )
    parser.add_argument("--login", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    failures = 0

    def check(name, condition, detail=""):
        nonlocal failures
        level = "PASS" if condition else "FAIL"
        if not condition:
            failures += 1
        suffix = f" — {detail}" if detail else ""
        print(f"[{level}] {name}{suffix}")

    index = (ROOT / "index.html").read_text(encoding="utf-8")
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    sql = (ROOT / "supabase_auth_migration.sql").read_text(
        encoding="utf-8"
    ).lower()

    check(
        "Frontend Supabase client",
        "@supabase/supabase-js@2" in index,
    )
    check(
        "Frontend account UI",
        'id="cloudSyncEmail"' in index
        and 'id="cloudSyncSignupBtn"' in index
        and 'id="cloudSyncResetBtn"' in index,
    )
    check(
        "Account-scoped local cache",
        'CLOUD_LOCAL_OWNER_KEY="thesisos_cloud_local_owner_v1"' in index
        and "cloudPrepareLocalStateForUser" in index
        and "cloudClearSyncedLocalState" in index
        and "cloudSaveUserCache" in index,
    )
    check(
        "Empty-account visual state",
        'placeholder="Ex.: 30"' in index
        and 'placeholder="Ex.: 150"' in index
        and 'function emptyInvestorPolicy(){return {age:null,planning_capital:null,horizon:null' in index
        and 'monthly_contribution:null' in index
        and 'value===null||value===undefined?"":value' in index
        and "function policyFormHasData()" in index
        and "if(!stored&&!policyFormHasData())" in index,
    )
    check(
        "Saved-policy gate",
        'function hasSavedInvestorPolicy()' in index
        and 'if(!hasSavedInvestorPolicy()){renderPortfolioConstructionEmptyState();return;}' in index
        and 'Guarda uma Investment Policy antes de calcular limites e tamanho de posição.' in index
        and 'updated_at:new Date().toISOString()' in index
        and 'updated_at:null' in index,
    )
    check(
        "Password recovery diagnostics",
        "Supabase password recovery failed" in index
        and "error?.status===429" in index,
    )
    check(
        "Backend Auth routes",
        all(
            route in server
            for route in (
                "/api/auth/config",
                "/api/auth/session",
                "/api/user/state",
                "/api/auth/migrate-legacy",
            )
        ),
    )
    check(
        "Per-user table",
        "create table if not exists public.thesisos_user_state" in sql,
    )
    check(
        "RLS user isolation",
        "to authenticated" in sql
        and "(select auth.uid()) = user_id" in sql
        and "revoke all on table public.thesisos_user_state from anon" in sql,
    )
    check(
        "Secret not embedded",
        "sb_secret_" not in index
        and "SUPABASE_SECRET_KEY" not in index,
    )

    status, config = request_json(f"{base}/api/auth/config")
    configured = (
        status == 200
        and config.get("status") == "ok"
        and config.get("configured") is True
        and bool(config.get("url"))
        and bool(config.get("publishable_key"))
    )
    check(
        "Auth configuration endpoint",
        configured,
        (
            "configured"
            if configured
            else f"HTTP {status}: {config.get('missing') or config}"
        ),
    )

    status, _ = request_json(f"{base}/api/user/state")
    check(
        "Anonymous state access denied",
        status == 401,
        f"HTTP {status}",
    )

    status, _ = request_json(
        f"{base}/api/auth/migrate-legacy",
        method="POST",
        payload={"password": "not-used"},
    )
    check(
        "Anonymous migration denied",
        status == 401,
        f"HTTP {status}",
    )

    if args.login:
        email = os.getenv("THESISOS_TEST_EMAIL", "").strip()
        password = os.getenv("THESISOS_TEST_PASSWORD", "")
        if not email or not password:
            check(
                "Authenticated read",
                False,
                "Set THESISOS_TEST_EMAIL and THESISOS_TEST_PASSWORD",
            )
        elif configured:
            token_url = (
                config["url"].rstrip("/")
                + "/auth/v1/token?"
                + urlencode({"grant_type": "password"})
            )
            status, login = request_json(
                token_url,
                method="POST",
                payload={"email": email, "password": password},
                headers={"apikey": config["publishable_key"]},
            )
            token = login.get("access_token")
            check(
                "Supabase email/password login",
                status == 200 and bool(token),
                f"HTTP {status}",
            )
            if token:
                auth_headers = {"Authorization": f"Bearer {token}"}
                status, session = request_json(
                    f"{base}/api/auth/session",
                    headers=auth_headers,
                )
                check(
                    "Backend session validation",
                    status == 200 and session.get("authenticated") is True,
                    f"HTTP {status}",
                )
                status, state = request_json(
                    f"{base}/api/user/state",
                    headers=auth_headers,
                )
                check(
                    "Read-only per-user restore",
                    status == 200 and isinstance(state.get("state"), dict),
                    (
                        f"{state.get('row_count', 0)} namespaces"
                        if status == 200
                        else f"HTTP {status}"
                    ),
                )

    print("-" * 64)
    if failures:
        print(f"Supabase Auth validation failed: {failures} issue(s).")
        return 1
    print("Supabase Auth validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
