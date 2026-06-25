#!/usr/bin/env python3
"""ThesisOS empty-state V4 hotfix.

Fixes the remaining 33/100 preview on an empty Investor Policy form.
The cause is that fillPolicyForm(emptyInvestorPolicy()) writes numeric zeroes
into the inputs, so policyFormHasData() treats the form as a draft.

Run from the ThesisOS project root:
    python3 fix_auth_empty_state_v4.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
TEST = ROOT / "test_supabase_auth.py"
INDEX_BACKUP = ROOT / "index_before_auth_empty_state_v4.html"
TEST_BACKUP = ROOT / "test_supabase_auth_before_empty_state_v4.py"

OLD_EMPTY = (
    '  function emptyInvestorPolicy(){return {age:0,planning_capital:0,'
    'horizon:0,goal:"",liquidity:"",income_stability:"",drop_reaction:"",'
    'emergency_fund:"",monthly_contribution:0,drawdown:0,max_stock_weight:0,'
    'max_etf_weight:0,initial_position_weight:0,max_speculative:0,max_sector:0,'
    'max_currency:0,min_cash:0,rebalance_threshold:0,target_allocation:{core:0,'
    'quality:0,dividend:0,speculative:0,cash:0},score:null,label:"Por configurar",'
    'updated_at:null};}'
)

NEW_EMPTY = (
    '  function emptyInvestorPolicy(){return {age:null,planning_capital:null,'
    'horizon:null,goal:"",liquidity:"",income_stability:"",drop_reaction:"",'
    'emergency_fund:"",monthly_contribution:null,drawdown:null,'
    'max_stock_weight:null,max_etf_weight:null,initial_position_weight:null,'
    'max_speculative:null,max_sector:null,max_currency:null,min_cash:null,'
    'rebalance_threshold:null,target_allocation:{core:null,quality:null,'
    'dividend:null,speculative:null,cash:null},score:null,label:"Por configurar",'
    'updated_at:null};}'
)

OLD_FILL = (
    '    Object.entries(fields).forEach(([id,value])=>{if($("#"+id))'
    '$("#"+id).value=value;});investorPolicyFormLoaded=true;'
)

NEW_FILL = (
    '    Object.entries(fields).forEach(([id,value])=>{if($("#"+id))'
    '$("#"+id).value=value===null||value===undefined?"":value;});'
    'investorPolicyFormLoaded=true;'
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"[SKIP] {label} já aplicado")
        return text
    if old not in text:
        raise SystemExit(f"[FAIL] Bloco não encontrado: {label}")
    return text.replace(old, new, 1)


def main() -> int:
    if not INDEX.exists() or not TEST.exists():
        raise SystemExit("[FAIL] index.html ou test_supabase_auth.py em falta.")

    index = INDEX.read_text(encoding="utf-8")
    test = TEST.read_text(encoding="utf-8")

    if not INDEX_BACKUP.exists():
        shutil.copy2(INDEX, INDEX_BACKUP)
        print(f"[BACKUP] {INDEX_BACKUP.name}")
    if not TEST_BACKUP.exists():
        shutil.copy2(TEST, TEST_BACKUP)
        print(f"[BACKUP] {TEST_BACKUP.name}")

    index = replace_once(index, OLD_EMPTY, NEW_EMPTY, "emptyInvestorPolicy null state")
    index = replace_once(index, OLD_FILL, NEW_FILL, "fillPolicyForm null handling")

    old_check = (
        '        and \'function emptyInvestorPolicy()\' in index\n'
        '        and "function policyFormHasData()" in index\n'
        '        and "if(!stored&&!policyFormHasData())" in index,\n'
    )
    new_check = (
        '        and \'function emptyInvestorPolicy(){return {age:null,planning_capital:null,horizon:null\' in index\n'
        '        and \'monthly_contribution:null\' in index\n'
        '        and \'value===null||value===undefined?"":value\' in index\n'
        '        and "function policyFormHasData()" in index\n'
        '        and "if(!stored&&!policyFormHasData())" in index,\n'
    )
    test = replace_once(test, old_check, new_check, "runtime-empty-state test markers")

    required = (
        'function emptyInvestorPolicy(){return {age:null',
        'monthly_contribution:null',
        'value===null||value===undefined?"":value',
        'if(!stored&&!policyFormHasData())',
        '"Saved-policy gate"',
    )
    missing = [marker for marker in required if marker not in index + test]
    if missing:
        raise SystemExit("[FAIL] Marcadores em falta: " + ", ".join(missing))

    compile(test, "test_supabase_auth.py", "exec")
    INDEX.write_text(index, encoding="utf-8")
    TEST.write_text(test, encoding="utf-8")

    print("[WRITE] index.html")
    print("[WRITE] test_supabase_auth.py")
    print("[PASS] Empty form no longer contains zero values")
    print()
    print("Executa agora:")
    print("  python3 test_supabase_auth.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
