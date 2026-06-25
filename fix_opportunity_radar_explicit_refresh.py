#!/usr/bin/env python3
"""Patch ThesisOS so Opportunity Radar runs only by explicit user action.

Run from the ThesisOS project root:
    python3 fix_opportunity_radar_explicit_refresh.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
SERVER = ROOT / "server.py"
TEST = ROOT / "test_supabase_auth.py"


def fail(message: str) -> None:
    raise SystemExit(f"[FAIL] {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"[SKIP] {label} já aplicado")
        return text
    if old not in text:
        fail(f"Bloco não encontrado: {label}")
    return text.replace(old, new, 1)


def validate_inline_js(index_text: str) -> None:
    scripts = []
    position = 0
    while True:
        start = index_text.find("<script>", position)
        if start < 0:
            break
        end = index_text.find("</script>", start)
        if end < 0:
            fail("Existe um bloco <script> sem fecho.")
        scripts.append(index_text[start + len("<script>"):end])
        position = end + len("</script>")

    if len(scripts) != 1:
        print(f"[WARN] Encontrados {len(scripts)} scripts inline; validação JS ignorada")
        return

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(scripts[0])
        js_path = Path(handle.name)

    try:
        result = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            fail((result.stderr or result.stdout).strip())
        print("[PASS] JavaScript inline válido")
    except FileNotFoundError:
        print("[WARN] Node.js indisponível; validação JavaScript ignorada")
    finally:
        js_path.unlink(missing_ok=True)


def patch_index(text: str) -> str:
    # Remove automatic Radar execution on page open.
    automatic_block = '''    if(id==="radar" && !window.radarHasLoaded){
      window.radarHasLoaded=true;
      setTimeout(()=>loadOpportunityRadar(),120);
    }
'''
    if automatic_block in text:
        text = text.replace(automatic_block, "", 1)
    elif "window.radarHasLoaded" in text:
        text, count = re.subn(
            r'\s*if\(id==="radar"\s*&&\s*!window\.radarHasLoaded\)\{.*?\}\n',
            "\n",
            text,
            count=1,
            flags=re.S,
        )
        if count != 1:
            fail("Não foi possível remover a execução automática do Radar.")
    else:
        print("[SKIP] execução automática do Radar já removida")

    # Add a deterministic empty-state reset.
    loader_marker = '  async function loadOpportunityRadar(force=false){'
    reset_function = '''  function resetOpportunityRadarView(){
    window.lastRadarData=null;
    const container=$("#dynamicRadarCards");
    const empty=$("#dynamicRadarEmpty");
    if(container)container.innerHTML="";
    if(empty){
      empty.textContent="Ainda não existem resultados. Carrega em Executar Radar.";
      empty.style.display="block";
    }
    if($("#radarUpdated"))$("#radarUpdated").textContent="Ainda não executado";
    if($("#radarCoverage"))$("#radarCoverage").textContent="0 analisados";
    if($("#radarStatusTitle"))$("#radarStatusTitle").textContent="Radar pronto";
    if($("#radarStatusDetail"))$("#radarStatusDetail").textContent="Configura o universo e carrega em Executar Radar para gerar um ranking atual.";
  }

'''
    if "function resetOpportunityRadarView()" not in text:
        if loader_marker not in text:
            fail("Função loadOpportunityRadar não encontrada.")
        text = text.replace(loader_marker, reset_function + loader_marker, 1)
    else:
        print("[SKIP] resetOpportunityRadarView já existe")

    # Explicit button press must send a real cache bypass flag.
    if 'if(force) params.set("refresh",Date.now());' in text:
        text = text.replace(
            'if(force) params.set("refresh",Date.now());',
            'if(force) params.set("refresh","1");',
            1,
        )
    elif 'if(force) params.set("refresh","1");' not in text:
        fail("Parâmetro refresh do frontend não encontrado.")

    old_updated = '$("#radarUpdated").textContent=`Atualizado ${formatAssetDate(data.generated_at)}${data.cached?" · cache":""}`;'
    new_updated = '$("#radarUpdated").textContent=`Atualizado ${formatAssetDate(data.generated_at)}${data.cached?" · cache":force?" · atualização forçada":""}`;'
    if old_updated in text:
        text = text.replace(old_updated, new_updated, 1)
    elif new_updated not in text:
        fail("Indicador de atualização do Radar não encontrado.")

    # Initial render is empty. Auth/cloud restore may populate it later.
    event_anchor = '  $("#runRadarBtn")?.addEventListener("click",()=>loadOpportunityRadar(true));'
    initial_reset = event_anchor + '\n  resetOpportunityRadarView();'
    if initial_reset not in text:
        if event_anchor not in text:
            fail("Listener do botão Executar Radar não encontrado.")
        text = text.replace(event_anchor, initial_reset, 1)

    # Clear stale visible results when cloud/local user state has no last_radar.
    old_cloud = 'const radar=dashboardRead(DASHBOARD_RADAR_KEY,null);if(radar){window.lastRadarData=radar;renderRadarResults(radar);}'
    new_cloud = 'const radar=dashboardRead(DASHBOARD_RADAR_KEY,null);if(radar){window.lastRadarData=radar;renderRadarResults(radar);if($("#radarUpdated"))$("#radarUpdated").textContent=`Guardado ${formatAssetDate(radar.generated_at)}`;}else{resetOpportunityRadarView();}'
    text = replace_once(text, old_cloud, new_cloud, "limpeza do Radar no cloudRenderAll")

    return text


def patch_server(text: str) -> str:
    old_signature = '''def build_opportunity_radar(
    universe: str = "core_us",
    symbols_value: str | None = None,
    limit: int = 8,
) -> dict:'''
    new_signature = '''def build_opportunity_radar(
    universe: str = "core_us",
    symbols_value: str | None = None,
    limit: int = 8,
    force_refresh: bool = False,
) -> dict:'''
    text = replace_once(text, old_signature, new_signature, "assinatura build_opportunity_radar")

    old_cache = '''    cached = RADAR_CACHE.get(cache_key)

    if cached:
        age = time.time() - cached["created_at"]
        if age < RADAR_CACHE_TTL_SECONDS:
            return {**cached["data"], "cached": True}
'''
    new_cache = '''    cached = RADAR_CACHE.get(cache_key)

    if cached and not force_refresh:
        age = time.time() - cached["created_at"]
        if age < RADAR_CACHE_TTL_SECONDS:
            return {**cached["data"], "cached": True}
'''
    text = replace_once(text, old_cache, new_cache, "bypass do RADAR_CACHE")

    old_query = '''        universe = query.get("universe", ["core_us"])[0]
        symbols_value = query.get("symbols", [None])[0]

        try:
            limit = int(query.get("limit", ["8"])[0])
'''
    new_query = '''        universe = query.get("universe", ["core_us"])[0]
        symbols_value = query.get("symbols", [None])[0]
        refresh_value = str(query.get("refresh", ["0"])[0]).strip().lower()
        force_refresh = refresh_value not in {"", "0", "false", "no", "off"}

        try:
            limit = int(query.get("limit", ["8"])[0])
'''
    text = replace_once(text, old_query, new_query, "parâmetro refresh no endpoint")

    handler_position = text.find("    def handle_radar_request(self, parsed_url) -> None:")
    if handler_position < 0:
        fail("handle_radar_request não encontrado.")
    before = text[:handler_position]
    handler = text[handler_position:]

    old_call = '''                universe=universe,
                symbols_value=symbols_value,
                limit=limit,
            )'''
    new_call = '''                universe=universe,
                symbols_value=symbols_value,
                limit=limit,
                force_refresh=force_refresh,
            )'''
    handler = replace_once(handler, old_call, new_call, "forward de force_refresh")
    return before + handler


def patch_test(text: str) -> str:
    if '"Radar explicit-run behavior"' in text:
        print("[SKIP] testes do Radar já adicionados")
        return text

    backend_anchor = '''    check(
        "Backend Auth routes",
'''
    addition = '''    check(
        "Radar explicit-run behavior",
        'window.radarHasLoaded' not in index
        and "function resetOpportunityRadarView()" in index
        and 'params.set("refresh","1")' in index
        and 'if(cloudSyncSession.authenticated){try{localStorage.setItem(DASHBOARD_RADAR_KEY' in index
        and 'const radar=cloudSyncSession.authenticated?dashboardRead(DASHBOARD_RADAR_KEY,null):null' in index
        and "else{resetOpportunityRadarView();}" in index,
    )
    check(
        "Radar server cache bypass",
        "force_refresh: bool = False" in server
        and "if cached and not force_refresh:" in server
        and "force_refresh=force_refresh" in server,
    )
'''
    if backend_anchor not in text:
        fail("Âncora Backend Auth routes não encontrada no teste.")
    return text.replace(backend_anchor, addition + backend_anchor, 1)


def main() -> int:
    for path in (INDEX, SERVER, TEST):
        if not path.exists():
            fail(f"Ficheiro em falta: {path.name}")

    for source, backup_name in (
        (INDEX, "index_before_radar_explicit_refresh.html"),
        (SERVER, "server_before_radar_explicit_refresh.py"),
        (TEST, "test_supabase_auth_before_radar_explicit_refresh.py"),
    ):
        backup = ROOT / backup_name
        if not backup.exists():
            shutil.copy2(source, backup)
            print(f"[BACKUP] {backup.name}")

    index_text = patch_index(INDEX.read_text(encoding="utf-8"))
    server_text = patch_server(SERVER.read_text(encoding="utf-8"))
    test_text = patch_test(TEST.read_text(encoding="utf-8"))

    compile(server_text, "server.py", "exec")
    compile(test_text, "test_supabase_auth.py", "exec")
    print("[PASS] Python válido")
    validate_inline_js(index_text)

    INDEX.write_text(index_text, encoding="utf-8")
    SERVER.write_text(server_text, encoding="utf-8")
    TEST.write_text(test_text, encoding="utf-8")

    print("[WRITE] index.html")
    print("[WRITE] server.py")
    print("[WRITE] test_supabase_auth.py")
    print()
    print("Próximos passos:")
    print("1. Stop → Run")
    print("2. python3 test_supabase_auth.py")
    print("3. Abrir Opportunity Radar: deve começar vazio")
    print("4. Carregar em Executar Radar: deve indicar atualização forçada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
