from pathlib import Path

index_path = Path("index.html")
readme_path = Path("README.md")

if not index_path.exists():
    raise SystemExit("ERRO: index.html não encontrado.")

html = index_path.read_text(encoding="utf-8")

if 'id="liveDashboardUpdated"' in html:
    print("O Dashboard operacional já está instalado.")
    raise SystemExit(0)

required_markers = [
    'id="evidenceEventsCard"',
    'const PORTFOLIO_TX_KEY=',
    'const DECISION_JOURNAL_KEY=',
    'const MONITORING_ALERTS_KEY=',
    'async function loadOpportunityRadar(',
]

missing = [marker for marker in required_markers if marker not in html]
if missing:
    raise SystemExit(
        "ERRO: a versão atual do index.html não contém todos os módulos "
        "necessários: " + ", ".join(missing)
    )

# ------------------------------------------------------------------
# Dashboard styling
# ------------------------------------------------------------------
css = r'''
    /* ThesisOS live operational dashboard */
    .live-dashboard-actions{display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap}
    .live-dashboard-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
    .live-dashboard-kpi{min-height:126px;position:relative;overflow:hidden}
    .live-dashboard-kpi::after{content:"";position:absolute;width:92px;height:92px;border-radius:50%;right:-32px;top:-36px;background:radial-gradient(circle,rgba(124,156,255,.15),transparent 68%)}
    .live-dashboard-kpi .metric{font-size:27px}
    .live-dashboard-status{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:16px;padding:13px 15px;border:1px solid #2b4365;border-radius:14px;background:#13223a}
    .live-dashboard-health{display:flex;align-items:center;gap:11px}.live-dashboard-health-icon{width:39px;height:39px;border-radius:12px;display:grid;place-items:center;background:#1b3050;border:1px solid #34547d;font-size:17px}
    .live-dashboard-health strong{display:block}.live-dashboard-health small{display:block;color:var(--muted);margin-top:3px}
    .live-dashboard-provider-row{display:flex;gap:6px;flex-wrap:wrap}
    .live-dashboard-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:16px;margin-bottom:16px}
    .live-dashboard-section-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}
    .live-dashboard-list{display:grid;gap:9px}
    .live-dashboard-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:11px 12px;border:1px solid #243650;border-radius:12px;background:#101b2d}
    .live-dashboard-row-main{min-width:0}.live-dashboard-row-main strong{display:block;line-height:1.35}.live-dashboard-row-main small{display:block;color:var(--muted);font-size:11px;margin-top:4px;line-height:1.4;overflow:hidden;text-overflow:ellipsis}
    .live-dashboard-row-value{text-align:right;white-space:nowrap}.live-dashboard-row-value strong{display:block}.live-dashboard-row-value small{display:block;color:var(--muted);margin-top:3px}
    .live-dashboard-empty{padding:27px 16px;border:1px dashed #354b6c;border-radius:14px;text-align:center;color:var(--muted);line-height:1.5}
    .live-dashboard-radar-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    .live-dashboard-candidate{padding:13px;border:1px solid #263a59;border-radius:13px;background:#101b2d}
    .live-dashboard-candidate-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.live-dashboard-candidate h3{margin:0;font-size:14px}.live-dashboard-candidate p{margin:5px 0 0;color:var(--muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .live-dashboard-score-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.live-dashboard-score{padding:7px;border:1px solid #22344d;border-radius:9px;background:#0f1a2c}.live-dashboard-score span{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}.live-dashboard-score strong{display:block;margin-top:3px;font-size:11px}
    .live-dashboard-card-actions{display:flex;gap:6px;margin-top:10px}.live-dashboard-card-actions button{flex:1;padding:7px 8px;font-size:10px}
    .live-dashboard-modules{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
    .live-dashboard-module{padding:12px;border:1px solid #263a59;border-radius:12px;background:#101b2d}.live-dashboard-module span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.05em}.live-dashboard-module strong{display:block;margin-top:6px;font-size:13px}.live-dashboard-module small{display:block;color:var(--muted);font-size:10px;margin-top:5px;line-height:1.4}
    .live-dashboard-event{border-left:3px solid var(--accent)}.live-dashboard-event.material{border-left-color:var(--warn)}.live-dashboard-event.critical{border-left-color:var(--danger)}
    .live-dashboard-review.overdue{border-color:#6a3340}.live-dashboard-review.soon{border-color:#6a4f25}
    .live-dashboard-spinner{width:14px;height:14px;border-radius:50%;border:2px solid #334a6c;border-top-color:#fff;animation:radarSpin .8s linear infinite;display:inline-block;vertical-align:-2px;margin-right:6px}
    @media(max-width:1150px){.live-dashboard-kpis{grid-template-columns:repeat(2,1fr)}.live-dashboard-modules{grid-template-columns:repeat(3,1fr)}.live-dashboard-radar-grid{grid-template-columns:1fr 1fr}}
    @media(max-width:850px){.live-dashboard-grid{grid-template-columns:1fr}.live-dashboard-radar-grid{grid-template-columns:1fr}.live-dashboard-modules{grid-template-columns:repeat(2,1fr)}}
    @media(max-width:620px){.live-dashboard-kpis,.live-dashboard-modules{grid-template-columns:1fr}.live-dashboard-actions{justify-content:flex-start}.live-dashboard-status{align-items:flex-start}.live-dashboard-provider-row{width:100%}}
'''

if "</style>" not in html:
    raise SystemExit("ERRO: não encontrei </style>.")
html = html.replace("</style>", css + "\n  </style>", 1)

# ------------------------------------------------------------------
# Replace the demonstrative dashboard
# ------------------------------------------------------------------
dashboard_start = html.find('<section id="dashboard" class="page active">')
radar_start = html.find('<section id="radar" class="page">', dashboard_start)

if dashboard_start < 0 or radar_start < 0:
    raise SystemExit("ERRO: não encontrei os limites do Dashboard atual.")

new_dashboard = r'''<section id="dashboard" class="page active">
      <div class="hero">
        <div>
          <h1>Centro operacional</h1>
          <p class="sub">Carteira, Radar, decisões, alertas e evidência num único resumo — sem valores demonstrativos.</p>
        </div>
        <div class="live-dashboard-actions">
          <span class="freshness warn" id="liveDashboardUpdated">Ainda não atualizado</span>
          <button class="ghost" id="dashboardOpenAnalysisBtn" type="button">Analisar ativo</button>
          <button class="primary" id="dashboardRefreshAllBtn" type="button">Atualizar tudo</button>
        </div>
      </div>

      <div class="live-dashboard-status">
        <div class="live-dashboard-health">
          <div class="live-dashboard-health-icon" id="dashboardHealthIcon">⋯</div>
          <div><strong id="dashboardHealthTitle">A verificar o ThesisOS API</strong><small id="dashboardHealthDetail">O estado das integrações aparecerá aqui.</small></div>
        </div>
        <div class="live-dashboard-provider-row" id="dashboardProviderRow"><span class="chip">A aguardar API</span></div>
      </div>

      <div class="live-dashboard-kpis">
        <div class="card live-dashboard-kpi"><div class="metric-label">Valor da carteira</div><div class="metric" id="dashboardPortfolioValue">€0,00</div><div class="delta" id="dashboardPortfolioDetail">Sem posições registadas</div></div>
        <div class="card live-dashboard-kpi"><div class="metric-label">Candidatas do último Radar</div><div class="metric" id="dashboardCandidateCount">0</div><div class="delta" id="dashboardRadarDetail">Radar ainda não executado</div></div>
        <div class="card live-dashboard-kpi"><div class="metric-label">Alertas ativados</div><div class="metric" id="dashboardTriggeredCount">0</div><div class="delta" id="dashboardAlertDetail">Sem alertas configurados</div></div>
        <div class="card live-dashboard-kpi"><div class="metric-label">Próxima revisão</div><div class="metric" id="dashboardNextReview">—</div><div class="delta" id="dashboardReviewDetail">Sem revisões agendadas</div></div>
      </div>

      <div class="live-dashboard-grid">
        <section class="card">
          <div class="live-dashboard-section-head"><div><h2>Carteira atual</h2><p class="sub">Valor, pesos e cobertura das cotações guardadas.</p></div><button class="link-btn" data-go="myportfolio">Abrir carteira</button></div>
          <div class="live-dashboard-list" id="dashboardPortfolioPositions"></div>
        </section>
        <section class="card">
          <div class="live-dashboard-section-head"><div><h2>Alertas que exigem atenção</h2><p class="sub">Ativados, com erro ou próximos da revisão.</p></div><button class="link-btn" data-go="alerts">Abrir monitorização</button></div>
          <div class="live-dashboard-list" id="dashboardAttentionList"></div>
        </section>
      </div>

      <section class="card" style="margin-bottom:16px">
        <div class="live-dashboard-section-head"><div><h2>Melhores candidatas do último Radar</h2><p class="sub" id="dashboardRadarMeta">Executa o Radar para gerar um ranking atual.</p></div><button class="link-btn" data-go="radar">Abrir Radar</button></div>
        <div class="live-dashboard-radar-grid" id="dashboardRadarCandidates"></div>
      </section>

      <div class="live-dashboard-grid">
        <section class="card">
          <div class="live-dashboard-section-head"><div><h2>Próximas revisões da tese</h2><p class="sub">Última versão guardada por ativo.</p></div><button class="link-btn" data-go="journal">Abrir Journal</button></div>
          <div class="live-dashboard-list" id="dashboardReviewList"></div>
        </section>
        <section class="card">
          <div class="live-dashboard-section-head"><div><h2>Evidência material recente</h2><p class="sub">Último Evidence Feed consultado.</p></div><button class="link-btn" data-go="alerts">Abrir Evidence Feed</button></div>
          <div class="live-dashboard-list" id="dashboardEvidenceList"></div>
        </section>
      </div>

      <section class="card">
        <div class="live-dashboard-section-head"><div><h2>Estado dos módulos</h2><p class="sub">Persistência local, cobertura e última atualização conhecida.</p></div></div>
        <div class="live-dashboard-modules" id="dashboardModuleStatus"></div>
      </section>
    </section>

    '''

html = html[:dashboard_start] + new_dashboard + html[radar_start:]

# ------------------------------------------------------------------
# Navigation hook
# ------------------------------------------------------------------
old_nav = '''    if(id==="watchlist") renderWatchlist();
    if(id==="myportfolio") renderPortfolioManager();'''
new_nav = '''    if(id==="dashboard"){
      renderLiveDashboard();
      if(!window.dashboardHealthCheckedAt || Date.now()-window.dashboardHealthCheckedAt>60000){
        void dashboardCheckHealth();
      }
    }
    if(id==="watchlist") renderWatchlist();
    if(id==="myportfolio") renderPortfolioManager();'''

if old_nav not in html:
    raise SystemExit("ERRO: não encontrei o hook de navegação.")
html = html.replace(old_nav, new_nav, 1)

# ------------------------------------------------------------------
# Persist Radar and Evidence results
# ------------------------------------------------------------------
old_radar = '''window.lastRadarData=data;renderRadarResults(data);'''
new_radar = '''window.lastRadarData=data;try{localStorage.setItem(DASHBOARD_RADAR_KEY,JSON.stringify(data));}catch{}renderRadarResults(data);renderLiveDashboard();'''
if old_radar not in html:
    raise SystemExit("ERRO: não encontrei o resultado do Radar.")
html = html.replace(old_radar, new_radar, 1)

old_evidence = '''  function renderEvidenceSnapshot(evidence){
    if(!evidence){resetEvidenceSnapshot();return;}'''
new_evidence = '''  function renderEvidenceSnapshot(evidence){
    if(!evidence){resetEvidenceSnapshot();return;}
    try{localStorage.setItem(DASHBOARD_EVIDENCE_KEY,JSON.stringify(evidence));}catch{}
    renderLiveDashboard();'''
if old_evidence not in html:
    raise SystemExit("ERRO: não encontrei renderEvidenceSnapshot.")
html = html.replace(old_evidence, new_evidence, 1)

old_feed = '''  function renderEvidenceFeed(evidence){
    currentEvidenceFeed=evidence;'''
new_feed = '''  function renderEvidenceFeed(evidence){
    currentEvidenceFeed=evidence;
    if(evidence){try{localStorage.setItem(DASHBOARD_EVIDENCE_KEY,JSON.stringify(evidence));}catch{}renderLiveDashboard();}'''
if old_feed not in html:
    raise SystemExit("ERRO: não encontrei renderEvidenceFeed.")
html = html.replace(old_feed, new_feed, 1)

# Keep dashboard synchronized with local state changes.
replacements = {
    'function saveWatchlist(items){localStorage.setItem(WATCHLIST_KEY,JSON.stringify(items));updateWatchlistCount();}':
    'function saveWatchlist(items){localStorage.setItem(WATCHLIST_KEY,JSON.stringify(items));updateWatchlistCount();renderLiveDashboard();}',
    'function savePortfolioTransactions(items){portfolioWrite(PORTFOLIO_TX_KEY,items);renderPortfolioManager();}':
    'function savePortfolioTransactions(items){portfolioWrite(PORTFOLIO_TX_KEY,items);renderPortfolioManager();renderLiveDashboard();}',
    'function savePortfolioQuotes(items){portfolioWrite(PORTFOLIO_QUOTES_KEY,items);}':
    'function savePortfolioQuotes(items){portfolioWrite(PORTFOLIO_QUOTES_KEY,items);renderLiveDashboard();}',
    'function saveDecisionJournal(items){decisionWrite(DECISION_JOURNAL_KEY,items);renderDecisionJournal();}':
    'function saveDecisionJournal(items){decisionWrite(DECISION_JOURNAL_KEY,items);renderDecisionJournal();renderLiveDashboard();}',
    'function saveMonitoringAlerts(items){decisionWrite(MONITORING_ALERTS_KEY,items);renderMonitoringAlerts();}':
    'function saveMonitoringAlerts(items){decisionWrite(MONITORING_ALERTS_KEY,items);renderMonitoringAlerts();renderLiveDashboard();}',
}
for old, new in replacements.items():
    if old not in html:
        raise SystemExit(f"ERRO: não encontrei o sincronizador esperado: {old[:55]}")
    html = html.replace(old, new, 1)

# ------------------------------------------------------------------
# Dashboard logic — inserted after all local data modules exist
# ------------------------------------------------------------------
dashboard_js = r'''
  // ---------------------------------------------------------------
  // Live operational dashboard
  // ---------------------------------------------------------------
  const DASHBOARD_RADAR_KEY="thesisos_last_radar_v1";
  const DASHBOARD_EVIDENCE_KEY="thesisos_last_evidence_v1";
  let dashboardRefreshRunning=false;

  function dashboardRead(key,fallback=null){
    try{return JSON.parse(localStorage.getItem(key)) ?? fallback;}catch{return fallback;}
  }
  function dashboardPercent(value,digits=1){
    const number=Number(value);return Number.isFinite(number)?`${new Intl.NumberFormat("pt-PT",{maximumFractionDigits:digits}).format(number)}%`:"—";
  }
  function dashboardSafeDate(value){
    if(!value)return null;const date=new Date(String(value).length===10?`${value}T00:00:00`:value);return Number.isNaN(date.getTime())?null:date;
  }
  function dashboardDaysUntil(value){
    const date=dashboardSafeDate(value);if(!date)return null;const today=new Date();today.setHours(0,0,0,0);date.setHours(0,0,0,0);return Math.ceil((date-today)/86400000);
  }
  function dashboardRelativeReview(days){
    if(days===null)return "Sem data";if(days<0)return `Vencida há ${Math.abs(days)} dia${Math.abs(days)===1?"":"s"}`;if(days===0)return "Hoje";if(days===1)return "Amanhã";return `Em ${days} dias`;
  }
  function dashboardLatestDecisions(){
    const map=new Map();for(const item of getDecisionJournal()){const current=map.get(item.symbol);if(!current||Number(item.version||0)>Number(current.version||0)||(item.version===current.version&&String(item.created_at)>String(current.created_at)))map.set(item.symbol,item);}return [...map.values()];
  }
  function dashboardPortfolioRisk(snapshot){
    if(!snapshot.positions.length)return {label:"Sem dados",detail:"Adiciona posições à carteira",class:"warn"};
    const largest=snapshot.positions[0]?.weight||0,speculative=snapshot.positions.filter(item=>item.category==="Especulativa").reduce((sum,item)=>sum+item.weight,0),unquoted=snapshot.positions.length-snapshot.quoted;
    if(snapshot.cash<0||largest>55||speculative>10)return {label:"Elevado",detail:`Maior posição ${dashboardPercent(largest)} · especulativas ${dashboardPercent(speculative)}`,class:"bad"};
    if(largest>35||speculative>5||unquoted>0)return {label:"Moderado",detail:`Maior posição ${dashboardPercent(largest)} · ${unquoted} sem cotação`,class:"warn"};
    return {label:"Controlado",detail:`Maior posição ${dashboardPercent(largest)} · cobertura ${snapshot.quoted}/${snapshot.positions.length}`,class:""};
  }
  function dashboardReadRadar(){return window.lastRadarData||dashboardRead(DASHBOARD_RADAR_KEY,null);}
  function dashboardReadEvidence(){return window.currentAnalysisPayload?.evidence||currentEvidenceFeed||dashboardRead(DASHBOARD_EVIDENCE_KEY,null);}

  function renderLiveDashboard(){
    if(!$("#dashboardPortfolioValue"))return;
    const portfolio=portfolioSnapshot(),alerts=getMonitoringAlerts(),decisions=dashboardLatestDecisions(),watchlist=getWatchlist(),radar=dashboardReadRadar(),evidence=dashboardReadEvidence();
    const triggered=alerts.filter(item=>item.status==="triggered"),errors=alerts.filter(item=>item.status==="error"),pending=alerts.filter(item=>item.status==="pending"),risk=dashboardPortfolioRisk(portfolio);

    $("#dashboardPortfolioValue").textContent=portfolioEuro(portfolio.totalValue);
    $("#dashboardPortfolioDetail").textContent=portfolio.positions.length?`${portfolio.positions.length} posições · P/L ${portfolio.unrealized>=0?"+":""}${portfolioEuro(portfolio.unrealized)} · risco ${risk.label}`:"Sem posições registadas";
    $("#dashboardPortfolioDetail").className=`delta ${risk.class||""}`;

    const radarResults=radar?.results||[],candidates=radarResults.filter(item=>item.status?.code==="candidate"&&!item.event_review_required);
    $("#dashboardCandidateCount").textContent=String(candidates.length);
    $("#dashboardRadarDetail").textContent=radar?`${radar.analysed_count??radarResults.length} analisados · ${radar.error_count??0} erros${radar.cached?" · cache":""}`:"Radar ainda não executado";
    $("#dashboardRadarDetail").className=`delta ${radar?.error_count?"warn":""}`;

    $("#dashboardTriggeredCount").textContent=String(triggered.length);
    $("#dashboardAlertDetail").textContent=alerts.length?`${pending.length} pendentes · ${errors.length} com erro`:`Sem alertas configurados`;
    $("#dashboardAlertDetail").className=`delta ${triggered.length?"bad":errors.length?"warn":""}`;

    const dated=decisions.filter(item=>item.review_date).map(item=>({...item,days:dashboardDaysUntil(item.review_date)})).sort((a,b)=>a.days-b.days),next=dated[0];
    $("#dashboardNextReview").textContent=next?dashboardRelativeReview(next.days):"—";
    $("#dashboardReviewDetail").textContent=next?`${next.symbol} · ${DECISION_ACTIONS[next.action]||next.action} · v${next.version}`:"Sem revisões agendadas";
    $("#dashboardReviewDetail").className=`delta ${next?.days<0?"bad":next?.days<=14?"warn":""}`;

    renderDashboardPortfolio(portfolio);
    renderDashboardAttention(alerts,dated);
    renderDashboardRadar(radar);
    renderDashboardReviews(dated);
    renderDashboardEvidence(evidence);
    renderDashboardModules({portfolio,alerts,decisions,watchlist,radar,evidence});

    const timestamps=[...(Object.values(getPortfolioQuotes()).map(item=>item.updated_at)),radar?.generated_at,evidence?.generated_at,...alerts.map(item=>item.last_checked)].filter(Boolean).sort();
    $("#liveDashboardUpdated").textContent=timestamps.length?`Último dado ${formatAssetDate(timestamps.at(-1))}`:"Sem atualizações guardadas";
    $("#liveDashboardUpdated").className=`freshness ${timestamps.length?"":"warn"}`;
  }

  function renderDashboardPortfolio(snapshot){
    const container=$("#dashboardPortfolioPositions");if(!snapshot.positions.length){container.innerHTML='<div class="live-dashboard-empty"><strong>A carteira está vazia.</strong><br>Regista uma compra ou carrega a demonstração no Portfolio Manager.</div>';return;}
    container.innerHTML=snapshot.positions.slice(0,5).map(item=>`<div class="live-dashboard-row"><div class="live-dashboard-row-main"><strong>${portfolioEscape(item.symbol)} · ${portfolioEscape(item.name)}</strong><small>${portfolioEscape(item.category)} · ${portfolioNumber(item.quantity,4)} unidades${item.quote?.updated_at?` · cotação ${formatAssetDate(item.quote.updated_at)}`:" · sem cotação atual"}</small></div><div class="live-dashboard-row-value"><strong>${portfolioEuro(item.market_value_eur)}</strong><small>${dashboardPercent(item.weight)} · <span class="${item.pnl_eur>=0?"portfolio-pnl-positive":"portfolio-pnl-negative"}">${item.pnl_eur>=0?"+":""}${portfolioEuro(item.pnl_eur)}</span></small></div></div>`).join("");
  }

  function renderDashboardAttention(alerts,reviews){
    const container=$("#dashboardAttentionList"),items=[];
    alerts.filter(item=>item.status==="triggered"||item.status==="error").slice(0,4).forEach(item=>items.push({kind:item.status,title:`${item.symbol} · ${ALERT_LABELS[item.type]||item.type}`,detail:item.status==="error"?(item.error||"Indicador indisponível"):(item.note||"Condição atingida"),value:item.status==="triggered"?"Ativado":"Erro",symbol:item.symbol,exchange:item.exchange}));
    reviews.filter(item=>item.days<=14).slice(0,3).forEach(item=>items.push({kind:item.days<0?"error":"pending",title:`${item.symbol} · revisão da tese`,detail:`Versão ${item.version} · ${DECISION_ACTIONS[item.action]||item.action}`,value:dashboardRelativeReview(item.days),symbol:item.symbol,exchange:item.exchange}));
    if(!items.length){container.innerHTML='<div class="live-dashboard-empty"><strong>Sem itens críticos.</strong><br>Os alertas e datas de revisão não exigem ação imediata.</div>';return;}
    container.innerHTML=items.slice(0,6).map(item=>`<button class="live-dashboard-row ${item.kind==="error"?"live-dashboard-review overdue":item.kind==="pending"?"live-dashboard-review soon":""}" data-dashboard-analyze="${portfolioEscape(item.symbol||"")}" data-dashboard-exchange="${portfolioEscape(item.exchange||"")}" type="button"><span class="live-dashboard-row-main"><strong>${portfolioEscape(item.title)}</strong><small>${portfolioEscape(item.detail)}</small></span><span class="live-dashboard-row-value"><strong>${portfolioEscape(item.value)}</strong><small>Abrir análise</small></span></button>`).join("");
  }

  function renderDashboardRadar(radar){
    const container=$("#dashboardRadarCandidates"),meta=$("#dashboardRadarMeta"),results=[...(radar?.results||[])].sort((a,b)=>(Number(b.radar_score)||-1)-(Number(a.radar_score)||-1)).slice(0,3);
    if(!results.length){container.innerHTML='<div class="live-dashboard-empty" style="grid-column:1/-1"><strong>Sem ranking guardado.</strong><br>Executa o Opportunity Radar para preencher este bloco.</div>';meta.textContent="Executa o Radar para gerar um ranking atual.";return;}
    meta.textContent=`${radar.analysed_count??results.length} ativos · ${radar.universe||"universo personalizado"} · ${radar.generated_at?formatAssetDate(radar.generated_at):"sem data"}`;
    container.innerHTML=results.map(item=>`<article class="live-dashboard-candidate"><div class="live-dashboard-candidate-head"><div><h3>${portfolioEscape(item.symbol)}</h3><p>${portfolioEscape(item.name||item.symbol)}</p></div><span class="pill ${item.event_review_required?"warn":item.status?.code==="candidate"?"":"warn"}">${portfolioEscape(item.event_review_required?"Rever evento":item.status?.label||"Sem estado")}</span></div><div class="live-dashboard-score-grid"><div class="live-dashboard-score"><span>Radar</span><strong>${Number.isFinite(Number(item.radar_score))?`${Math.round(item.radar_score)}/100`:"—"}</strong></div><div class="live-dashboard-score"><span>Valuation</span><strong>${Number.isFinite(Number(item.valuation_score))?Math.round(item.valuation_score):"—"}</strong></div><div class="live-dashboard-score"><span>Técnica</span><strong>${Number.isFinite(Number(item.technical_score))?Math.round(item.technical_score):"—"}</strong></div></div><div class="live-dashboard-card-actions"><button class="ghost" data-dashboard-analyze="${portfolioEscape(item.analysis_query||item.symbol)}" data-dashboard-exchange="${portfolioEscape(item.analysis_exchange||"")}" data-dashboard-ticker="${portfolioEscape(item.analysis_ticker||item.symbol)}">Analisar</button><button class="ghost" data-dashboard-watch="${encodeURIComponent(JSON.stringify(item))}">Watchlist</button></div></article>`).join("");
  }

  function renderDashboardReviews(reviews){
    const container=$("#dashboardReviewList");if(!reviews.length){container.innerHTML='<div class="live-dashboard-empty"><strong>Sem revisões agendadas.</strong><br>Guarda uma decisão no Investment Journal e define a próxima revisão.</div>';return;}
    container.innerHTML=reviews.slice(0,6).map(item=>`<button class="live-dashboard-row live-dashboard-review ${item.days<0?"overdue":item.days<=14?"soon":""}" data-dashboard-journal="${portfolioEscape(item.id)}" type="button"><span class="live-dashboard-row-main"><strong>${portfolioEscape(item.symbol)} · ${portfolioEscape(DECISION_ACTIONS[item.action]||item.action)}</strong><small>${portfolioEscape(item.name||item.symbol)} · versão ${item.version} · ${portfolioEscape(item.review_date)}</small></span><span class="live-dashboard-row-value"><strong>${portfolioEscape(dashboardRelativeReview(item.days))}</strong><small>Abrir tese</small></span></button>`).join("");
  }

  function renderDashboardEvidence(evidence){
    const container=$("#dashboardEvidenceList"),events=(evidence?.events||[]).filter(item=>["critical","high"].includes(item.materiality)).slice(0,6);
    if(!events.length){container.innerHTML='<div class="live-dashboard-empty"><strong>Sem eventos materiais guardados.</strong><br>Analisa uma ação ou usa o Evidence Feed para atualizar este bloco.</div>';return;}
    container.innerHTML=events.map(item=>`<a class="live-dashboard-row live-dashboard-event ${item.materiality==="critical"?"critical":"material"}" href="${portfolioEscape(item.url||"#")}" ${item.url?'target="_blank" rel="noopener noreferrer"':''}><span class="live-dashboard-row-main"><strong>${portfolioEscape(item.headline||"Evento material")}</strong><small>${portfolioEscape(item.event_date||"Sem data")} · ${portfolioEscape(item.source||"Fonte")}</small></span><span class="live-dashboard-row-value"><strong>${portfolioEscape(evidenceMaterialityLabel(item.materiality))}</strong><small>${portfolioEscape(item.category_label||item.form||"")}</small></span></a>`).join("");
  }

  function renderDashboardModules({portfolio,alerts,decisions,watchlist,radar,evidence}){
    const modules=[
      ["Portfolio",portfolio.positions.length?`${portfolio.positions.length} posições`:"Vazio",`${portfolio.quoted}/${portfolio.positions.length} cotações`],
      ["Radar",radar?`${radar.analysed_count??radar.results?.length??0} analisados`:"Não executado",radar?.generated_at?formatAssetDate(radar.generated_at):"Sem snapshot"],
      ["Watchlist",`${watchlist.length} ativos`,watchlist.length?"Persistência local":"Sem ativos"],
      ["Journal",`${decisions.length} teses`,decisions.length?"Versões guardadas":"Sem decisões"],
      ["Alertas",`${alerts.length} regras`,`${alerts.filter(item=>item.status==="triggered").length} ativadas`],
      ["Evidence",evidence?`${evidence.material_events_count??0} materiais`:"Sem feed",evidence?.generated_at?formatAssetDate(evidence.generated_at):"Sem snapshot"],
    ];
    $("#dashboardModuleStatus").innerHTML=modules.map(([label,value,detail])=>`<div class="live-dashboard-module"><span>${portfolioEscape(label)}</span><strong>${portfolioEscape(value)}</strong><small>${portfolioEscape(detail)}</small></div>`).join("");
  }

  async function dashboardCheckHealth(){
    const icon=$("#dashboardHealthIcon"),title=$("#dashboardHealthTitle"),detail=$("#dashboardHealthDetail"),providers=$("#dashboardProviderRow");
    icon.textContent="…";title.textContent="A verificar o ThesisOS API";detail.textContent="A confirmar o servidor e os fornecedores configurados.";
    try{const response=await fetch("/api/health",{cache:"no-store"}),data=await response.json();if(!response.ok)throw new Error(data.error||"API indisponível");icon.textContent="✓";title.textContent=`${data.service||"ThesisOS API"} operacional`;detail.textContent="O servidor respondeu corretamente. A disponibilidade individual de cada fornecedor é confirmada durante as análises.";providers.innerHTML=(data.providers||[]).map(item=>`<span class="chip">${portfolioEscape(item)}</span>`).join("")||'<span class="chip">Servidor operacional</span>';window.dashboardHealthCheckedAt=Date.now();}
    catch(error){icon.textContent="!";title.textContent="ThesisOS API indisponível";detail.textContent=error.message;providers.innerHTML='<span class="pill bad">Verificar servidor</span>';}
  }

  async function dashboardRefreshAll(){
    if(dashboardRefreshRunning)return;dashboardRefreshRunning=true;const button=$("#dashboardRefreshAllBtn"),original=button.textContent;button.disabled=true;button.innerHTML='<span class="live-dashboard-spinner"></span>A atualizar';
    try{
      await dashboardCheckHealth();
      if(portfolioSnapshot().positions.length)await portfolioRefreshQuotes();
      if(getMonitoringAlerts().length)await refreshMonitoringAlerts();
      const stored=dashboardReadRadar();if(stored){if(stored.universe&&$("#radarUniverse"))$("#radarUniverse").value=["core_us","quality_us","growth_us"].includes(stored.universe)?stored.universe:"core_us";await loadOpportunityRadar(true);}
      renderLiveDashboard();showToast("Dashboard atualizado.");
    }catch(error){showToast(error.message||"Atualização incompleta.");}
    finally{dashboardRefreshRunning=false;button.disabled=false;button.textContent=original;}
  }

  $("#dashboardRefreshAllBtn")?.addEventListener("click",dashboardRefreshAll);
  $("#dashboardOpenAnalysisBtn")?.addEventListener("click",()=>handlePageOpen("analysis"));
  document.addEventListener("click",event=>{
    const analyze=event.target.closest("[data-dashboard-analyze]");if(analyze){void loadUnifiedAnalysis(analyze.dataset.dashboardAnalyze,{exchange:analyze.dataset.dashboardExchange||undefined,ticker:analyze.dataset.dashboardTicker||undefined});return;}
    const journal=event.target.closest("[data-dashboard-journal]");if(journal){selectedDecisionId=journal.dataset.dashboardJournal;handlePageOpen("journal");return;}
    const watch=event.target.closest("[data-dashboard-watch]");if(watch){try{const item=JSON.parse(decodeURIComponent(watch.dataset.dashboardWatch));addWatchlistRecord({symbol:item.symbol,name:item.name,asset_type:item.asset_type,exchange:item.analysis_exchange||item.exchange,ticker:item.analysis_ticker||item.symbol,price:item.price,currency:item.currency,added_at:new Date().toISOString()});}catch{showToast("Não foi possível guardar o ativo.");}}
  });
  renderLiveDashboard();
  void dashboardCheckHealth();

'''

js_anchor = '''  // ---------------------------------------------------------------
  // Portfolio Builder
  // ---------------------------------------------------------------'''
if js_anchor not in html:
    raise SystemExit("ERRO: não encontrei o ponto de inserção do Dashboard JS.")
html = html.replace(js_anchor, dashboard_js + js_anchor, 1)

index_path.write_text(html, encoding="utf-8")

# ------------------------------------------------------------------
# README and a lightweight static test
# ------------------------------------------------------------------
if readme_path.exists():
    readme = readme_path.read_text(encoding="utf-8")
    capability = "- Live operational dashboard combining portfolio, Radar, alerts, thesis reviews, evidence and API health."
    if capability not in readme:
        marker = "## Data sources"
        position = readme.find(marker)
        if position >= 0:
            readme = readme[:position] + capability + "\n\n" + readme[position:]
        else:
            readme += "\n" + capability + "\n"
    readme = readme.replace(
        "1. Search `AAPL` to demonstrate stock fundamentals, cash flow, debt and technical analysis.",
        "1. Open the live Dashboard to show portfolio, Radar, alerts, reviews, evidence and API status.\n2. Search `AAPL` to demonstrate stock fundamentals, cash flow, debt and technical analysis.",
    )
    # Renumber the remaining demo lines only if the dashboard line was added.
    if "1. Open the live Dashboard" in readme:
        for old, new in [("2. Search `VWCE`", "3. Search `VWCE`"), ("3. Open **Opportunity Radar**", "4. Open **Opportunity Radar**"), ("4. Add an asset", "5. Add an asset"), ("5. Compare `MSFT`", "6. Compare `MSFT`"), ("6. Add AAPL", "7. Add AAPL"), ("7. Save the current", "8. Save the current"), ("8. In an analysis", "9. In an analysis")]:
            readme = readme.replace(old, new, 1)
    readme_path.write_text(readme, encoding="utf-8")

static_test = '''from pathlib import Path\n\nhtml = Path("index.html").read_text(encoding="utf-8")\nrequired = [\n    'id="liveDashboardUpdated"',\n    'id="dashboardPortfolioValue"',\n    'id="dashboardRadarCandidates"',\n    'id="dashboardAttentionList"',\n    'id="dashboardEvidenceList"',\n    'function renderLiveDashboard()',\n    'async function dashboardCheckHealth()',\n]\nmissing = [item for item in required if item not in html]\nforbidden = ["€18.450", "4 oportunidades a aproximar-se", "Supabase + motor próprio"]\npresent_forbidden = [item for item in forbidden if item in html]\nif missing or present_forbidden:\n    raise SystemExit({"missing": missing, "demonstrative_content": present_forbidden})\nprint("Live Dashboard: PASS")\n'''
Path("test_live_dashboard.py").write_text(static_test, encoding="utf-8")

print("SUCESSO: Dashboard operacional instalado.")
print("Foram atualizados index.html e README.md.")
print("Foi criado test_live_dashboard.py.")
