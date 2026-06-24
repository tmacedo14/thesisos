#!/usr/bin/env python3
"""Install Supabase Auth and per-user Cloud Sync into the current ThesisOS files.

Expected current files:
- index.html
- server.py
- README.md
- .env.example (optional)

The installer creates backups and writes:
- supabase_auth_migration.sql
- test_supabase_auth.py

It keeps the legacy single-workspace Cloud Sync backend for rollback and
one-time migration.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
SERVER = ROOT / "server.py"
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"

AUTH_SERVER_BLOCK = '# ---------------------------------------------------------------------------\n# Supabase Auth + per-user Cloud Sync\n# ---------------------------------------------------------------------------\nAUTH_USER_STATE_TABLE = "thesisos_user_state"\n\n\ndef auth_publishable_key():\n    return (\n        os.getenv("SUPABASE_PUBLISHABLE_KEY")\n        or os.getenv("SUPABASE_ANON_KEY")\n        or ""\n    ).strip()\n\n\ndef auth_configuration():\n    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")\n    publishable_key = auth_publishable_key()\n    missing = []\n    if not url:\n        missing.append("SUPABASE_URL")\n    if not publishable_key:\n        missing.append("SUPABASE_PUBLISHABLE_KEY ou SUPABASE_ANON_KEY")\n    return {\n        "configured": not missing,\n        "url": url,\n        "publishable_key": publishable_key,\n        "missing": missing,\n        "provider": "Supabase Auth",\n        "table": AUTH_USER_STATE_TABLE,\n    }\n\n\ndef bearer_token_from_headers(headers):\n    value = str(headers.get("Authorization") or "").strip()\n    if not value.lower().startswith("bearer "):\n        return None\n    token = value[7:].strip()\n    return token or None\n\n\ndef supabase_auth_user(access_token):\n    config = auth_configuration()\n    if not config["configured"]:\n        raise RuntimeError(\n            "Supabase Auth não configurado: " + ", ".join(config["missing"])\n        )\n    if not access_token:\n        raise PermissionError("Token de autenticação em falta.")\n    request = Request(\n        f\'{config["url"]}/auth/v1/user\',\n        headers={\n            "apikey": config["publishable_key"],\n            "Authorization": f"Bearer {access_token}",\n            "Accept": "application/json",\n            "User-Agent": "ThesisOS/1.1 SupabaseAuth",\n        },\n        method="GET",\n    )\n    try:\n        with urlopen(request, timeout=20) as response:\n            return json.loads(response.read().decode("utf-8"))\n    except HTTPError as error:\n        detail = error.read().decode("utf-8", errors="replace")\n        if error.code in {401, 403}:\n            raise PermissionError("Sessão Supabase inválida ou expirada.") from error\n        raise RuntimeError(\n            f"Supabase Auth respondeu HTTP {error.code}: {detail[:500]}"\n        ) from error\n\n\ndef supabase_user_state_request(\n    access_token,\n    method="GET",\n    query="",\n    payload=None,\n    prefer=None,\n):\n    config = auth_configuration()\n    if not config["configured"]:\n        raise RuntimeError(\n            "Supabase Auth não configurado: " + ", ".join(config["missing"])\n        )\n    url = f\'{config["url"]}/rest/v1/{AUTH_USER_STATE_TABLE}\'\n    if query:\n        url += "?" + query\n    headers = {\n        "apikey": config["publishable_key"],\n        "Authorization": f"Bearer {access_token}",\n        "Accept": "application/json",\n        "Content-Type": "application/json",\n        "User-Agent": "ThesisOS/1.1 UserCloudSync",\n    }\n    if prefer:\n        headers["Prefer"] = prefer\n    body = None if payload is None else json.dumps(\n        payload,\n        ensure_ascii=False,\n        separators=(",", ":"),\n    ).encode("utf-8")\n    request = Request(url, data=body, headers=headers, method=method)\n    try:\n        with urlopen(request, timeout=25) as response:\n            raw = response.read()\n            return json.loads(raw.decode("utf-8")) if raw else None\n    except HTTPError as error:\n        detail = error.read().decode("utf-8", errors="replace")\n        if error.code in {401, 403}:\n            raise PermissionError(\n                "A sessão não tem autorização para aceder aos dados."\n            ) from error\n        raise RuntimeError(\n            f"Supabase respondeu HTTP {error.code}: {detail[:500]}"\n        ) from error\n\n\ndef supabase_admin_table_request(\n    table,\n    method="GET",\n    query="",\n    payload=None,\n    prefer=None,\n):\n    config = sync_configuration()\n    if not config["url"] or not config["key"]:\n        raise RuntimeError(\n            "A migração exige SUPABASE_URL e SUPABASE_SECRET_KEY."\n        )\n    url = f\'{config["url"]}/rest/v1/{table}\'\n    if query:\n        url += "?" + query\n    headers = {\n        "apikey": config["key"],\n        "Authorization": f\'Bearer {config["key"]}\',\n        "Accept": "application/json",\n        "Content-Type": "application/json",\n        "User-Agent": "ThesisOS/1.1 LegacyMigration",\n    }\n    if prefer:\n        headers["Prefer"] = prefer\n    body = None if payload is None else json.dumps(\n        payload,\n        ensure_ascii=False,\n        separators=(",", ":"),\n    ).encode("utf-8")\n    request = Request(url, data=body, headers=headers, method=method)\n    try:\n        with urlopen(request, timeout=25) as response:\n            raw = response.read()\n            return json.loads(raw.decode("utf-8")) if raw else None\n    except HTTPError as error:\n        detail = error.read().decode("utf-8", errors="replace")\n        raise RuntimeError(\n            f"Supabase respondeu HTTP {error.code}: {detail[:500]}"\n        ) from error\n\n\ndef fetch_user_cloud_state(user_id, access_token):\n    query = urlencode({\n        "select": "namespace,payload,version,updated_at",\n        "user_id": f"eq.{user_id}",\n        "order": "namespace.asc",\n    })\n    rows = supabase_user_state_request(\n        access_token,\n        "GET",\n        query=query,\n    ) or []\n    state = {}\n    metadata = {}\n    for row in rows:\n        namespace = row.get("namespace")\n        if namespace not in SYNC_ALLOWED_NAMESPACES:\n            continue\n        state[namespace] = row.get("payload")\n        metadata[namespace] = {\n            "version": row.get("version"),\n            "updated_at": row.get("updated_at"),\n        }\n    return {"state": state, "metadata": metadata, "row_count": len(state)}\n\n\ndef upsert_user_cloud_state(user_id, access_token, raw_state):\n    state = validate_cloud_state(raw_state)\n    version = int(time.time() * 1000)\n    delete_namespaces = sorted(\n        namespace\n        for namespace, payload in state.items()\n        if payload is None\n    )\n    upsert_state = {\n        namespace: payload\n        for namespace, payload in state.items()\n        if payload is not None\n    }\n\n    if delete_namespaces:\n        namespace_filter = "in.(" + ",".join(delete_namespaces) + ")"\n        delete_query = urlencode({\n            "user_id": f"eq.{user_id}",\n            "namespace": namespace_filter,\n        })\n        supabase_user_state_request(\n            access_token,\n            "DELETE",\n            query=delete_query,\n            prefer="return=minimal",\n        )\n\n    rows = [\n        {\n            "user_id": user_id,\n            "namespace": namespace,\n            "payload": payload,\n            "version": version,\n        }\n        for namespace, payload in upsert_state.items()\n    ]\n    result = []\n    if rows:\n        query = urlencode({"on_conflict": "user_id,namespace"})\n        result = supabase_user_state_request(\n            access_token,\n            "POST",\n            query=query,\n            payload=rows,\n            prefer="resolution=merge-duplicates,return=representation",\n        ) or []\n\n    return {\n        "saved_namespaces": sorted(state),\n        "saved_count": len(state),\n        "upserted_count": len(upsert_state),\n        "deleted_count": len(delete_namespaces),\n        "version": version,\n        "rows": result,\n    }\n\n\ndef migrate_legacy_state_to_user(user_id, overwrite=False):\n    legacy = fetch_cloud_state()\n    legacy_state = {\n        namespace: payload\n        for namespace, payload in legacy.get("state", {}).items()\n        if namespace in SYNC_ALLOWED_NAMESPACES and payload is not None\n    }\n    if not legacy_state:\n        return {\n            "migrated_count": 0,\n            "namespaces": [],\n            "legacy_workspace": sync_workspace_id(),\n        }\n\n    existing_query = urlencode({\n        "select": "namespace",\n        "user_id": f"eq.{user_id}",\n        "limit": "1",\n    })\n    existing = supabase_admin_table_request(\n        AUTH_USER_STATE_TABLE,\n        "GET",\n        query=existing_query,\n    ) or []\n    if existing and not overwrite:\n        raise ValueError(\n            "A conta já contém dados cloud. Ativa a substituição explícita "\n            "para importar o workspace antigo."\n        )\n\n    version = int(time.time() * 1000)\n    rows = [\n        {\n            "user_id": user_id,\n            "namespace": namespace,\n            "payload": payload,\n            "version": version,\n        }\n        for namespace, payload in legacy_state.items()\n    ]\n    query = urlencode({"on_conflict": "user_id,namespace"})\n    supabase_admin_table_request(\n        AUTH_USER_STATE_TABLE,\n        "POST",\n        query=query,\n        payload=rows,\n        prefer="resolution=merge-duplicates,return=minimal",\n    )\n    return {\n        "migrated_count": len(rows),\n        "namespaces": sorted(legacy_state),\n        "legacy_workspace": sync_workspace_id(),\n        "version": version,\n    }\n\n'
AUTH_CSS = '\n/* ThesisOS Supabase Auth */\n.cloud-auth-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:12px 0}\n.cloud-auth-tab{border:1px solid #2b4162;background:#132139;color:#aebbd0;border-radius:10px;padding:9px;font-weight:800}\n.cloud-auth-tab.active{background:#284268;color:#fff;border-color:#4c6c9e}\n.cloud-auth-fields{display:grid;gap:10px}\n.cloud-auth-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}\n.cloud-auth-user{display:none;padding:13px;border:1px solid #2d5a4c;border-radius:12px;background:#17352a}\n.cloud-auth-user.visible{display:block}\n.cloud-auth-user strong{display:block}\n.cloud-auth-user small{display:block;color:#a9cbbb;margin-top:5px;overflow-wrap:anywhere}\n.cloud-auth-signed-out.hidden{display:none}\n.cloud-auth-recovery{display:none;margin-top:12px;padding:13px;border:1px solid #604a26;border-radius:12px;background:#2c2417}\n.cloud-auth-recovery.visible{display:block}\n.cloud-legacy-migration{display:none;margin-top:12px;padding:13px;border:1px solid #304968;border-radius:12px;background:#132139}\n.cloud-legacy-migration.visible{display:block}\n.cloud-legacy-migration p{margin:6px 0 0;color:var(--muted);font-size:12px;line-height:1.45}\n.cloud-auth-note{margin-top:10px;color:var(--muted);font-size:11px;line-height:1.5}\n@media(max-width:620px){.cloud-auth-tabs{grid-template-columns:1fr}.cloud-auth-actions button{flex:1}}\n'
NEW_CLOUD_CARD = '<section class="card cloud-sync-card" id="cloudSyncCard">\n  <div class="cloud-sync-head">\n    <div><h2 style="margin:0 0 6px">Conta & Cloud Sync · Supabase</h2><p class="sub">Cria uma conta pessoal para sincronizar carteira, Journal, alertas, política, planos, Radar e Evidence Feed entre dispositivos.</p></div>\n    <span class="freshness warn" id="cloudSyncBadge">A verificar</span>\n  </div>\n  <div class="cloud-sync-layout">\n    <div class="cloud-sync-auth">\n      <h3>Conta ThesisOS</h3>\n      <p class="sub" style="font-size:12px;margin-top:6px">A autenticação é gerida pelo Supabase Auth. Cada utilizador vê apenas os seus próprios dados através de Row Level Security.</p>\n\n      <div class="cloud-auth-signed-out" id="cloudAuthSignedOut">\n        <div class="cloud-auth-tabs">\n          <button class="cloud-auth-tab active" id="cloudAuthLoginTab" type="button">Entrar</button>\n          <button class="cloud-auth-tab" id="cloudAuthSignupTab" type="button">Criar conta</button>\n          <button class="cloud-auth-tab" id="cloudAuthResetTab" type="button">Recuperar</button>\n        </div>\n        <div class="cloud-auth-fields">\n          <input id="cloudSyncEmail" type="email" autocomplete="email" placeholder="Email">\n          <input id="cloudSyncPassword" type="password" autocomplete="current-password" placeholder="Palavra-passe">\n        </div>\n        <div class="cloud-auth-actions">\n          <button class="primary" id="cloudSyncLoginBtn" type="button">Entrar</button>\n          <button class="primary" id="cloudSyncSignupBtn" type="button" style="display:none">Criar conta</button>\n          <button class="ghost" id="cloudSyncResetBtn" type="button" style="display:none">Enviar email de recuperação</button>\n        </div>\n        <div class="cloud-auth-note">A chave publicável pode ser usada no navegador. A chave secreta permanece exclusivamente no backend.</div>\n      </div>\n\n      <div class="cloud-auth-user" id="cloudAuthSignedIn">\n        <strong id="cloudAuthUserEmail">Sessão iniciada</strong>\n        <small id="cloudAuthUserId">—</small>\n        <div class="cloud-auth-actions">\n          <button class="ghost" id="cloudSyncLogoutBtn" type="button">Terminar sessão</button>\n          <button class="ghost" id="cloudSyncRefreshBtn" type="button">Verificar estado</button>\n        </div>\n      </div>\n\n      <div class="cloud-auth-recovery" id="cloudRecoveryPanel">\n        <strong>Definir nova palavra-passe</strong>\n        <div class="cloud-auth-fields" style="margin-top:10px">\n          <input id="cloudNewPassword" type="password" autocomplete="new-password" minlength="8" placeholder="Nova palavra-passe">\n        </div>\n        <div class="cloud-auth-actions">\n          <button class="primary" id="cloudUpdatePasswordBtn" type="button">Atualizar palavra-passe</button>\n        </div>\n      </div>\n\n      <div class="cloud-legacy-migration" id="cloudLegacyMigration">\n        <strong>Importar o workspace antigo</strong>\n        <p>Usa uma única vez a antiga palavra-passe do Cloud Sync para copiar os dados do workspace <code>tiago</code> para esta conta. A tabela antiga permanece como backup.</p>\n        <div class="cloud-auth-fields" style="margin-top:10px">\n          <input id="cloudLegacyPassword" type="password" autocomplete="off" placeholder="Palavra-passe antiga do Cloud Sync">\n        </div>\n        <div class="cloud-auth-actions">\n          <button class="ghost" id="cloudLegacyMigrateBtn" type="button">Importar dados antigos</button>\n        </div>\n      </div>\n\n      <label class="cloud-sync-toggle"><input id="cloudSyncAuto" type="checkbox"><span>Sincronizar automaticamente depois de escolher a origem inicial dos dados.</span></label>\n    </div>\n\n    <div class="cloud-sync-state">\n      <h3>Estado e controlo de conflitos</h3>\n      <div class="cloud-sync-status-list">\n        <div class="cloud-sync-status-row"><span>Configuração</span><strong id="cloudSyncConfigured">A verificar</strong></div>\n        <div class="cloud-sync-status-row"><span>Sessão</span><strong id="cloudSyncSession">Desligada</strong></div>\n        <div class="cloud-sync-status-row"><span>Conta</span><strong id="cloudSyncWorkspace">—</strong></div>\n        <div class="cloud-sync-status-row"><span>Última sincronização</span><strong id="cloudSyncLastSync">Nunca</strong></div>\n        <div class="cloud-sync-status-row"><span>Dados cloud</span><strong id="cloudSyncRows">—</strong></div>\n      </div>\n      <div class="cloud-sync-buttons">\n        <button class="primary" id="cloudSyncPushBtn" type="button">Enviar dados locais</button>\n        <button class="ghost" id="cloudSyncPullBtn" type="button">Restaurar da cloud</button>\n      </div>\n      <div class="cloud-sync-message" id="cloudSyncMessage">Podes continuar a usar o ThesisOS apenas neste navegador. Ao iniciar sessão, a cloud passa a estar associada à tua conta pessoal.</div>\n    </div>\n  </div>\n</section>'
AUTH_JS = '// Supabase Auth + per-user Cloud Sync with localStorage fallback\n  // ---------------------------------------------------------------\n  const CLOUD_AUTOSYNC_KEY="thesisos_cloud_autosync_user_v1";\n  const CLOUD_LAST_SYNC_KEY="thesisos_cloud_last_sync_user_v1";\n  let cloudSyncSession={configured:false,authenticated:false,user:null,row_count:null};\n  let cloudAuthClient=null;\n  let cloudAuthMode="login";\n  let cloudSyncTimer=null;\n  let cloudApplyingState=false;\n  let cloudSyncBusy=false;\n  let cloudRecoveryMode=false;\n\n  function cloudStorageMap(){return {\n    watchlist:WATCHLIST_KEY,\n    portfolio_transactions:PORTFOLIO_TX_KEY,\n    portfolio_quotes:PORTFOLIO_QUOTES_KEY,\n    decision_journal:DECISION_JOURNAL_KEY,\n    monitoring_alerts:MONITORING_ALERTS_KEY,\n    investor_policy:INVESTOR_POLICY_KEY,\n    portfolio_construction:PORTFOLIO_CONSTRUCTION_KEY,\n    last_radar:DASHBOARD_RADAR_KEY,\n    last_evidence:DASHBOARD_EVIDENCE_KEY,\n  };}\n  function cloudDefaultFor(namespace){return ["watchlist","portfolio_transactions","decision_journal","monitoring_alerts"].includes(namespace)?[]:["portfolio_quotes"].includes(namespace)?{}:null;}\n  function cloudCollectState(){const state={};for(const [namespace,key] of Object.entries(cloudStorageMap())){const raw=localStorage.getItem(key);if(raw===null){state[namespace]=cloudDefaultFor(namespace);continue;}try{state[namespace]=JSON.parse(raw);}catch{state[namespace]=cloudDefaultFor(namespace);}}return state;}\n  function cloudLocalHasData(){const state=cloudCollectState();return Object.values(state).some(value=>Array.isArray(value)?value.length>0:value&&typeof value==="object"?Object.keys(value).length>0:value!==null&&value!=="");}\n  function cloudSetMessage(text,tone=""){const element=$("#cloudSyncMessage");if(!element)return;element.textContent=text;element.className=`cloud-sync-message ${tone}`.trim();}\n  function cloudSetBusy(value){cloudSyncBusy=value;["cloudSyncLoginBtn","cloudSyncSignupBtn","cloudSyncResetBtn","cloudSyncLogoutBtn","cloudSyncRefreshBtn","cloudSyncPushBtn","cloudSyncPullBtn","cloudUpdatePasswordBtn","cloudLegacyMigrateBtn"].forEach(id=>{const el=$("#"+id);if(el)el.disabled=value;});}\n  function cloudInitials(email){const name=String(email||"").split("@")[0].replace(/[^A-Za-z0-9]+/g," ").trim();const parts=name.split(/\\s+/).filter(Boolean);return (parts.length>1?parts[0][0]+parts[1][0]:name.slice(0,2)||"U").toUpperCase();}\n  function cloudDisplayName(email){const name=String(email||"").split("@")[0].replace(/[._-]+/g," ").trim();return name?name.replace(/\\b\\w/g,char=>char.toUpperCase()):"Utilizador";}\n  function cloudSwitchAuthMode(mode){cloudAuthMode=mode;["login","signup","reset"].forEach(value=>{$(`#cloudAuth${value[0].toUpperCase()+value.slice(1)}Tab`)?.classList.toggle("active",value===mode);});if($("#cloudSyncLoginBtn"))$("#cloudSyncLoginBtn").style.display=mode==="login"?"inline-block":"none";if($("#cloudSyncSignupBtn"))$("#cloudSyncSignupBtn").style.display=mode==="signup"?"inline-block":"none";if($("#cloudSyncResetBtn"))$("#cloudSyncResetBtn").style.display=mode==="reset"?"inline-block":"none";const password=$("#cloudSyncPassword");if(password){password.style.display=mode==="reset"?"none":"block";password.autocomplete=mode==="signup"?"new-password":"current-password";password.placeholder=mode==="signup"?"Palavra-passe nova (mínimo 8 caracteres)":"Palavra-passe";}}\n  function cloudUpdateUi(){const configured=cloudSyncSession.configured,authenticated=cloudSyncSession.authenticated,user=cloudSyncSession.user||{},badge=$("#cloudSyncBadge");if(badge){badge.textContent=!configured?"Não configurado":authenticated?"Conta ligada":"Sessão desligada";badge.className=`freshness ${!configured?"bad":authenticated?"":"warn"}`.trim();}if($("#cloudSyncConfigured"))$("#cloudSyncConfigured").textContent=configured?"Supabase Auth pronta":"Falta configuração";if($("#cloudSyncSession"))$("#cloudSyncSession").textContent=authenticated?"Autenticada":"Desligada";if($("#cloudSyncWorkspace"))$("#cloudSyncWorkspace").textContent=user.email||"—";if($("#cloudSyncRows"))$("#cloudSyncRows").textContent=cloudSyncSession.row_count===null?"—":`${cloudSyncSession.row_count} namespaces`;const last=localStorage.getItem(CLOUD_LAST_SYNC_KEY);if($("#cloudSyncLastSync"))$("#cloudSyncLastSync").textContent=last?new Date(last).toLocaleString("pt-PT"):"Nunca";if($("#cloudSyncAuto"))$("#cloudSyncAuto").checked=localStorage.getItem(CLOUD_AUTOSYNC_KEY)==="1";$("#cloudAuthSignedOut")?.classList.toggle("hidden",authenticated);$("#cloudAuthSignedIn")?.classList.toggle("visible",authenticated);$("#cloudLegacyMigration")?.classList.toggle("visible",authenticated);$("#cloudRecoveryPanel")?.classList.toggle("visible",cloudRecoveryMode&&authenticated);if($("#cloudAuthUserEmail"))$("#cloudAuthUserEmail").textContent=user.email||"Sessão iniciada";if($("#cloudAuthUserId"))$("#cloudAuthUserId").textContent=user.id?`ID: ${user.id}`:"—";["cloudSyncPushBtn","cloudSyncPullBtn","cloudSyncAuto","cloudLegacyMigrateBtn"].forEach(id=>{const el=$("#"+id);if(el)el.disabled=!authenticated||cloudSyncBusy;});const topName=$("#topbarUserName"),topAvatar=$("#topbarUserAvatar");if(topName)topName.textContent=authenticated?cloudDisplayName(user.email):"Visitante";if(topAvatar)topAvatar.textContent=authenticated?cloudInitials(user.email):"—";}\n  async function cloudRequest(path,options={},requireAuth=false){const headers={"Content-Type":"application/json",...(options.headers||{})};if(requireAuth){if(!cloudAuthClient)throw new Error("Supabase Auth ainda não inicializada.");const {data,error}=await cloudAuthClient.auth.getSession();if(error)throw error;const token=data?.session?.access_token;if(!token)throw new Error("Inicia sessão para aceder à cloud.");headers.Authorization=`Bearer ${token}`;}const response=await fetch(path,{...options,headers,credentials:"same-origin"}),data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||data.error||`Erro HTTP ${response.status}`);return data;}\n  function cloudRenderAll(){updateWatchlistCount();renderWatchlist();renderPortfolioManager();renderDecisionJournal();renderMonitoringAlerts();investorPolicyFormLoaded=false;fillPolicyForm(getInvestorPolicy());renderInvestorPolicy();renderPortfolioConstruction();renderLiveDashboard();const radar=dashboardRead(DASHBOARD_RADAR_KEY,null);if(radar){window.lastRadarData=radar;renderRadarResults(radar);}const evidence=dashboardRead(DASHBOARD_EVIDENCE_KEY,null);if(evidence)renderEvidenceFeed(evidence);if(window.currentAnalysisPayload)renderPolicySizing(window.currentAnalysisPayload);}\n  function cloudApplyState(state){cloudApplyingState=true;try{for(const [namespace,key] of Object.entries(cloudStorageMap())){if(!Object.prototype.hasOwnProperty.call(state,namespace))continue;const value=state[namespace];if(value===null||value===undefined)localStorage.removeItem(key);else localStorage.setItem(key,JSON.stringify(value));}localStorage.setItem(CLOUD_LAST_SYNC_KEY,new Date().toISOString());}finally{cloudApplyingState=false;}cloudRenderAll();cloudUpdateUi();}\n  async function cloudRefreshAuthenticatedState({resolveConflict=false,autoPull=false}={}){if(!cloudAuthClient)return null;const {data,error}=await cloudAuthClient.auth.getSession();if(error)throw error;const session=data?.session;if(!session){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudUpdateUi();return null;}const backend=await cloudRequest("/api/auth/session",{method:"GET"},true);if(!backend.authenticated){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};cloudUpdateUi();return null;}cloudSyncSession={...cloudSyncSession,configured:true,authenticated:true,user:backend.user,row_count:backend.row_count??0};cloudUpdateUi();if(resolveConflict)await cloudResolveInitialConflict();else if(autoPull&&localStorage.getItem(CLOUD_AUTOSYNC_KEY)==="1")await cloudPullState({silent:true,confirmOverwrite:false});return backend;}\n  async function cloudResolveInitialConflict(){const localHas=cloudLocalHasData(),cloudHas=(cloudSyncSession.row_count||0)>0;if(cloudHas&&!localHas){await cloudPullState({silent:true,confirmOverwrite:false});localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");cloudSetMessage("Os dados desta conta foram restaurados porque este navegador estava vazio.");}else if(!cloudHas&&localHas){await cloudPushState({silent:true});localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");cloudSetMessage("Os dados locais foram associados automaticamente à nova conta.");}else if(cloudHas&&localHas){localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");cloudSetMessage("Existem dados locais e dados na conta. Escolhe ‘Enviar dados locais’ ou ‘Restaurar da cloud’ antes de ativar a sincronização automática.","warn");}else{localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");cloudSetMessage("Conta vazia e pronta para sincronizar.");}cloudUpdateUi();}\n  async function cloudInitializeAuth(){try{const config=await cloudRequest("/api/auth/config",{method:"GET"});cloudSyncSession.configured=Boolean(config.configured);cloudUpdateUi();if(!config.configured){cloudSetMessage(`Configuração incompleta: ${(config.missing||[]).join(", ")}.`,"warn");return;}if(!window.supabase?.createClient)throw new Error("Biblioteca Supabase JS indisponível.");cloudAuthClient=window.supabase.createClient(config.url,config.publishable_key,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}});cloudAuthClient.auth.onAuthStateChange((event,session)=>{setTimeout(()=>void cloudHandleAuthEvent(event,session),0);});await cloudRefreshAuthenticatedState({autoPull:true});if(!cloudSyncSession.authenticated)cloudSetMessage("Cria uma conta ou inicia sessão para sincronizar entre dispositivos.");}catch(error){cloudSyncSession={configured:false,authenticated:false,user:null,row_count:null};cloudUpdateUi();cloudSetMessage(`Supabase Auth indisponível: ${error.message}`,"bad");}}\n  async function cloudHandleAuthEvent(event,session){if(event==="PASSWORD_RECOVERY"){cloudRecoveryMode=true;cloudSetMessage("Sessão de recuperação validada. Define agora uma nova palavra-passe.","warn");}if(event==="SIGNED_OUT"){cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");cloudRecoveryMode=false;cloudUpdateUi();return;}if(session){try{await cloudRefreshAuthenticatedState();}catch(error){cloudSetMessage(`Sessão recebida, mas a validação falhou: ${error.message}`,"bad");}}cloudUpdateUi();}\n  async function cloudLogin(){const email=$("#cloudSyncEmail")?.value.trim()||"",password=$("#cloudSyncPassword")?.value||"";if(!email||!password){showToast("Preenche email e palavra-passe.");return;}if(!cloudAuthClient){showToast("Supabase Auth ainda não está pronta.");return;}cloudSetBusy(true);try{const {error}=await cloudAuthClient.auth.signInWithPassword({email,password});if(error)throw error;$("#cloudSyncPassword").value="";await cloudRefreshAuthenticatedState({resolveConflict:true});cloudSetMessage("Sessão iniciada. A cloud está associada à tua conta.");showToast("Sessão iniciada.");}catch(error){cloudSetMessage(error.message,"bad");showToast("Não foi possível iniciar sessão.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudSignup(){const email=$("#cloudSyncEmail")?.value.trim()||"",password=$("#cloudSyncPassword")?.value||"";if(!email||password.length<8){showToast("Indica um email e uma palavra-passe com pelo menos 8 caracteres.");return;}if(!cloudAuthClient){showToast("Supabase Auth ainda não está pronta.");return;}cloudSetBusy(true);try{const redirectTo=`${location.origin}/?auth=confirmed`;const {data,error}=await cloudAuthClient.auth.signUp({email,password,options:{emailRedirectTo:redirectTo}});if(error)throw error;$("#cloudSyncPassword").value="";if(data.session){await cloudRefreshAuthenticatedState({resolveConflict:true});cloudSetMessage("Conta criada e sessão iniciada.");}else{cloudSetMessage("Conta criada. Consulta o email e confirma o registo antes de iniciar sessão.");}showToast("Pedido de registo concluído.");}catch(error){cloudSetMessage(error.message,"bad");showToast("Não foi possível criar a conta.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudSendRecovery(){const email=$("#cloudSyncEmail")?.value.trim()||"";if(!email){showToast("Indica o email da conta.");return;}if(!cloudAuthClient){showToast("Supabase Auth ainda não está pronta.");return;}cloudSetBusy(true);try{const redirectTo=`${location.origin}/?auth=recovery`;const {error}=await cloudAuthClient.auth.resetPasswordForEmail(email,{redirectTo});if(error)throw error;cloudSetMessage("Email de recuperação solicitado. Consulta a caixa de entrada e o spam.");showToast("Email de recuperação solicitado.");}catch(error){cloudSetMessage(error.message,"bad");showToast("Falha ao solicitar recuperação.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudUpdatePassword(){const password=$("#cloudNewPassword")?.value||"";if(password.length<8){showToast("A nova palavra-passe deve ter pelo menos 8 caracteres.");return;}cloudSetBusy(true);try{const {error}=await cloudAuthClient.auth.updateUser({password});if(error)throw error;$("#cloudNewPassword").value="";cloudRecoveryMode=false;history.replaceState({},document.title,location.pathname);cloudSetMessage("Palavra-passe atualizada com sucesso.");showToast("Palavra-passe atualizada.");}catch(error){cloudSetMessage(error.message,"bad");showToast("Falha ao atualizar a palavra-passe.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudLogout(){cloudSetBusy(true);try{if(cloudAuthClient){const {error}=await cloudAuthClient.auth.signOut();if(error)throw error;}cloudSyncSession={...cloudSyncSession,authenticated:false,user:null,row_count:0};localStorage.setItem(CLOUD_AUTOSYNC_KEY,"0");cloudSetMessage("Sessão terminada. Os dados locais continuam disponíveis neste navegador.");showToast("Sessão terminada.");}catch(error){cloudSetMessage(error.message,"bad");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudPushState({silent=false}={}){if(!cloudSyncSession.authenticated){if(!silent)showToast("Inicia primeiro a sessão.");return;}cloudSetBusy(true);try{const data=await cloudRequest("/api/user/state",{method:"POST",body:JSON.stringify({state:cloudCollectState()})},true);cloudSyncSession.row_count=data.saved_count;localStorage.setItem(CLOUD_LAST_SYNC_KEY,new Date().toISOString());localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");cloudUpdateUi();cloudSetMessage(`${data.saved_count} namespaces enviados para a conta. A sincronização automática ficou ativa.`);if(!silent)showToast("Dados enviados para a cloud.");}catch(error){cloudSetMessage(`Falha ao guardar: ${error.message}`,"bad");if(!silent)showToast("Falha ao enviar dados.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudPullState({silent=false,confirmOverwrite=true}={}){if(!cloudSyncSession.authenticated){if(!silent)showToast("Inicia primeiro a sessão.");return;}if(confirmOverwrite&&cloudLocalHasData()&&!confirm("Restaurar da cloud vai substituir os dados locais sincronizáveis deste navegador. Continuar?"))return;cloudSetBusy(true);try{const data=await cloudRequest("/api/user/state",{method:"GET"},true);cloudSyncSession.row_count=data.row_count||0;if(data.row_count){cloudApplyState(data.state||{});localStorage.setItem(CLOUD_AUTOSYNC_KEY,"1");cloudSetMessage(`${data.row_count} namespaces restaurados da tua conta.`);if(!silent)showToast("Dados restaurados da cloud.");}else{cloudSetMessage("Esta conta ainda não contém dados. Envia a cópia local ou importa o workspace antigo.","warn");if(!silent)showToast("Cloud da conta vazia.");}}catch(error){cloudSetMessage(`Falha ao restaurar: ${error.message}`,"bad");if(!silent)showToast("Falha ao restaurar dados.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  async function cloudMigrateLegacy(){const password=$("#cloudLegacyPassword")?.value||"";if(!password){showToast("Introduz a antiga palavra-passe do Cloud Sync.");return;}const overwrite=(cloudSyncSession.row_count||0)>0?confirm("A conta já contém dados. Queres substituir/mesclar os mesmos namespaces com o workspace antigo?"):false;if((cloudSyncSession.row_count||0)>0&&!overwrite)return;cloudSetBusy(true);try{const data=await cloudRequest("/api/auth/migrate-legacy",{method:"POST",body:JSON.stringify({password,overwrite})},true);$("#cloudLegacyPassword").value="";cloudSetMessage(`${data.migrated_count} namespaces importados do workspace antigo. A tabela antiga foi mantida como backup.`);await cloudPullState({silent:true,confirmOverwrite:false});showToast("Workspace antigo importado.");}catch(error){cloudSetMessage(`Migração falhou: ${error.message}`,"bad");showToast("Falha na migração.");}finally{cloudSetBusy(false);cloudUpdateUi();}}\n  function cloudSchedulePush(){if(cloudApplyingState||!cloudSyncSession.authenticated||localStorage.getItem(CLOUD_AUTOSYNC_KEY)!=="1")return;clearTimeout(cloudSyncTimer);cloudSyncTimer=setTimeout(()=>void cloudPushState({silent:true}),1200);}\n\n  $("#cloudAuthLoginTab")?.addEventListener("click",()=>cloudSwitchAuthMode("login"));$("#cloudAuthSignupTab")?.addEventListener("click",()=>cloudSwitchAuthMode("signup"));$("#cloudAuthResetTab")?.addEventListener("click",()=>cloudSwitchAuthMode("reset"));\n  $("#cloudSyncLoginBtn")?.addEventListener("click",cloudLogin);$("#cloudSyncSignupBtn")?.addEventListener("click",cloudSignup);$("#cloudSyncResetBtn")?.addEventListener("click",cloudSendRecovery);$("#cloudSyncLogoutBtn")?.addEventListener("click",cloudLogout);$("#cloudSyncRefreshBtn")?.addEventListener("click",()=>cloudRefreshAuthenticatedState({autoPull:false}));$("#cloudSyncPushBtn")?.addEventListener("click",()=>cloudPushState());$("#cloudSyncPullBtn")?.addEventListener("click",()=>cloudPullState());$("#cloudUpdatePasswordBtn")?.addEventListener("click",cloudUpdatePassword);$("#cloudLegacyMigrateBtn")?.addEventListener("click",cloudMigrateLegacy);$("#cloudSyncPassword")?.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();if(cloudAuthMode==="signup")void cloudSignup();else if(cloudAuthMode==="reset")void cloudSendRecovery();else void cloudLogin();}});$("#cloudSyncAuto")?.addEventListener("change",event=>{localStorage.setItem(CLOUD_AUTOSYNC_KEY,event.target.checked?"1":"0");cloudUpdateUi();if(event.target.checked)cloudSchedulePush();});\n  cloudSwitchAuthMode("login");cloudUpdateUi();void cloudInitializeAuth();'
AUTH_SQL = '-- ThesisOS Supabase Auth migration\n-- Run once in Supabase > SQL Editor after the original supabase_schema.sql.\n-- The legacy public.thesisos_state table is intentionally preserved as backup.\n\ncreate table if not exists public.thesisos_user_state (\n  user_id uuid not null references auth.users(id) on delete cascade,\n  namespace text not null,\n  payload jsonb not null,\n  version bigint not null default 1,\n  updated_at timestamptz not null default now(),\n  primary key (user_id, namespace),\n  constraint thesisos_user_state_namespace_not_blank\n    check (length(trim(namespace)) > 0)\n);\n\ncreate or replace function public.set_thesisos_user_state_updated_at()\nreturns trigger\nlanguage plpgsql\nsecurity invoker\nset search_path = public\nas $$\nbegin\n  new.updated_at = now();\n  return new;\nend;\n$$;\n\ndrop trigger if exists thesisos_user_state_updated_at\n  on public.thesisos_user_state;\n\ncreate trigger thesisos_user_state_updated_at\nbefore update on public.thesisos_user_state\nfor each row execute function public.set_thesisos_user_state_updated_at();\n\nalter table public.thesisos_user_state enable row level security;\n\nrevoke all on table public.thesisos_user_state from anon;\ngrant select, insert, update, delete\n  on table public.thesisos_user_state\n  to authenticated, service_role;\n\ndrop policy if exists "Users read own ThesisOS state"\n  on public.thesisos_user_state;\ncreate policy "Users read own ThesisOS state"\non public.thesisos_user_state\nfor select\nto authenticated\nusing ((select auth.uid()) = user_id);\n\ndrop policy if exists "Users insert own ThesisOS state"\n  on public.thesisos_user_state;\ncreate policy "Users insert own ThesisOS state"\non public.thesisos_user_state\nfor insert\nto authenticated\nwith check ((select auth.uid()) = user_id);\n\ndrop policy if exists "Users update own ThesisOS state"\n  on public.thesisos_user_state;\ncreate policy "Users update own ThesisOS state"\non public.thesisos_user_state\nfor update\nto authenticated\nusing ((select auth.uid()) = user_id)\nwith check ((select auth.uid()) = user_id);\n\ndrop policy if exists "Users delete own ThesisOS state"\n  on public.thesisos_user_state;\ncreate policy "Users delete own ThesisOS state"\non public.thesisos_user_state\nfor delete\nto authenticated\nusing ((select auth.uid()) = user_id);\n\ncreate index if not exists thesisos_user_state_updated_at_idx\n  on public.thesisos_user_state (updated_at desc);\n\ncomment on table public.thesisos_user_state is\n  \'Per-user ThesisOS state protected by Supabase Auth and Row Level Security.\';\n'
AUTH_TEST = '#!/usr/bin/env python3\n"""Read-only validation for ThesisOS Supabase Auth.\n\nUsage:\n    python3 test_supabase_auth.py\n    THESISOS_TEST_EMAIL="..." THESISOS_TEST_PASSWORD="..." \\\n      python3 test_supabase_auth.py --login\n\nThe optional login mode signs in through Supabase Auth and reads only the\ncurrent user\'s ThesisOS cloud state. It never writes or deletes data.\n"""\n\nfrom __future__ import annotations\n\nimport argparse\nimport json\nimport os\nimport sys\nfrom pathlib import Path\nfrom urllib.error import HTTPError\nfrom urllib.parse import urlencode\nfrom urllib.request import Request, urlopen\n\nROOT = Path(__file__).resolve().parent\n\n\ndef request_json(url, method="GET", payload=None, headers=None, timeout=30):\n    body = None\n    request_headers = {"Accept": "application/json", **(headers or {})}\n    if payload is not None:\n        body = json.dumps(payload).encode("utf-8")\n        request_headers["Content-Type"] = "application/json"\n    request = Request(\n        url,\n        data=body,\n        method=method,\n        headers=request_headers,\n    )\n    try:\n        with urlopen(request, timeout=timeout) as response:\n            raw = response.read().decode("utf-8")\n            return response.status, json.loads(raw) if raw else {}\n    except HTTPError as error:\n        raw = error.read().decode("utf-8", errors="replace")\n        try:\n            data = json.loads(raw)\n        except json.JSONDecodeError:\n            data = {"error": raw[:500]}\n        return error.code, data\n\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument(\n        "--base-url",\n        default="http://localhost:3000",\n    )\n    parser.add_argument("--login", action="store_true")\n    args = parser.parse_args()\n    base = args.base_url.rstrip("/")\n    failures = 0\n\n    def check(name, condition, detail=""):\n        nonlocal failures\n        level = "PASS" if condition else "FAIL"\n        if not condition:\n            failures += 1\n        suffix = f" — {detail}" if detail else ""\n        print(f"[{level}] {name}{suffix}")\n\n    index = (ROOT / "index.html").read_text(encoding="utf-8")\n    server = (ROOT / "server.py").read_text(encoding="utf-8")\n    sql = (ROOT / "supabase_auth_migration.sql").read_text(\n        encoding="utf-8"\n    ).lower()\n\n    check(\n        "Frontend Supabase client",\n        "@supabase/supabase-js@2" in index,\n    )\n    check(\n        "Frontend account UI",\n        \'id="cloudSyncEmail"\' in index\n        and \'id="cloudSyncSignupBtn"\' in index\n        and \'id="cloudSyncResetBtn"\' in index,\n    )\n    check(\n        "Backend Auth routes",\n        all(\n            route in server\n            for route in (\n                "/api/auth/config",\n                "/api/auth/session",\n                "/api/user/state",\n                "/api/auth/migrate-legacy",\n            )\n        ),\n    )\n    check(\n        "Per-user table",\n        "create table if not exists public.thesisos_user_state" in sql,\n    )\n    check(\n        "RLS user isolation",\n        "to authenticated" in sql\n        and "(select auth.uid()) = user_id" in sql\n        and "revoke all on table public.thesisos_user_state from anon" in sql,\n    )\n    check(\n        "Secret not embedded",\n        "sb_secret_" not in index\n        and "SUPABASE_SECRET_KEY" not in index,\n    )\n\n    status, config = request_json(f"{base}/api/auth/config")\n    configured = (\n        status == 200\n        and config.get("status") == "ok"\n        and config.get("configured") is True\n        and bool(config.get("url"))\n        and bool(config.get("publishable_key"))\n    )\n    check(\n        "Auth configuration endpoint",\n        configured,\n        (\n            "configured"\n            if configured\n            else f"HTTP {status}: {config.get(\'missing\') or config}"\n        ),\n    )\n\n    status, _ = request_json(f"{base}/api/user/state")\n    check(\n        "Anonymous state access denied",\n        status == 401,\n        f"HTTP {status}",\n    )\n\n    status, _ = request_json(\n        f"{base}/api/auth/migrate-legacy",\n        method="POST",\n        payload={"password": "not-used"},\n    )\n    check(\n        "Anonymous migration denied",\n        status == 401,\n        f"HTTP {status}",\n    )\n\n    if args.login:\n        email = os.getenv("THESISOS_TEST_EMAIL", "").strip()\n        password = os.getenv("THESISOS_TEST_PASSWORD", "")\n        if not email or not password:\n            check(\n                "Authenticated read",\n                False,\n                "Set THESISOS_TEST_EMAIL and THESISOS_TEST_PASSWORD",\n            )\n        elif configured:\n            token_url = (\n                config["url"].rstrip("/")\n                + "/auth/v1/token?"\n                + urlencode({"grant_type": "password"})\n            )\n            status, login = request_json(\n                token_url,\n                method="POST",\n                payload={"email": email, "password": password},\n                headers={"apikey": config["publishable_key"]},\n            )\n            token = login.get("access_token")\n            check(\n                "Supabase email/password login",\n                status == 200 and bool(token),\n                f"HTTP {status}",\n            )\n            if token:\n                auth_headers = {"Authorization": f"Bearer {token}"}\n                status, session = request_json(\n                    f"{base}/api/auth/session",\n                    headers=auth_headers,\n                )\n                check(\n                    "Backend session validation",\n                    status == 200 and session.get("authenticated") is True,\n                    f"HTTP {status}",\n                )\n                status, state = request_json(\n                    f"{base}/api/user/state",\n                    headers=auth_headers,\n                )\n                check(\n                    "Read-only per-user restore",\n                    status == 200 and isinstance(state.get("state"), dict),\n                    (\n                        f"{state.get(\'row_count\', 0)} namespaces"\n                        if status == 200\n                        else f"HTTP {status}"\n                    ),\n                )\n\n    print("-" * 64)\n    if failures:\n        print(f"Supabase Auth validation failed: {failures} issue(s).")\n        return 1\n    print("Supabase Auth validation passed.")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
AUTH_README_SECTION = "## Supabase Auth and per-user Cloud Sync\n\nThesisOS supports personal accounts through Supabase Auth. Each authenticated\nuser receives an independent cloud workspace stored in\n`public.thesisos_user_state`.\n\nSynchronized namespaces:\n\n- watchlist;\n- portfolio transactions and cached quotes;\n- Decision Journal;\n- monitoring alerts;\n- Investor Policy;\n- saved Portfolio Construction plan;\n- latest Radar result;\n- latest Evidence Feed.\n\n### Supabase setup\n\n1. Execute `supabase_schema.sql` if the legacy Cloud Sync table does not yet\n   exist.\n2. Execute `supabase_auth_migration.sql` once.\n3. In **Authentication → URL Configuration**, set the production Site URL to\n   the public ThesisOS URL and add the same origin to the allowed redirect\n   URLs.\n4. Keep Email/Password enabled in **Authentication → Providers**.\n5. Add these Replit Secrets:\n\n```text\nSUPABASE_URL\nSUPABASE_PUBLISHABLE_KEY\nSUPABASE_SECRET_KEY\n```\n\n`SUPABASE_ANON_KEY` is supported as a legacy fallback for the publishable key,\nand `SUPABASE_SERVICE_ROLE_KEY` remains a legacy fallback for the secret key.\n\nThe publishable key is intentionally returned to the browser so the official\nSupabase JavaScript client can create and refresh user sessions. The secret key\nremains server-side and is used only for the optional one-time migration of the\nold single-workspace data.\n\n### Authorization model\n\n- Supabase Auth manages registration, email confirmation, login, logout and\n  password recovery.\n- The browser sends the authenticated user's access token to the ThesisOS\n  backend.\n- The backend forwards that same user token to Supabase PostgREST.\n- Row Level Security enforces `auth.uid() = user_id`.\n- Anonymous users have no database privileges.\n- The legacy `public.thesisos_state` table remains server-only and can be kept\n  temporarily as a backup.\n\n### Conflict strategy\n\n- `localStorage` remains the offline cache and local fallback.\n- If the browser is empty and the account contains cloud data, the cloud copy\n  is restored.\n- If the account is empty and the browser contains data, the local copy is\n  associated with the account.\n- If both contain data, ThesisOS asks the user to choose which copy should\n  prevail before enabling automatic synchronization.\n- The old workspace can be imported once by an authenticated user who knows\n  the former `THESISOS_SYNC_PASSWORD`.\n\nCloud Sync stores application state only. It does not store broker credentials,\nplace orders or replace tax records.\n"


def fail(message: str) -> None:
    raise SystemExit(f"[FAIL] {message}")


def backup(path: Path, backup_name: str) -> None:
    target = ROOT / backup_name
    if not target.exists():
        shutil.copy2(path, target)
        print(f"[BACKUP] {target.name}")


def patch_server(text: str) -> str:
    if 'AUTH_USER_STATE_TABLE = "thesisos_user_state"' in text:
        print("[SKIP] server.py already contains Supabase Auth")
        return text

    anchor = "\nRADAR_UNIVERSES = {"
    if anchor not in text:
        fail("Could not locate RADAR_UNIVERSES in server.py")
    text = text.replace(
        anchor,
        "\n\n" + AUTH_SERVER_BLOCK + "RADAR_UNIVERSES = {",
        1,
    )

    old = """    def sync_authenticated(self) -> bool:
        return sync_session_from_headers(self.headers) is not None

    def do_GET(self) -> None:
"""
    new = """    def sync_authenticated(self) -> bool:
        return sync_session_from_headers(self.headers) is not None

    def supabase_auth_context(self):
        token = bearer_token_from_headers(self.headers)
        if not token:
            return None, None
        try:
            user = supabase_auth_user(token)
        except (PermissionError, RuntimeError):
            return token, None
        return token, user

    def do_GET(self) -> None:
"""
    if old not in text:
        fail("Could not locate ThesisOSHandler auth helper anchor")
    text = text.replace(old, new, 1)

    old = '        if parsed_url.path == "/api/sync/status":\n'
    new = """        if parsed_url.path == "/api/auth/config":
            config = auth_configuration()
            self.send_json({
                "status": "ok",
                "configured": config["configured"],
                "url": config["url"] if config["configured"] else None,
                "publishable_key": (
                    config["publishable_key"]
                    if config["configured"]
                    else None
                ),
                "provider": config["provider"],
                "table": config["table"],
                "missing": config["missing"],
                "allowed_namespaces": sorted(SYNC_ALLOWED_NAMESPACES),
            })
            return

        if parsed_url.path == "/api/auth/session":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "status": "ok",
                    "authenticated": False,
                    "user": None,
                    "row_count": 0,
                })
                return
            try:
                cloud = fetch_user_cloud_state(user.get("id"), access_token)
                self.send_json({
                    "status": "ok",
                    "authenticated": True,
                    "user": {
                        "id": user.get("id"),
                        "email": user.get("email"),
                        "email_confirmed_at": user.get("email_confirmed_at"),
                        "last_sign_in_at": user.get("last_sign_in_at"),
                    },
                    "row_count": cloud["row_count"],
                    "cloud_metadata": cloud["metadata"],
                })
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível validar a sessão Supabase.",
                    "detail": str(error),
                }, status=502)
            return

        if parsed_url.path == "/api/user/state":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "error": "Inicia sessão com uma conta Supabase."
                }, status=401)
                return
            try:
                payload = fetch_user_cloud_state(user.get("id"), access_token)
                self.send_json({
                    "status": "ok",
                    "user_id": user.get("id"),
                    **payload,
                })
            except PermissionError as error:
                self.send_json({"error": str(error)}, status=401)
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível ler o estado da conta.",
                    "detail": str(error),
                }, status=502)
            return

        if parsed_url.path == "/api/sync/status":
"""
    if old not in text:
        fail("Could not locate GET sync route anchor")
    text = text.replace(old, new, 1)

    old = '        if parsed_url.path == "/api/sync/login":\n'
    new = """        if parsed_url.path == "/api/user/state":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "error": "Inicia sessão com uma conta Supabase."
                }, status=401)
                return
            try:
                body = self.read_json_body()
                result = upsert_user_cloud_state(
                    user.get("id"),
                    access_token,
                    body.get("state"),
                )
                self.send_json({
                    "status": "ok",
                    "user_id": user.get("id"),
                    **result,
                })
            except PermissionError as error:
                self.send_json({"error": str(error)}, status=401)
            except ValueError as error:
                self.send_json({"error": str(error)}, status=400)
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível guardar o estado da conta.",
                    "detail": str(error),
                }, status=502)
            return

        if parsed_url.path == "/api/auth/migrate-legacy":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "error": "Inicia sessão antes de importar o workspace antigo."
                }, status=401)
                return
            try:
                body = self.read_json_body()
                config = sync_configuration()
                supplied = str(body.get("password") or "")
                if (
                    not config["configured"]
                    or not hmac.compare_digest(
                        supplied,
                        config["password"],
                    )
                ):
                    self.send_json({
                        "error": "Palavra-passe do workspace antigo incorreta."
                    }, status=401)
                    return
                result = migrate_legacy_state_to_user(
                    user.get("id"),
                    overwrite=bool(body.get("overwrite")),
                )
                self.send_json({
                    "status": "ok",
                    "user_id": user.get("id"),
                    **result,
                })
            except ValueError as error:
                self.send_json({"error": str(error)}, status=409)
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível importar o workspace antigo.",
                    "detail": str(error),
                }, status=502)
            return

        if parsed_url.path == "/api/sync/login":
"""
    if old not in text:
        fail("Could not locate POST sync route anchor")
    text = text.replace(old, new, 1)
    return text


def patch_index(text: str) -> str:
    if 'id="cloudSyncSignupBtn"' in text:
        print("[SKIP] index.html already contains Supabase Auth")
        return text

    if "</style>" not in text:
        fail("Could not locate </style> in index.html")
    text = text.replace("</style>", AUTH_CSS + "\n  </style>", 1)

    old_top = '<div class="user"><div><strong>Tiago</strong><div id="topbarPolicyLabel" style="font-size:11px;color:var(--muted)">Política por configurar</div></div><div class="avatar">TS</div></div>'
    new_top = '<div class="user"><div><strong id="topbarUserName">Visitante</strong><div id="topbarPolicyLabel" style="font-size:11px;color:var(--muted)">Política por configurar</div></div><div class="avatar" id="topbarUserAvatar">—</div></div>'
    if old_top not in text:
        fail("Could not locate the current topbar user block")
    text = text.replace(old_top, new_top, 1)

    start = text.find(
        '      <section class="card cloud-sync-card" id="cloudSyncCard">'
    )
    end_marker = (
        '      </section>\n'
        '    </section>\n\n'
        '    <p class="prototype-note"'
    )
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        fail("Could not locate the current Cloud Sync card")
    text = (
        text[:start]
        + NEW_CLOUD_CARD
        + text[end + len("      </section>"):]
    )

    old_script = "<script>\n(() => {"
    new_script = (
        '<script src="https://cdn.jsdelivr.net/npm/'
        '@supabase/supabase-js@2"></script>\n'
        "<script>\n(() => {"
    )
    if old_script not in text:
        fail("Could not locate the main inline script")
    text = text.replace(old_script, new_script, 1)

    js_start = text.find(
        "// Secure Supabase persistence with localStorage fallback"
    )
    js_end = text.find("\n\n\n})();", js_start)
    if js_start < 0 or js_end < 0:
        fail("Could not locate the legacy Cloud Sync JavaScript block")
    text = text[:js_start] + AUTH_JS + text[js_end:]
    return text


def patch_readme(text: str) -> str:
    old_heading = "## Secure Cloud Sync with Supabase"
    index = text.find(old_heading)
    if index >= 0:
        text = text[:index] + AUTH_README_SECTION
    elif "## Supabase Auth and per-user Cloud Sync" not in text:
        text = text.rstrip() + "\n\n" + AUTH_README_SECTION

    text = text.replace(
        "- The watchlist, investor policy, portfolio transactions, "
        "decision journal and alerts are stored only in browser `localStorage`.",
        "- The browser keeps a local cache in `localStorage`; authenticated "
        "users can synchronize the supported namespaces to their own "
        "Supabase account.",
    )
    return text


def patch_env_example() -> None:
    if not ENV_EXAMPLE.exists():
        return
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    if "SUPABASE_PUBLISHABLE_KEY" not in text:
        addition = (
            "\n# Supabase Auth (safe/public client key, stored here for "
            "deployment configuration)\n"
            "SUPABASE_PUBLISHABLE_KEY=sb_publishable_your_key_here\n"
            "# Keep the next two only while importing the legacy workspace.\n"
            "THESISOS_SYNC_PASSWORD=replace_for_legacy_migration\n"
            "THESISOS_WORKSPACE_ID=tiago\n"
        )
        ENV_EXAMPLE.write_text(
            text.rstrip() + "\n" + addition,
            encoding="utf-8",
        )
        print("[WRITE] .env.example updated")


def validate(index_text: str, server_text: str) -> None:
    compile(server_text, "server.py", "exec")
    print("[PASS] server.py compiles")

    scripts = []
    position = 0
    while True:
        start = index_text.find("<script>", position)
        if start < 0:
            break
        end = index_text.find("</script>", start)
        if end < 0:
            fail("Unclosed inline script")
        scripts.append(index_text[start + len("<script>"):end])
        position = end + len("</script>")

    if len(scripts) != 1:
        fail(f"Expected one inline script, found {len(scripts)}")

    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".js",
        delete=False,
        encoding="utf-8",
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


def main() -> int:
    for path in (INDEX, SERVER, README):
        if not path.exists():
            fail(f"Missing required file: {path.name}")

    backup(INDEX, "index_before_supabase_auth.html")
    backup(SERVER, "server_before_supabase_auth.py")
    backup(README, "README_before_supabase_auth.md")

    index_text = patch_index(INDEX.read_text(encoding="utf-8"))
    server_text = patch_server(SERVER.read_text(encoding="utf-8"))
    readme_text = patch_readme(README.read_text(encoding="utf-8"))

    validate(index_text, server_text)

    INDEX.write_text(index_text, encoding="utf-8")
    SERVER.write_text(server_text, encoding="utf-8")
    README.write_text(readme_text, encoding="utf-8")
    (ROOT / "supabase_auth_migration.sql").write_text(
        AUTH_SQL,
        encoding="utf-8",
    )
    (ROOT / "test_supabase_auth.py").write_text(
        AUTH_TEST,
        encoding="utf-8",
    )
    patch_env_example()

    print("[WRITE] index.html")
    print("[WRITE] server.py")
    print("[WRITE] README.md")
    print("[WRITE] supabase_auth_migration.sql")
    print("[WRITE] test_supabase_auth.py")
    print()
    print("Next:")
    print("1. Run supabase_auth_migration.sql in Supabase SQL Editor.")
    print("2. Add SUPABASE_PUBLISHABLE_KEY to Replit Secrets.")
    print("3. Configure the public URL in Supabase Auth URL Configuration.")
    print("4. Restart the app and run: python3 test_supabase_auth.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
