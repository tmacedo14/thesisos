#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
TEST = ROOT / "test_supabase_auth.py"


def fail(message: str) -> None:
    raise SystemExit(f"[FAIL] {message}")


def main() -> int:
    if not INDEX.exists() or not TEST.exists():
        fail("index.html ou test_supabase_auth.py em falta.")

    index = INDEX.read_text(encoding="utf-8")
    test = TEST.read_text(encoding="utf-8")

    for source, backup_name in (
        (INDEX, "index_before_radar_frontend_hotfix.html"),
        (TEST, "test_supabase_auth_before_radar_frontend_hotfix.py"),
    ):
        backup = ROOT / backup_name
        if not backup.exists():
            shutil.copy2(source, backup)
            print(f"[BACKUP] {backup.name}")

    before = {
        "legacy_marker_absent": "window.radarHasLoaded" not in index,
        "reset_function": "function resetOpportunityRadarView()" in index,
        "refresh_parameter": 'params.set("refresh","1")' in index,
        "cloud_empty_reset": "resetOpportunityRadarView();" in index
        and "DASHBOARD_RADAR_KEY" in index,
    }
    print("[INFO] Estado anterior:", before)

    index, removed_blocks = re.subn(
        r'\n\s*if\s*\(\s*id\s*===\s*"radar"\s*&&\s*!window\.radarHasLoaded\s*\)'
        r'\s*\{\s*window\.radarHasLoaded\s*=\s*true\s*;'
        r'\s*setTimeout\(\(\)=>loadOpportunityRadar\(\),\s*120\)\s*;?\s*\}',
        "",
        index,
        count=0,
        flags=re.S,
    )

    index = re.sub(
        r'\s*window\.radarHasLoaded\s*=\s*(?:true|false)\s*;\s*',
        "\n",
        index,
    )

    index = re.sub(
        r'params\.set\("refresh",\s*(?:Date\.now\(\)|"1"|1)\s*\)',
        'params.set("refresh","1")',
        index,
        count=1,
    )

    if "function resetOpportunityRadarView()" not in index:
        fail("A função resetOpportunityRadarView() não existe. Reaplica primeiro o patch principal do Radar.")

    radar_segment_pattern = (
        r'const radar=dashboardRead\(DASHBOARD_RADAR_KEY,null\);'
        r'if\(radar\)\{.*?\}'
        r'(?:else\{.*?\})?'
        r'const evidence='
    )
    radar_segment_replacement = (
        'const radar=dashboardRead(DASHBOARD_RADAR_KEY,null);'
        'if(radar){window.lastRadarData=radar;renderRadarResults(radar);'
        'if($("#radarUpdated"))$("#radarUpdated").textContent=`Guardado ${formatAssetDate(radar.generated_at)}`;}'
        'else{resetOpportunityRadarView();}'
        'const evidence='
    )
    index, cloud_replacements = re.subn(
        radar_segment_pattern,
        radar_segment_replacement,
        index,
        count=1,
        flags=re.S,
    )
    if cloud_replacements != 1:
        fail("Não foi possível normalizar o estado Radar dentro de cloudRenderAll().")

    listener = '$("#runRadarBtn")?.addEventListener("click",()=>loadOpportunityRadar(true));'
    if listener not in index:
        fail("Listener do botão Executar Radar não encontrado.")
    if listener + "\n  resetOpportunityRadarView();" not in index:
        index = index.replace(
            listener,
            listener + "\n  resetOpportunityRadarView();",
            1,
        )

    new_check = "\n".join([
        '    check(',
        '        "Radar explicit-run behavior",',
        '        \'if(id==="radar" && !window.radarHasLoaded)\' not in index',
        '        and "function resetOpportunityRadarView()" in index',
        '        and \'params.set("refresh","1")\' in index',
        '        and "else{resetOpportunityRadarView();}" in index',
        '        and \'$("#runRadarBtn")?.addEventListener("click",()=>loadOpportunityRadar(true));\' in index,',
        '    )',
        '',
    ])

    test, check_replacements = re.subn(
        r'    check\(\n'
        r'        "Radar explicit-run behavior",\n'
        r'.*?'
        r'    \)\n',
        new_check,
        test,
        count=1,
        flags=re.S,
    )
    if check_replacements != 1:
        fail("Bloco de teste Radar explicit-run behavior não encontrado.")

    required = {
        "automatic_block_absent":
            'if(id==="radar" && !window.radarHasLoaded)' not in index,
        "reset_function":
            "function resetOpportunityRadarView()" in index,
        "refresh_parameter":
            'params.set("refresh","1")' in index,
        "cloud_empty_reset":
            "else{resetOpportunityRadarView();}" in index,
        "explicit_button":
            '$("#runRadarBtn")?.addEventListener("click",()=>loadOpportunityRadar(true));' in index,
    }
    missing = [name for name, passed in required.items() if not passed]
    if missing:
        fail("Marcadores finais em falta: " + ", ".join(missing))

    compile(test, "test_supabase_auth.py", "exec")
    INDEX.write_text(index, encoding="utf-8")
    TEST.write_text(test, encoding="utf-8")

    print(f"[INFO] Blocos automáticos removidos: {removed_blocks}")
    print("[WRITE] index.html")
    print("[WRITE] test_supabase_auth.py")
    print("[PASS] Radar só executa por ação explícita")
    print("[PASS] Estado visual vazio normalizado")
    print()
    print("Agora executa:")
    print("  python3 test_supabase_auth.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
