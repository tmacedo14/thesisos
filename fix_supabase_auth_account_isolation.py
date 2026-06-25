#!/usr/bin/env python3
"""Fix local account isolation and improve Supabase recovery diagnostics.

Run from the ThesisOS project root after add_supabase_auth.py.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
TEST = ROOT / "test_supabase_auth.py"


def fail(message: str) -> None:
    raise SystemExit(f"[FAIL] {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        fail(f"Could not locate {label}. The current index.html may differ.")
    return text.replace(old, new, 1)


def validate_js(index_text: str) -> None:
    scripts = []
    position = 0
    while True:
        start = index_text.find("<script>", position)
        if start < 0:
            break
        end = index_text.find("</script>", start)
        if end < 0:
            fail("Unclosed inline <script> block")
        scripts.append(index_text[start + len("<script>"):end])
        position = end + len("</script>")

    if len(scripts) != 1:
        fail(f"Expected one inline script, found {len(scripts)}")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as handle:
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
        print("[PASS] inline JavaScript syntax")
    except FileNotFoundError:
        print("[WARN] Node.js unavailable; JavaScript check skipped")
    finally:
        js_path.unlink(missing_ok=True)


def patch_index(text: str) -> str:
    if 'const CLOUD_LOCAL_OWNER_KEY="thesisos_cloud_local_owner_v1";' in text:
        print("[SKIP] Browser account isolation already installed")
        return text

    text = replace_once(
        text,
        '  const CLOUD_AUTOSYNC_KEY="thesisos_cloud_autosync_user_v1";\n'
        '  const CLOUD_LAST_SYNC_KEY="thesisos_cloud_last_sync_user_v1";',
        '  const CLOUD_AUTOSYNC_KEY="thesisos_cloud_autosync_user_v1";\n'
        '  const CLOUD_LAST_SYNC_KEY="thesisos_cloud_last_sync_user_v1";\n'
        '  const CLOUD_LOCAL_OWNER_KEY="thesisos_cloud_local_owner_v1";\n'
        '  const CLOUD_USER_CACHE_PREFIX="thesisos_cloud_user_cache_v1:";',
        "Cloud Sync storage constants",
    )

    text = replace_once(
        text,
        '  function cloudLocalHasData(){const state=cloudCollectState();return Object.values(state).some(value=>Array.isArray(value)?value.length>0:value&&typeof value==="object"?Object.keys(value).length>0:value!==null&&value!=="");}\n'
        '  function cloudSetMessage',
        '  function cloudLocalHasData(){const state=cloudCollectState();return Object.values(state).some(value=>Array.isArray(value)?value.length>0:value&&typeof value==="object"?Object.keys(value).length>0:value!==null&&value!=="");}\n'
        '  function cloudWriteStateToLocal(state,{touchSync=false}={}){cloudApplyingState=true;try{for(const [namespace,key] of Object.entries(cloudStorageMap())){if(!Object.prototype.hasOwnProperty.call(state,namespace))continue;const value=state[namespace];if(value===null||value===undefined)localStorage.removeItem(key);else localStorage.setItem(key,JSON.stringify(value));}if(touchSync)localStorage.setItem(CLOUD_LAST_SYNC_KEY,new Date().toISOString());}finally{cloudApplyingState=false;}cloudRenderAll();cloudUpdateUi();}\n'
        '  function cloudClearSyncedLocalState(){cloudApplyingState=true;try{for(const key of Object.values(cloudStorageMap()))localStorage.removeItem(key);localStorage.removeItem(CLOUD_LAST_SYNC_KEY);localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");}finally{cloudApplyingState=false;}cloudRenderAll();cloudUpdateUi();}\n'
        '  function cloudSaveUserCache(userId){if(!userId)return;try{localStorage.setItem(CLOUD_USER_CACHE_PREFIX+userId,JSON.stringify(cloudCollectState()));}catch(error){console.warn("Não foi possível guardar a cache local da conta.",error);}}\n'
        '  function cloudLoadUserCache(userId){if(!userId)return false;const raw=localStorage.getItem(CLOUD_USER_CACHE_PREFIX+userId);if(!raw)return false;try{const state=JSON.parse(raw);if(!state||typeof state!=="object")return false;cloudWriteStateToLocal(state);return cloudLocalHasData();}catch(error){console.warn("Cache local inválida para a conta.",error);return false;}}\n'
        '  function cloudPrepareLocalStateForUser(userId){if(!userId)return;const owner=localStorage.getItem(CLOUD_LOCAL_OWNER_KEY);if(owner===userId)return;if(owner&&owner!==userId){cloudSaveUserCache(owner);cloudClearSyncedLocalState();}if(!owner&&cloudLocalHasData()){localStorage.setItem(CLOUD_LOCAL_OWNER_KEY,userId);cloudSaveUserCache(userId);return;}if(!cloudLocalHasData())cloudLoadUserCache(userId);localStorage.setItem(CLOUD_LOCAL_OWNER_KEY,userId);localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");localStorage.removeItem(CLOUD_LAST_SYNC_KEY);}\n'
        '  function cloudSetMessage',
        "local account isolation helpers",
    )

    text = replace_once(
        text,
        '  function cloudApplyState(state){cloudApplyingState=true;try{for(const [namespace,key] of Object.entries(cloudStorageMap())){if(!Object.prototype.hasOwnProperty.call(state,namespace))continue;const value=state[namespace];if(value===null||value===undefined)localStorage.removeItem(key);else localStorage.setItem(key,JSON.stringify(value));}localStorage.setItem(CLOUD_LAST_SYNC_KEY,new Date().toISOString());}finally{cloudApplyingState=false;}cloudRenderAll();cloudUpdateUi();}',
        '  function cloudApplyState(state){cloudWriteStateToLocal(state,{touchSync:true});const userId=cloudSyncSession.user?.id||localStorage.getItem(CLOUD_LOCAL_OWNER_KEY);if(userId)cloudSaveUserCache(userId);}',
        "cloudApplyState",
    )

    text = replace_once(
        text,
        '  async function cloudRefreshAuthenticatedState({resolveConflict=false,autoPull=false}={}){if(!cloudAuthClient)return null;const {data,error}=await cloudAuthClient.auth.getSession();if(error)throw error;const session=data?.session;if(!session){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudUpdateUi();return null;}const backend=await cloudRequest("/api/auth/session",{method:"GET"},true);if(!backend.authenticated){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudUpdateUi();return null;}cloudSyncSession={...cloudSyncSession,configured:true,authenticated:true,user:backend.user,row_count:backend.row_count??0};cloudUpdateUi();if(resolveConflict)await cloudResolveInitialConflict();else if(autoPull&&localStorage.getItem(CLOUD_AUTOSYNC_KEY)==="1")await cloudPullState({silent:true,confirmOverwrite:false});return backend;}',
        '  async function cloudRefreshAuthenticatedState({resolveConflict=false,autoPull=false}={}){if(!cloudAuthClient)return null;const {data,error}=await cloudAuthClient.auth.getSession();if(error)throw error;const session=data?.session;if(!session){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudUpdateUi();return null;}const backend=await cloudRequest("/api/auth/session",{method:"GET"},true);if(!backend.authenticated){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudUpdateUi();return null;}cloudPrepareLocalStateForUser(backend.user?.id);cloudSyncSession={...cloudSyncSession,configured:true,authenticated:true,user:backend.user,row_count:backend.row_count??0};cloudUpdateUi();if(resolveConflict)await cloudResolveInitialConflict();else if(autoPull&&localStorage.getItem(CLOUD_AUTOSYNC_KEY)==="1")await cloudPullState({silent:true,confirmOverwrite:false});return backend;}',
        "cloudRefreshAuthenticatedState",
    )

    text = replace_once(
        text,
        '  async function cloudHandleAuthEvent(event,session){if(event==="PASSWORD_RECOVERY"){cloudRecoveryMode=true;cloudSetMessage("Sessão de recuperação validada. Define agora uma nova palavra-passe.","warn");}if(event==="SIGNED_OUT"){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");cloudRecoveryMode=false;cloudUpdateUi();return;}if(session){try{await cloudRefreshAuthenticatedState();}catch(error){cloudSetMessage(`Sessão recebida, mas a validação falhou: ${error.message}`,"bad");}}cloudUpdateUi();}',
        '  async function cloudHandleAuthEvent(event,session){if(event==="PASSWORD_RECOVERY"){cloudRecoveryMode=true;cloudSetMessage("Sessão de recuperação validada. Define agora uma nova palavra-passe.","warn");}if(event==="SIGNED_OUT"){const previousUserId=cloudSyncSession.user?.id||localStorage.getItem(CLOUD_LOCAL_OWNER_KEY);if(previousUserId)cloudSaveUserCache(previousUserId);cloudClearSyncedLocalState();localStorage.removeItem(CLOUD_LOCAL_OWNER_KEY);cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudRecoveryMode=false;cloudUpdateUi();return;}if(session){try{await cloudRefreshAuthenticatedState();}catch(error){cloudSetMessage(`Sessão recebida, mas a validação falhou: ${error.message}`,"bad");}}cloudUpdateUi();}',
        "cloudHandleAuthEvent",
    )

    text = replace_once(
        text,
        '  async function cloudSendRecovery(){const email=$("#cloudSyncEmail")?.value.trim()||"";if(!email){showToast("Indica o email da conta.");return;}if(!cloudAuthClient){showToast("Supabase Auth ainda não está pronta.");return;}cloudSetBusy(true);try{const redirectTo=`${location.origin}/?auth=recovery`;const {error}=await cloudAuthClient.auth.resetPasswordForEmail(email,{redirectTo});if(error)throw error;cloudSetMessage("Email de recuperação solicitado. Consulta a caixa de entrada e o spam.");showToast("Email de recuperação solicitado.");}catch(error){cloudSetMessage(error.message,"bad");showToast("Falha ao solicitar recuperação.");}finally{cloudSetBusy(false);cloudUpdateUi();}}',
        '  async function cloudSendRecovery(){const email=$("#cloudSyncEmail")?.value.trim()||"";if(!email){showToast("Indica o email da conta.");return;}if(!cloudAuthClient){showToast("Supabase Auth ainda não está pronta.");return;}cloudSetBusy(true);try{const redirectTo=`${location.origin}/?auth=recovery`;const {error}=await cloudAuthClient.auth.resetPasswordForEmail(email,{redirectTo});if(error)throw error;cloudSetMessage("Email de recuperação solicitado. Consulta a caixa de entrada e o spam.");showToast("Email de recuperação solicitado.");}catch(error){const status=error?.status?`HTTP ${error.status}`:"";const code=error?.code?String(error.code):"";const detail=[error?.message||"Erro desconhecido",status,code].filter(Boolean).join(" · ");console.error("Supabase password recovery failed",error);cloudSetMessage(`Falha ao solicitar recuperação: ${detail}`,"bad");showToast(error?.status===429?"Limite temporário de emails atingido. Tenta novamente mais tarde.":"Falha ao solicitar recuperação.");}finally{cloudSetBusy(false);cloudUpdateUi();}}',
        "cloudSendRecovery",
    )

    text = replace_once(
        text,
        '  async function cloudLogout(){cloudSetBusy(true);try{if(cloudAuthClient){const {error}=await cloudAuthClient.auth.signOut();if(error)throw error;}cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");cloudSetMessage("Sessão terminada. Os dados locais continuam disponíveis neste navegador.");showToast("Sessão terminada.");}catch(error){cloudSetMessage(error.message,"bad");}finally{cloudSetBusy(false);cloudUpdateUi();}}',
        '  async function cloudLogout(){cloudSetBusy(true);try{const userId=cloudSyncSession.user?.id||localStorage.getItem(CLOUD_LOCAL_OWNER_KEY);if(userId)cloudSaveUserCache(userId);if(cloudAuthClient){const {error}=await cloudAuthClient.auth.signOut();if(error)throw error;}cloudClearSyncedLocalState();localStorage.removeItem(CLOUD_LOCAL_OWNER_KEY);cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudSetMessage("Sessão terminada. Os dados privados foram retirados da vista local e voltarão ao iniciar sessão.");showToast("Sessão terminada.");}catch(error){cloudSetMessage(error.message,"bad");}finally{cloudSetBusy(false);cloudUpdateUi();}}',
        "cloudLogout",
    )

    text = replace_once(
        text,
        '  async function cloudPushState({silent=false}={}){if(!cloudSyncSession.authenticated){if(!silent)showToast("Inicia primeiro a sessão.");return;}cloudSetBusy(true);try{const data=await cloudRequest("/api/user/state",{method:"POST",body:JSON.stringify({state:cloudCollectState()})},true);cloudSyncSession.row_count=data.saved_count;localStorage.setItem(CLOUD_LAST_SYNC_KEY,new Date().toISOString());localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");cloudUpdateUi();cloudSetMessage(`${data.saved_count} namespaces enviados para a conta. A sincronização automática ficou ativa.`);if(!silent)showToast("Dados enviados para a cloud.");}catch(error){cloudSetMessage(`Falha ao guardar: ${error.message}`,"bad");if(!silent)showToast("Falha ao enviar dados.");}finally{cloudSetBusy(false);cloudUpdateUi();}}',
        '  async function cloudPushState({silent=false}={}){if(!cloudSyncSession.authenticated){if(!silent)showToast("Inicia primeiro a sessão.");return;}cloudSetBusy(true);try{const data=await cloudRequest("/api/user/state",{method:"POST",body:JSON.stringify({state:cloudCollectState()})},true);cloudSyncSession.row_count=data.saved_count;localStorage.setItem(CLOUD_LAST_SYNC_KEY,new Date().toISOString());localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");if(cloudSyncSession.user?.id){localStorage.setItem(CLOUD_LOCAL_OWNER_KEY,cloudSyncSession.user.id);cloudSaveUserCache(cloudSyncSession.user.id);}cloudUpdateUi();cloudSetMessage(`${data.saved_count} namespaces enviados para a conta. A sincronização automática ficou ativa.`);if(!silent)showToast("Dados enviados para a cloud.");}catch(error){cloudSetMessage(`Falha ao guardar: ${error.message}`,"bad");if(!silent)showToast("Falha ao enviar dados.");}finally{cloudSetBusy(false);cloudUpdateUi();}}',
        "cloudPushState",
    )

    return text


def patch_test(text: str) -> str:
    if "Account-scoped local cache" in text:
        print("[SKIP] test_supabase_auth.py already checks account isolation")
        return text

    anchor = '''    check(
        "Frontend account UI",
        'id="cloudSyncEmail"' in index
        and 'id="cloudSyncSignupBtn"' in index
        and 'id="cloudSyncResetBtn"' in index,
    )
'''
    addition = anchor + '''    check(
        "Account-scoped local cache",
        'CLOUD_LOCAL_OWNER_KEY="thesisos_cloud_local_owner_v1"' in index
        and "cloudPrepareLocalStateForUser" in index
        and "cloudClearSyncedLocalState" in index
        and "cloudSaveUserCache" in index,
    )
    check(
        "Password recovery diagnostics",
        "Supabase password recovery failed" in index
        and "error?.status===429" in index,
    )
'''
    if anchor not in text:
        fail("Could not locate the frontend account UI test anchor")
    return text.replace(anchor, addition, 1)


def main() -> int:
    if not INDEX.exists():
        fail("index.html not found")
    if not TEST.exists():
        fail("test_supabase_auth.py not found")

    backup = ROOT / "index_before_auth_account_isolation.html"
    if not backup.exists():
        shutil.copy2(INDEX, backup)
        print(f"[BACKUP] {backup.name}")

    test_backup = ROOT / "test_supabase_auth_before_account_isolation.py"
    if not test_backup.exists():
        shutil.copy2(TEST, test_backup)
        print(f"[BACKUP] {test_backup.name}")

    index_text = patch_index(INDEX.read_text(encoding="utf-8"))
    test_text = patch_test(TEST.read_text(encoding="utf-8"))

    validate_js(index_text)
    compile(test_text, "test_supabase_auth.py", "exec")
    print("[PASS] test_supabase_auth.py compiles")

    INDEX.write_text(index_text, encoding="utf-8")
    TEST.write_text(test_text, encoding="utf-8")
    print("[WRITE] index.html")
    print("[WRITE] test_supabase_auth.py")
    print()
    print("Next:")
    print("1. Restart the development app.")
    print("2. Run: python3 test_supabase_auth.py")
    print("3. Republish.")
    print("4. Re-test two accounts in a fresh private window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
