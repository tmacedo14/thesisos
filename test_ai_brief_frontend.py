#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
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


def function_block(text: str, name: str) -> str:
    match = re.search(rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\(", text)
    if not match:
        return ""

    paren = text.find("(", match.start())
    depth = 0
    state = "normal"
    escaped = False
    index = paren
    body_start = -1

    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""

        if state == "normal":
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "`":
                state = "template"
            elif char == "/" and nxt == "/":
                state = "line"
                index += 1
            elif char == "/" and nxt == "*":
                state = "block"
                index += 1
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    body_start = text.find("{", index + 1)
                    break
        elif state in {"single", "double", "template"}:
            quote = {"single": "'", "double": '"', "template": "`"}[state]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                state = "normal"
        elif state == "line" and char == "\n":
            state = "normal"
        elif state == "block" and char == "*" and nxt == "/":
            state = "normal"
            index += 1
        index += 1

    if body_start < 0:
        return ""

    depth = 0
    state = "normal"
    escaped = False
    index = body_start

    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""

        if state == "normal":
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "`":
                state = "template"
            elif char == "/" and nxt == "/":
                state = "line"
                index += 1
            elif char == "/" and nxt == "*":
                state = "block"
                index += 1
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[match.start():index + 1]
        elif state in {"single", "double", "template"}:
            quote = {"single": "'", "double": '"', "template": "`"}[state]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                state = "normal"
        elif state == "line" and char == "\n":
            state = "normal"
        elif state == "block" and char == "*" and nxt == "/":
            state = "normal"
            index += 1
        index += 1

    return ""


def main() -> int:
    text = INDEX.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    check(text.count('id="analysisAiBriefBtn"') == 1, "Explicit AI Brief action button")
    check(text.count('id="aiBriefCard"') == 1, "Dedicated AI Brief card")
    check(text.count('id="aiBriefContent"') == 1, "Dedicated safe content container")
    check(text.count("/api/ai-brief/config") == 1, "Public config route used once")
    check(text.count('"/api/ai-brief"') == 1, "Authenticated route used once")

    generate = function_block(text, "generateAiInvestmentBrief")
    render = function_block(text, "renderAiInvestmentBrief")
    load = function_block(text, "loadUnifiedAnalysis")
    unified_render = function_block(text, "renderUnifiedAnalysis")

    check(bool(generate), "Generate function present")
    check(bool(render), "Render function present")
    check("cloudRequest(" in generate and "true\n      );" in generate, "Authenticated cloudRequest reused")
    check("Authorization" not in generate, "No duplicate token handling")
    check(all(field in generate for field in ("identifier", "base_currency", "exchange", "ticker")), "Minimal runtime request fields")
    check("grounding" not in generate.lower(), "Client grounding not submitted")
    check("JSON.stringify(requestBody)" in generate, "Only normalized request body serialized")
    check("AbortController" in generate and "signal:controller.signal" in generate, "AbortController wired")
    check("resetAiInvestmentBrief({abort:true})" in load, "Brief invalidated on new analysis")
    check("generateAiInvestmentBrief" not in load and "generateAiInvestmentBrief" not in unified_render, "No automatic AI generation")
    check(text.count('$("#analysisAiBriefBtn")?.addEventListener') == 1, "Explicit generation listener")
    check("innerHTML" not in render and "insertAdjacentHTML" not in render, "Provider content never rendered as HTML")
    check("textContent" in render and "createElement" in text, "Safe DOM APIs used")
    check("localStorage" not in generate and "localStorage" not in render, "Brief not persisted locally")
    check("cloudSchedulePush" not in generate and "cloudSchedulePush" not in render, "Brief not persisted to cloud state")

    statuses = {
        "ready",
        "partial",
        "insufficient_data",
        "provider_unavailable",
        "disabled",
        "generation_failed",
    }
    for status in sorted(statuses):
        check(status in text, f"Frontend state {status}")

    check("api_key" not in generate.lower(), "No API-key material in request")
    check("THESISOS_AI_BRIEF_ENABLED" not in text, "Server feature flag not embedded")
    check("python test_ai_brief_frontend.py" in workflow, "Frontend test included in CI")
    check("test_thesisos_final.py --full" not in workflow, "FULL remains outside CI")

    fixtures = {
        "ready": {"status": "ready", "decision": {"action": "hold", "confidence": 78}},
        "partial": {"status": "partial", "decision": {"action": "watch", "confidence": 54}},
        "insufficient_data": {"status": "insufficient_data", "decision": {"action": "unavailable", "confidence": 0}},
        "disabled": {"status": "disabled", "decision": {"action": "unavailable", "confidence": 0}},
        "provider_unavailable": {"status": "provider_unavailable", "decision": {"action": "unavailable", "confidence": 0}},
        "generation_failed": {"status": "generation_failed", "decision": {"action": "unavailable", "confidence": 0}},
    }
    for status, payload in fixtures.items():
        check(payload["status"] == status and bool(payload["decision"]["action"]), f"Deterministic fixture {status}")

    print("-" * 64)
    print(f"Result: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
