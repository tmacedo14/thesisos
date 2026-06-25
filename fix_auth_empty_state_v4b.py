#!/usr/bin/env python3
# ThesisOS Auth empty-state V4B hotfix

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
TEST = ROOT / "test_supabase_auth.py"

EMPTY_FUNCTION = (
    '  function emptyInvestorPolicy(){return {age:null,planning_capital:null,'
    'horizon:null,goal:"",liquidity:"",income_stability:"",drop_reaction:"",'
    'emergency_fund:"",monthly_contribution:null,drawdown:null,'
    'max_stock_weight:null,max_etf_weight:null,initial_position_weight:null,'
    'max_speculative:null,max_sector:null,max_currency:null,min_cash:null,'
    'rebalance_threshold:null,target_allocation:{core:null,quality:null,'
    'dividend:null,speculative:null,cash:null},score:null,'
    'label:"Por configurar",updated_at:null};}'
)

FILL_LINE = (
    '    Object.entries(fields).forEach(([id,value])=>{if($("#"+id))'
    '$("#"+id).value=value===null||value===undefined?"":value;});'
    'investorPolicyFormLoaded=true;'
)

STRICT_CHECK = "\n".join([
    '    check(',
    '        "Empty-account visual state",',
    '        \'placeholder="Ex.: 30"\' in index',
    '        and \'placeholder="Ex.: 150"\' in index',
    '        and \'function emptyInvestorPolicy(){return {age:null,planning_capital:null,horizon:null\' in index',
    '        and \'monthly_contribution:null\' in index',
    '        and \'value===null||value===undefined?"":value\' in index',
    '        and "function policyFormHasData()" in index',
    '        and "if(!stored&&!policyFormHasData())" in index,',
    '    )',
    '',
])


def main() -> int:
    if not INDEX.exists() or not TEST.exists():
        raise SystemExit("[FAIL] index.html ou test_supabase_auth.py em falta.")

    index = INDEX.read_text(encoding="utf-8")
    test = TEST.read_text(encoding="utf-8")

    index_backup = ROOT / "index_before_auth_empty_state_v4b.html"
    test_backup = ROOT / "test_supabase_auth_before_empty_state_v4b.py"
    if not index_backup.exists():
        shutil.copy2(INDEX, index_backup)
        print(f"[BACKUP] {index_backup.name}")
    if not test_backup.exists():
        shutil.copy2(TEST, test_backup)
        print(f"[BACKUP] {test_backup.name}")

    index, empty_count = re.subn(
        r'  function emptyInvestorPolicy\(\)\{return \{.*?\};\}',
        EMPTY_FUNCTION,
        index,
        count=1,
        flags=re.S,
    )
    if empty_count != 1:
        raise SystemExit("[FAIL] Não foi possível substituir emptyInvestorPolicy().")

    index, fill_count = re.subn(
        r'    Object\.entries\(fields\)\.forEach\(\(\[id,value\]\)=>\{'
        r'if\(\$\("#"\+id\)\)\$\("#"\+id\)\.value=.*?\}\);'
        r'investorPolicyFormLoaded=true;',
        FILL_LINE,
        index,
        count=1,
        flags=re.S,
    )
    if fill_count != 1:
        raise SystemExit("[FAIL] Não foi possível corrigir fillPolicyForm().")

    test, check_count = re.subn(
        r'    check\(\n'
        r'        "Empty-account visual state",\n'
        r'.*?'
        r'    \)\n',
        STRICT_CHECK,
        test,
        count=1,
        flags=re.S,
    )
    if check_count == 0:
        print("[WARN] O bloco de teste não foi substituído; o frontend foi corrigido na mesma.")

    required = (
        'function emptyInvestorPolicy(){return {age:null',
        'monthly_contribution:null',
        'value===null||value===undefined?"":value',
        'if(!stored&&!policyFormHasData())',
    )
    missing = [marker for marker in required if marker not in index]
    if missing:
        raise SystemExit("[FAIL] Marcadores em falta: " + ", ".join(missing))

    compile(test, "test_supabase_auth.py", "exec")
    INDEX.write_text(index, encoding="utf-8")
    TEST.write_text(test, encoding="utf-8")

    print("[WRITE] index.html")
    print("[WRITE] test_supabase_auth.py")
    print("[PASS] Estado vazio passa a usar null em vez de zero")
    print("[PASS] fillPolicyForm deixa campos realmente vazios")
    print()
    print("Agora inicia o servidor com Stop → Run e executa:")
    print("  curl -sS -o /dev/null -w 'HTTP %{http_code}\\n' http://localhost:3000/")
    print("  python3 test_supabase_auth.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
