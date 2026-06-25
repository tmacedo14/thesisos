#!/usr/bin/env python3
"""ThesisOS Auth empty-state V3 hotfix.

Applies the missing HTML changes after the V2 package:
- removes the remaining personal defaults from Investor Policy;
- adds neutral placeholder options to policy selects;
- validates the markers used by test_supabase_auth.py.

Run from the ThesisOS project root:
    python3 fix_auth_empty_state_v3.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
BACKUP = ROOT / "index_before_auth_empty_state_v3.html"


def replace_input_value(text: str, element_id: str, placeholder: str) -> str:
    pattern = rf'(<input id="{re.escape(element_id)}"[^>]*?)\s+value="[^"]*"'
    replacement = rf'\1 placeholder="{placeholder}"'
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count == 0:
        # Accept an already-correct field.
        marker = f'id="{element_id}"'
        if marker not in text:
            raise RuntimeError(f"Campo não encontrado: {element_id}")
        if f'placeholder="{placeholder}"' not in text[
            max(0, text.find(marker) - 100): text.find(marker) + 300
        ]:
            raise RuntimeError(f"Não foi possível neutralizar: {element_id}")
        return text
    return updated


def add_blank_option(text: str, element_id: str) -> str:
    marker = f'<select id="{element_id}">'
    replacement = (
        marker
        + '<option value="" selected disabled>Seleciona…</option>'
    )
    if replacement in text:
        return text
    if marker not in text:
        raise RuntimeError(f"Select não encontrado: {element_id}")
    return text.replace(marker, replacement, 1)


def main() -> int:
    if not INDEX.exists():
        raise SystemExit("[FAIL] index.html não encontrado.")

    text = INDEX.read_text(encoding="utf-8")

    if not BACKUP.exists():
        shutil.copy2(INDEX, BACKUP)
        print(f"[BACKUP] {BACKUP.name}")

    numeric_fields = {
        "policyAge": "Ex.: 30",
        "policyPlanningCapital": "Ex.: 1000",
        "policyMonthlyContribution": "Ex.: 150",
        "policyDrawdown": "Ex.: 30",
    }
    for element_id, placeholder in numeric_fields.items():
        text = replace_input_value(text, element_id, placeholder)

    select_fields = (
        "policyHorizon",
        "policyGoal",
        "policyLiquidity",
        "policyIncomeStability",
        "policyDropReaction",
        "policyEmergencyFund",
    )
    for element_id in select_fields:
        text = add_blank_option(text, element_id)

    required_markers = (
        'placeholder="Ex.: 30"',
        'placeholder="Ex.: 150"',
        'function emptyInvestorPolicy()',
        'function policyFormHasData()',
        'if(!stored&&!policyFormHasData())',
        'function hasSavedInvestorPolicy()',
        'renderPortfolioConstructionEmptyState',
    )
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        raise SystemExit(
            "[FAIL] Marcadores em falta após a correção: "
            + ", ".join(missing)
        )

    INDEX.write_text(text, encoding="utf-8")
    print("[WRITE] index.html")
    print("[PASS] Defaults pessoais removidos")
    print("[PASS] Selects com estado neutro")
    print()
    print("Executa agora:")
    print("  python3 test_supabase_auth.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
