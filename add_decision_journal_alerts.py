from pathlib import Path

index_path = Path("index.html")
readme_path = Path("README.md")

if not index_path.exists():
    raise SystemExit("ERRO: index.html não encontrado.")

html = index_path.read_text(encoding="utf-8")

if 'id="decisionJournalModal"' in html:
    print("O Decision Journal e os alertas funcionais já estão instalados.")
    raise SystemExit(0)

css = r'''
    /* ThesisOS functional decision journal and monitoring alerts */
    .decision-toolbar{display:flex;gap:8px;flex-wrap:wrap}
    .decision-kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
    .decision-workspace{display:grid;grid-template-columns:minmax(340px,.85fr) minmax(0,1.15fr);gap:16px}
    .decision-filter-row{display:grid;grid-template-columns:1fr 180px 160px;gap:9px;margin-bottom:12px}
    .decision-list{display:grid;gap:10px;max-height:720px;overflow:auto;padding-right:3px}
    .decision-entry-button{width:100%;padding:13px;border:1px solid #263a59;border-radius:13px;background:#101b2d;color:var(--text);text-align:left;transition:.18s ease}
    .decision-entry-button:hover,.decision-entry-button.active{border-color:#5577ad;background:#14243b;transform:translateY(-1px)}
    .decision-entry-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
    .decision-entry-button p{margin:7px 0 0;color:var(--muted);font-size:12px;line-height:1.45}
    .decision-entry-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
    .decision-detail-empty{padding:40px 20px;text-align:center;color:var(--muted);border:1px dashed #354b6c;border-radius:15px}
    .decision-detail-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:14px}
    .decision-detail-actions{display:flex;gap:7px;flex-wrap:wrap}
    .decision-plan-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    .decision-plan-card{padding:13px;border:1px solid #263a59;border-radius:12px;background:#101b2d}
    .decision-plan-card span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px}
    .decision-plan-card strong{display:block;line-height:1.35}
    .decision-notes-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
    .decision-note-box{padding:13px;border:1px solid #263a59;border-radius:12px;background:#101b2d}
    .decision-note-box h3{margin-bottom:7px}.decision-note-box p{margin:0;color:var(--muted);font-size:12px;white-space:pre-wrap;line-height:1.55}
    .decision-snapshot{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:12px}
    .decision-snapshot .mini{margin:0}
    .decision-version-list{display:grid;gap:8px;margin-top:12px}
    .decision-version-row{display:grid;grid-template-columns:80px 1fr auto;gap:10px;align-items:center;padding:10px;border-radius:10px;background:#0f1a2c;border:1px solid #22344d;font-size:12px}
    .monitoring-toolbar{display:flex;gap:8px;flex-wrap:wrap}
    .monitoring-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
    .monitoring-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .monitoring-card{padding:15px;border:1px solid #263a59;border-radius:14px;background:#111d30}
    .monitoring-card.triggered{border-color:#80515c;box-shadow:0 0 18px rgba(241,112,127,.10)}
    .monitoring-card.error{border-color:#644d27}.monitoring-card.pending{border-color:#2f4668}
    .monitoring-card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
    .monitoring-card p{margin:7px 0;color:var(--muted);font-size:12px;line-height:1.5}
    .monitoring-card-footer{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-top:10px;padding-top:10px;border-top:1px solid #22344d}
    .monitoring-actions{display:flex;gap:6px}.monitoring-actions button{padding:6px 8px;font-size:10px}
    .monitoring-empty{grid-column:1/-1;padding:38px 20px;text-align:center;color:var(--muted);border:1px dashed #354b6c;border-radius:15px}
    .decision-modal-overlay{position:fixed;inset:0;z-index:11200;display:none;align-items:center;justify-content:center;padding:20px;background:rgba(2,8,23,.84);backdrop-filter:blur(8px)}
    .decision-modal-overlay.open{display:flex}
    .decision-modal{width:min(860px,100%);max-height:92vh;overflow:auto;border:1px solid var(--border);border-radius:20px;background:var(--panel);box-shadow:0 30px 90px rgba(0,0,0,.5)}
    .decision-modal-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;padding:18px 20px;border-bottom:1px solid var(--border)}
    .decision-modal-body{padding:18px 20px 20px}
    .decision-modal-close{width:36px;height:36px;padding:0;border-radius:11px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:20px}
    .decision-modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}
    .decision-suggestion{margin:0 0 14px;padding:13px 14px;border:1px solid #345079;border-radius:13px;background:#132139;color:#c4d2e7;font-size:12px;line-height:1.5}
    .decision-suggestion strong{color:#fff}
    .decision-textarea{width:100%;min-height:92px;margin-top:6px;padding:12px;border-radius:11px;border:1px solid #2a3b59;background:#101b2d;color:#fff;resize:vertical}
    .decision-check{display:flex;gap:9px;align-items:flex-start;margin-top:12px;color:#b8c5d8;font-size:12px}.decision-check input{width:auto;margin:2px 0 0}
    @media(max-width:1050px){.decision-workspace{grid-template-columns:1fr}.decision-kpi-grid,.monitoring-summary{grid-template-columns:repeat(2,1fr)}}
    @media(max-width:760px){.decision-kpi-grid,.monitoring-summary,.monitoring-grid,.decision-plan-grid,.decision-snapshot,.decision-notes-grid{grid-template-columns:1fr}.decision-filter-row{grid-template-columns:1fr}.decision-detail-head{flex-direction:column}.decision-version-row{grid-template-columns:65px 1fr}.decision-version-row button{grid-column:1/-1}}
'''

if "</style>" not in html:
    raise SystemExit("ERRO: não encontrei </style>.")
html = html.replace("</style>", css + "\n  </style>", 1)

old_actions = '''            <button class="ghost" id="analysisWatchBtn" type="button">☆ Watchlist</button>
            <button class="ghost" id="analysisPortfolioBtn" type="button">＋ Carteira</button>
            <button class="ghost" id="analysisCompareBtn" type="button">⇄ Comparar</button>
            <button class="primary" id="analysisPrintBtn" type="button">Imprimir / PDF</button>'''
new_actions = '''            <button class="ghost" id="analysisWatchBtn" type="button">☆ Watchlist</button>
            <button class="ghost" id="analysisPortfolioBtn" type="button">＋ Carteira</button>
            <button class="ghost" id="analysisDecisionBtn" type="button">✎ Decisão</button>
            <button class="ghost" id="analysisAlertBtn" type="button">◉ Alerta</button>
            <button class="ghost" id="analysisCompareBtn" type="button">⇄ Comparar</button>
            <button class="primary" id="analysisPrintBtn" type="button">Imprimir / PDF</button>'''
if old_actions not in html:
    raise SystemExit("ERRO: não encontrei os botões da análise.")
html = html.replace(old_actions, new_actions, 1)

journal_start = html.find('<section id="journal" class="page">')
alerts_start = html.find('<section id="alerts" class="page">', journal_start)
profile_start = html.find('<section id="profile" class="page">', alerts_start)
if min(journal_start, alerts_start, profile_start) < 0:
    raise SystemExit("ERRO: não encontrei as secções Journal/Alerts/Profile.")

journal_alerts_html = r'''<section id="journal" class="page">
      <div class="hero">
        <div><h1>Investment Journal</h1><p class="sub">Versões da tese, plano de entrada, monitorização e fotografia dos dados no momento da decisão.</p></div>
        <div class="decision-toolbar"><button class="ghost" id="journalExportBtn" type="button">Exportar CSV</button><button class="ghost" id="journalSaveCurrentBtn" type="button">Guardar análise atual</button><button class="primary" id="journalNewBtn" type="button">+ Nova decisão</button></div>
      </div>
      <div class="decision-kpi-grid">
        <div class="card"><div class="metric-label">Decisões guardadas</div><div class="metric" id="journalDecisionCount">0</div><div class="delta">Histórico local</div></div>
        <div class="card"><div class="metric-label">Ativos acompanhados</div><div class="metric" id="journalAssetCount">0</div><div class="delta">Teses distintas</div></div>
        <div class="card"><div class="metric-label">Revisões próximas</div><div class="metric" id="journalReviewCount">0</div><div class="delta">Vencidas ou em 14 dias</div></div>
        <div class="card"><div class="metric-label">Teses com versões</div><div class="metric" id="journalVersionedCount">0</div><div class="delta">Processo documentado</div></div>
      </div>
      <div class="decision-workspace">
        <div class="card">
          <div class="decision-filter-row"><input id="journalSearch" placeholder="Pesquisar ticker ou nota"><select id="journalActionFilter"><option value="">Todas as decisões</option><option value="OBSERVE">Observar</option><option value="INITIATE_SMALL">Iniciar pequena</option><option value="ACCUMULATE">Acumular</option><option value="HOLD">Manter</option><option value="REDUCE">Reduzir</option><option value="AVOID">Evitar</option><option value="SELL">Vender</option></select><select id="journalReviewFilter"><option value="">Todas as revisões</option><option value="due">Vencidas / próximas</option><option value="future">Futuras</option></select></div>
          <div class="decision-list" id="decisionJournalList"></div>
        </div>
        <div class="card" id="decisionJournalDetail"><div class="decision-detail-empty"><strong>Seleciona uma decisão.</strong><br>O detalhe mostrará plano, snapshot dos scores, versões e data de revisão.</div></div>
      </div>
    </section>

    <section id="alerts" class="page">
      <div class="hero">
        <div><h1>Monitorização & Alertas</h1><p class="sub">Regras de preço, score, RSI, pullback e revisão da tese, avaliadas com os dados atuais do ThesisOS.</p></div>
        <div class="monitoring-toolbar"><button class="ghost" id="alertsFromJournalBtn" type="button">Criar a partir da última decisão</button><button class="ghost" id="alertsRefreshBtn" type="button">Atualizar todos</button><button class="primary" id="alertsNewBtn" type="button">+ Novo alerta</button></div>
      </div>
      <div class="sync-strip" id="alertsSyncStrip"><div class="sync-left"><div class="sync-icon">◉</div><div><strong>Monitorização local e explicável</strong><small id="alertsSyncText">Os alertas são verificados quando abres ou atualizas esta página; não executam ordens.</small></div></div><span class="freshness warn" id="alertsUpdated">Ainda não verificados</span></div>
      <div class="monitoring-summary">
        <div class="card"><div class="metric-label">Alertas configurados</div><div class="metric" id="alertsTotalCount">0</div><div class="delta">Regras locais</div></div>
        <div class="card"><div class="metric-label">Ativados</div><div class="metric" id="alertsTriggeredCount">0</div><div class="delta bad">Exigem revisão</div></div>
        <div class="card"><div class="metric-label">Pendentes</div><div class="metric" id="alertsPendingCount">0</div><div class="delta warn">Condição ainda não atingida</div></div>
        <div class="card"><div class="metric-label">Com erro / sem dados</div><div class="metric" id="alertsErrorCount">0</div><div class="delta">Cobertura incompleta</div></div>
      </div>
      <div class="filter-row"><button class="filter active" data-alert-filter="all">Todos</button><button class="filter" data-alert-filter="triggered">Ativados</button><button class="filter" data-alert-filter="pending">Pendentes</button><button class="filter" data-alert-filter="error">Com erro</button></div>
      <div class="monitoring-grid" id="monitoringAlertsList"></div>
    </section>

    '''

html = html[:journal_start] + journal_alerts_html + html[profile_start:]

modal_anchor = '<div class="portfolio-modal-overlay" id="portfolioTransactionModal"'
if modal_anchor not in html:
    raise SystemExit("ERRO: não encontrei o modal da carteira.")

modals = r'''
<div class="decision-modal-overlay" id="decisionJournalModal" aria-hidden="true">
  <section class="decision-modal" role="dialog" aria-modal="true" aria-labelledby="decisionJournalModalTitle">
    <div class="decision-modal-head"><div><h2 id="decisionJournalModalTitle">Guardar decisão</h2><p class="sub">Cria uma versão auditável da tese e do plano de monitorização.</p></div><button class="decision-modal-close" id="decisionJournalClose" type="button" aria-label="Fechar">×</button></div>
    <form class="decision-modal-body" id="decisionJournalForm">
      <div class="decision-suggestion" id="decisionAutoSuggestion"><strong>Sugestão automática:</strong> pesquisa um ativo para preencher o contexto.</div>
      <div class="form-grid">
        <label>Ticker<input id="decisionSymbol" required placeholder="AAPL"></label>
        <label>Nome<input id="decisionName" placeholder="Apple Inc."></label>
        <label>Bolsa<input id="decisionExchange" placeholder="XETRA (opcional)"></label>
        <label>Decisão<select id="decisionAction"><option value="OBSERVE">Observar</option><option value="INITIATE_SMALL">Iniciar posição pequena</option><option value="ACCUMULATE">Acumular</option><option value="HOLD">Manter</option><option value="REDUCE">Reduzir</option><option value="AVOID">Evitar</option><option value="SELL">Vender</option></select></label>
        <label>Zona de entrada — mínimo<input id="decisionEntryMin" type="number" min="0" step="0.0001"></label>
        <label>Zona de entrada — máximo<input id="decisionEntryMax" type="number" min="0" step="0.0001"></label>
        <label>Peso inicial (%)<input id="decisionInitialWeight" type="number" min="0" max="100" step="0.1"></label>
        <label>Peso máximo (%)<input id="decisionMaxWeight" type="number" min="0" max="100" step="0.1"></label>
        <label>Próxima revisão<input id="decisionReviewDate" type="date"></label>
        <label>Horizonte<select id="decisionHorizon"><option value="long">Longo prazo</option><option value="medium">Médio prazo</option><option value="short">Curto prazo</option></select></label>
      </div>
      <label style="display:block;margin-top:12px">Racional<textarea class="decision-textarea" id="decisionRationale" placeholder="Porque faz sentido esta decisão com a informação disponível?"></textarea></label>
      <div class="form-grid" style="margin-top:12px"><label>Catalisadores / dados a acompanhar<textarea class="decision-textarea" id="decisionCatalysts"></textarea></label><label>Sinais de quebra da tese<textarea class="decision-textarea" id="decisionThesisBreaks"></textarea></label></div>
      <label class="decision-check"><input id="decisionCreateAlerts" type="checkbox" checked><span>Criar automaticamente um alerta para a data de revisão e, quando existir zona de entrada, um alerta de preço.</span></label>
      <div class="decision-modal-actions"><button class="ghost" id="decisionJournalCancel" type="button">Cancelar</button><button class="primary" type="submit">Guardar versão</button></div>
    </form>
  </section>
</div>

<div class="decision-modal-overlay" id="monitoringAlertModal" aria-hidden="true">
  <section class="decision-modal" role="dialog" aria-modal="true" aria-labelledby="monitoringAlertModalTitle">
    <div class="decision-modal-head"><div><h2 id="monitoringAlertModalTitle">Novo alerta</h2><p class="sub">A regra será verificada com dados reais quando atualizares a monitorização.</p></div><button class="decision-modal-close" id="monitoringAlertClose" type="button" aria-label="Fechar">×</button></div>
    <form class="decision-modal-body" id="monitoringAlertForm">
      <div class="form-grid">
        <label>Ticker<input id="alertSymbol" required placeholder="AAPL"></label>
        <label>Nome<input id="alertName" placeholder="Apple Inc."></label>
        <label>Bolsa<input id="alertExchange" placeholder="XETRA (opcional)"></label>
        <label>Tipo de alerta<select id="alertType"><option value="price_below">Preço abaixo de</option><option value="price_above">Preço acima de</option><option value="quality_below">Score de qualidade abaixo de</option><option value="technical_below">Score técnico abaixo de</option><option value="pullback_above">Pullback acima de</option><option value="rsi_above">RSI acima de</option><option value="rsi_below">RSI abaixo de</option><option value="review_due">Data de revisão atingida</option></select></label>
        <label id="alertThresholdWrap">Limite<input id="alertThreshold" type="number" step="0.01"></label>
        <label id="alertDateWrap" style="display:none">Data de revisão<input id="alertReviewDate" type="date"></label>
      </div>
      <label style="display:block;margin-top:12px">Nota<textarea class="decision-textarea" id="alertNote" placeholder="O que fazer quando o alerta for ativado?"></textarea></label>
      <div class="decision-modal-actions"><button class="ghost" id="monitoringAlertCancel" type="button">Cancelar</button><button class="primary" type="submit">Guardar alerta</button></div>
    </form>
  </section>
</div>

'''
html = html.replace(modal_anchor, modals + modal_anchor, 1)

old_handle = '''    if(id==="watchlist") renderWatchlist();
    if(id==="myportfolio") renderPortfolioManager();'''
new_handle = '''    if(id==="watchlist") renderWatchlist();
    if(id==="myportfolio") renderPortfolioManager();
    if(id==="journal") renderDecisionJournal();
    if(id==="alerts") renderMonitoringAlerts();'''
if old_handle not in html:
    raise SystemExit("ERRO: não encontrei handlePageOpen.")
html = html.replace(old_handle, new_handle, 1)

js = r'''
  // ---------------------------------------------------------------
  // Decision Journal and Monitoring Alerts
  // ---------------------------------------------------------------
  const DECISION_JOURNAL_KEY="thesisos_decision_journal_v1";
  const MONITORING_ALERTS_KEY="thesisos_monitoring_alerts_v1";
  let selectedDecisionId=null;
  let currentAlertFilter="all";

  const DECISION_ACTIONS={OBSERVE:"Observar",INITIATE_SMALL:"Iniciar posição pequena",ACCUMULATE:"Acumular",HOLD:"Manter",REDUCE:"Reduzir",AVOID:"Evitar",SELL:"Vender"};
  const ALERT_LABELS={price_below:"Preço abaixo de",price_above:"Preço acima de",quality_below:"Score de qualidade abaixo de",technical_below:"Score técnico abaixo de",pullback_above:"Pullback acima de",rsi_above:"RSI acima de",rsi_below:"RSI abaixo de",review_due:"Data de revisão"};

  function decisionRead(key,fallback=[]){try{const value=JSON.parse(localStorage.getItem(key));return value??fallback;}catch{return fallback;}}
  function decisionWrite(key,value){localStorage.setItem(key,JSON.stringify(value));}
  function decisionId(){return `${Date.now()}_${Math.random().toString(36).slice(2,8)}`;}
  function decisionDateOffset(days){const date=new Date();date.setDate(date.getDate()+days);return date.toISOString().slice(0,10);}
  function decisionEsc(value){return String(value??"").replace(/[&<>'"]/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));}
  function getDecisionJournal(){const items=decisionRead(DECISION_JOURNAL_KEY,[]);return Array.isArray(items)?items:[];}
  function saveDecisionJournal(items){decisionWrite(DECISION_JOURNAL_KEY,items);renderDecisionJournal();}
  function getMonitoringAlerts(){const items=decisionRead(MONITORING_ALERTS_KEY,[]);return Array.isArray(items)?items:[];}
  function saveMonitoringAlerts(items){decisionWrite(MONITORING_ALERTS_KEY,items);renderMonitoringAlerts();}
  function currentDecisionRecord(payload=window.currentAnalysisPayload){if(!payload)return null;const asset=payload.asset||{},market=payload.market||{};return {symbol:String(asset.symbol||payload.query||"").toUpperCase(),name:asset.name||asset.symbol||payload.query||"",exchange:asset.exchange_code||asset.exchange||"",ticker:asset.symbol||payload.query||"",asset_type:asset.asset_type||"stock",currency:market.currency||asset.currency||"",price:Number(market.price??asset.price),payload};}
  function currentPortfolioContext(record){try{const snapshot=portfolioSnapshot();const position=snapshot.positions.find(item=>String(item.symbol).toUpperCase()===record.symbol&&(!record.exchange||!item.exchange||String(item.exchange).toUpperCase()===String(record.exchange).toUpperCase()));return position?{weight:position.weight||0,target_weight:position.target_weight||0,quantity:position.quantity||0,value:position.market_value||0}:null;}catch{return null;}}
  function finiteDecisionNumber(value){const number=Number(value);return Number.isFinite(number)?number:null;}
  function decisionFrameworkItem(payload,id){return payload?.framework_engine?.framework_checklist?.items?.find(item=>item.id===id)||null;}
  function automaticDecisionSuggestion(payload){const record=currentDecisionRecord(payload);if(!record)return {action:"OBSERVE",reason:"Pesquisa um ativo para gerar contexto.",entry_min:null,entry_max:null,initial_weight:1,max_weight:5};const quality=finiteDecisionNumber(payload?.framework_engine?.quantitative_snapshot?.score),technical=finiteDecisionNumber(payload?.technical?.score),completeness=finiteDecisionNumber(payload?.data_quality?.completeness_percentage)||0,price=finiteDecisionNumber(record.price),sma50=finiteDecisionNumber(payload?.technical?.indicators?.sma_50),valuation=decisionFrameworkItem(payload,"valuation")||decisionFrameworkItem(payload,"aggregate_valuation"),valuationReady=valuation&&["complete","available"].includes(valuation.status),portfolio=currentPortfolioContext(record);let action="OBSERVE",reason="A análise ainda requer validação de valuation, riscos e contexto de carteira.";if(completeness<55||quality===null){action="OBSERVE";reason="Cobertura insuficiente para uma decisão forte.";}else if(portfolio&&quality<55){action="REDUCE";reason="A posição existe e o score de qualidade está abaixo do limiar genérico de 55.";}else if(portfolio){action="HOLD";reason=valuationReady?"A posição existente mantém qualidade suficiente; rever peso e valuation.":"A posição existente mantém qualidade, mas valuation e revisão qualitativa continuam necessários.";}else if(record.asset_type==="etf"&&quality>=82&&technical!==null&&technical>=60){action="INITIATE_SMALL";reason="Estrutura do ETF e tendência são fortes; usar entrada faseada e validar overlap da carteira.";}else if(quality>=82&&technical!==null&&technical>=65&&valuationReady){action="INITIATE_SMALL";reason="Qualidade, tendência e valuation têm cobertura suficiente para considerar uma posição inicial pequena.";}else if(quality<55){action="AVOID";reason="O score quantitativo atual não compensa os riscos identificados.";}else if(quality>=82){action="OBSERVE";reason="Qualidade forte, mas valuation ainda não está suficientemente avaliado.";}let reference=sma50||price,entryMin=null,entryMax=null;if(price&&reference){entryMin=Number((Math.min(price,reference)*0.97).toFixed(4));entryMax=Number((Math.min(price,reference)*1.01).toFixed(4));if(entryMin>entryMax)[entryMin,entryMax]=[entryMax,entryMin];}const initial=record.asset_type==="etf"?5:1,max=record.asset_type==="etf"?25:5;return {action,reason,entry_min:entryMin,entry_max:entryMax,initial_weight:portfolio?.weight||initial,max_weight:Math.max(portfolio?.target_weight||0,max)};}
  function decisionDefaultText(payload,type){const engine=payload?.framework_engine||{},snapshot=engine.quantitative_snapshot||{},technical=payload?.technical||{},positives=[...(snapshot.positive_signals||[]),...(technical.positive_signals||[])].map(item=>item.label).filter(Boolean),warnings=[...(snapshot.warning_signals||[]),...(technical.warning_signals||[])].map(item=>item.label).filter(Boolean);if(type==="rationale")return `Score de qualidade/estrutura: ${snapshot.score??"n/d"}. Score técnico: ${technical.score??"n/d"}. Conclusão atual: ${engine.decision?.label||"análise parcial"}.`;if(type==="catalysts")return [...new Set([...(engine.next_required_data||[]).slice(0,4),...positives.slice(0,2)])].join("\n");return [...new Set([...warnings.slice(0,4),"Deterioração material do cash flow, dívida, margens ou estrutura do ativo.","Alteração da tese que não seja explicada por volatilidade normal."])].join("\n");}
  function openDecisionModal(payload=window.currentAnalysisPayload,baseEntry=null){const modal=$("#decisionJournalModal"),record=currentDecisionRecord(payload),suggestion=automaticDecisionSuggestion(payload);$("#decisionJournalForm").reset();$("#decisionReviewDate").value=decisionDateOffset(90);$("#decisionHorizon").value="long";$("#decisionCreateAlerts").checked=true;if(record){$("#decisionSymbol").value=record.symbol;$("#decisionName").value=record.name;$("#decisionExchange").value=record.exchange||"";$("#decisionAction").value=suggestion.action;$("#decisionEntryMin").value=suggestion.entry_min??"";$("#decisionEntryMax").value=suggestion.entry_max??"";$("#decisionInitialWeight").value=suggestion.initial_weight;$("#decisionMaxWeight").value=suggestion.max_weight;$("#decisionRationale").value=decisionDefaultText(payload,"rationale");$("#decisionCatalysts").value=decisionDefaultText(payload,"catalysts");$("#decisionThesisBreaks").value=decisionDefaultText(payload,"breaks");}if(baseEntry){$("#decisionSymbol").value=baseEntry.symbol;$("#decisionName").value=baseEntry.name;$("#decisionExchange").value=baseEntry.exchange||"";$("#decisionAction").value=baseEntry.action;$("#decisionEntryMin").value=baseEntry.entry_min??"";$("#decisionEntryMax").value=baseEntry.entry_max??"";$("#decisionInitialWeight").value=baseEntry.initial_weight??"";$("#decisionMaxWeight").value=baseEntry.max_weight??"";$("#decisionReviewDate").value=decisionDateOffset(90);$("#decisionHorizon").value=baseEntry.horizon||"long";$("#decisionRationale").value=baseEntry.rationale||"";$("#decisionCatalysts").value=baseEntry.catalysts||"";$("#decisionThesisBreaks").value=baseEntry.thesis_breaks||"";}$("#decisionAutoSuggestion").innerHTML=`<strong>Sugestão automática: ${decisionEsc(DECISION_ACTIONS[suggestion.action])}</strong><br>${decisionEsc(suggestion.reason)} A decisão continua editável e não substitui valuation nem julgamento humano.`;modal.classList.add("open");modal.setAttribute("aria-hidden","false");}
  function closeDecisionModal(){const modal=$("#decisionJournalModal");modal.classList.remove("open");modal.setAttribute("aria-hidden","true");}
  function decisionSnapshot(payload,record){const technical=payload?.technical||{},engine=payload?.framework_engine||{},quality=engine.quantitative_snapshot||{},context=currentPortfolioContext(record);return {price:finiteDecisionNumber(record.price),currency:record.currency,quality_score:quality.score??null,quality_label:quality.classification?.label||null,technical_score:technical.score??null,technical_label:technical.classification?.label||null,data_completeness:payload?.data_quality?.completeness_percentage??null,framework_coverage:engine.framework_checklist?.coverage_percentage??null,pullback:technical.indicators?.pullback_from_52w_high_percentage??null,rsi:technical.indicators?.rsi_14??null,portfolio_weight:context?.weight??0,portfolio_target:context?.target_weight??0,decision_label:engine.decision?.label||null,positive_signals:(quality.positive_signals||[]).slice(0,4),warning_signals:(quality.warning_signals||[]).slice(0,4)};}
  function createDecisionAlerts(entry){const alerts=getMonitoringAlerts();if(entry.review_date&&!alerts.some(item=>item.symbol===entry.symbol&&item.type==="review_due"&&item.review_date===entry.review_date))alerts.push({id:decisionId(),symbol:entry.symbol,name:entry.name,exchange:entry.exchange||"",type:"review_due",threshold:null,review_date:entry.review_date,note:`Rever tese — versão ${entry.version}.`,status:"pending",created_at:new Date().toISOString(),last_checked:null,current_value:null});if(entry.entry_max&&!alerts.some(item=>item.symbol===entry.symbol&&item.type==="price_below"&&Number(item.threshold)===Number(entry.entry_max)))alerts.push({id:decisionId(),symbol:entry.symbol,name:entry.name,exchange:entry.exchange||"",type:"price_below",threshold:Number(entry.entry_max),review_date:null,note:`Entrou na zona técnica de interesse da versão ${entry.version}. Validar valuation e tese antes de agir.`,status:"pending",created_at:new Date().toISOString(),last_checked:null,current_value:null});decisionWrite(MONITORING_ALERTS_KEY,alerts);}
  function saveDecisionFromForm(){const symbol=$("#decisionSymbol").value.trim().toUpperCase();if(!symbol){showToast("Indica o ticker.");return;}const entries=getDecisionJournal(),versions=entries.filter(item=>item.symbol===symbol).map(item=>Number(item.version)||0),record=currentDecisionRecord(),payload=record&&record.symbol===symbol?window.currentAnalysisPayload:null;const entry={id:decisionId(),symbol,name:$("#decisionName").value.trim()||symbol,exchange:$("#decisionExchange").value.trim().toUpperCase(),action:$("#decisionAction").value,entry_min:finiteDecisionNumber($("#decisionEntryMin").value),entry_max:finiteDecisionNumber($("#decisionEntryMax").value),initial_weight:finiteDecisionNumber($("#decisionInitialWeight").value),max_weight:finiteDecisionNumber($("#decisionMaxWeight").value),review_date:$("#decisionReviewDate").value||null,horizon:$("#decisionHorizon").value,rationale:$("#decisionRationale").value.trim(),catalysts:$("#decisionCatalysts").value.trim(),thesis_breaks:$("#decisionThesisBreaks").value.trim(),version:Math.max(0,...versions)+1,created_at:new Date().toISOString(),snapshot:payload?decisionSnapshot(payload,record):null};entries.push(entry);decisionWrite(DECISION_JOURNAL_KEY,entries);if($("#decisionCreateAlerts").checked)createDecisionAlerts(entry);selectedDecisionId=entry.id;closeDecisionModal();renderDecisionJournal();renderMonitoringAlerts();showToast(`Decisão ${symbol} v${entry.version} guardada.`);}
  function decisionReviewIsNear(entry){if(!entry.review_date)return false;const today=new Date();today.setHours(0,0,0,0);const review=new Date(`${entry.review_date}T00:00:00`);return review.getTime()<=today.getTime()+14*86400000;}
  function renderDecisionJournal(){const entries=getDecisionJournal().sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at))),search=String($("#journalSearch")?.value||"").trim().toUpperCase(),action=$("#journalActionFilter")?.value||"",review=$("#journalReviewFilter")?.value||"";const assets=new Set(entries.map(item=>item.symbol)),counts={};entries.forEach(item=>counts[item.symbol]=(counts[item.symbol]||0)+1);$("#journalDecisionCount")&&($("#journalDecisionCount").textContent=entries.length);$("#journalAssetCount")&&($("#journalAssetCount").textContent=assets.size);$("#journalReviewCount")&&($("#journalReviewCount").textContent=entries.filter(decisionReviewIsNear).length);$("#journalVersionedCount")&&($("#journalVersionedCount").textContent=Object.values(counts).filter(value=>value>1).length);const filtered=entries.filter(item=>{if(search&&!`${item.symbol} ${item.name} ${item.rationale}`.toUpperCase().includes(search))return false;if(action&&item.action!==action)return false;if(review==="due"&&!decisionReviewIsNear(item))return false;if(review==="future"&&decisionReviewIsNear(item))return false;return true;});const list=$("#decisionJournalList");if(!list)return;if(!filtered.length){list.innerHTML='<div class="decision-detail-empty"><strong>Sem decisões guardadas.</strong><br>Guarda a análise atual ou cria uma entrada manual.</div>';$("#decisionJournalDetail").innerHTML='<div class="decision-detail-empty"><strong>Sem detalhe disponível.</strong></div>';return;}if(!selectedDecisionId||!entries.some(item=>item.id===selectedDecisionId))selectedDecisionId=filtered[0].id;list.innerHTML=filtered.map(item=>`<button class="decision-entry-button ${item.id===selectedDecisionId?"active":""}" data-decision-id="${decisionEsc(item.id)}"><div class="decision-entry-top"><div><strong>${decisionEsc(item.symbol)} · ${decisionEsc(DECISION_ACTIONS[item.action]||item.action)}</strong><small style="display:block;color:var(--muted);margin-top:3px">${decisionEsc(item.name)} · versão ${item.version}</small></div><span class="freshness ${decisionReviewIsNear(item)?"warn":""}">${decisionEsc(item.review_date||item.created_at.slice(0,10))}</span></div><p>${decisionEsc(item.rationale||"Sem racional registado.")}</p><div class="decision-entry-meta"><span class="chip">Q ${item.snapshot?.quality_score??"—"}</span><span class="chip">T ${item.snapshot?.technical_score??"—"}</span><span class="chip">Preço ${item.snapshot?.price??"—"}</span></div></button>`).join("");renderDecisionDetail(entries.find(item=>item.id===selectedDecisionId),entries);}
  function renderDecisionDetail(entry,entries=getDecisionJournal()){const container=$("#decisionJournalDetail");if(!container||!entry)return;const versions=entries.filter(item=>item.symbol===entry.symbol).sort((a,b)=>(b.version||0)-(a.version||0)),previous=versions.find(item=>item.version===entry.version-1),s=entry.snapshot||{};container.innerHTML=`<div class="decision-detail-head"><div><span class="tag">${decisionEsc(entry.symbol)} · v${entry.version}</span><h2 style="margin:8px 0 4px">${decisionEsc(DECISION_ACTIONS[entry.action]||entry.action)}</h2><p class="sub">Guardada em ${decisionEsc(entry.created_at.slice(0,10))}${entry.review_date?` · rever em ${decisionEsc(entry.review_date)}`:""}</p></div><div class="decision-detail-actions"><button class="ghost decision-new-version" data-id="${decisionEsc(entry.id)}">Nova versão</button><button class="ghost decision-analyze" data-symbol="${decisionEsc(entry.symbol)}" data-exchange="${decisionEsc(entry.exchange||"")}">Analisar</button><button class="danger-btn decision-delete" data-id="${decisionEsc(entry.id)}">Eliminar</button></div></div><div class="decision-plan-grid"><div class="decision-plan-card"><span>Zona de entrada</span><strong>${entry.entry_min??"—"} — ${entry.entry_max??"—"}</strong></div><div class="decision-plan-card"><span>Plano de posição</span><strong>${entry.initial_weight??"—"}% inicial · máximo ${entry.max_weight??"—"}%</strong></div><div class="decision-plan-card"><span>Horizonte / revisão</span><strong>${decisionEsc({long:"Longo prazo",medium:"Médio prazo",short:"Curto prazo"}[entry.horizon]||entry.horizon||"—")} · ${decisionEsc(entry.review_date||"sem data")}</strong></div></div><div class="decision-notes-grid"><div class="decision-note-box"><h3>Racional</h3><p>${decisionEsc(entry.rationale||"—")}</p></div><div class="decision-note-box"><h3>Catalisadores e dados</h3><p>${decisionEsc(entry.catalysts||"—")}</p></div><div class="decision-note-box"><h3>Quebra da tese</h3><p>${decisionEsc(entry.thesis_breaks||"—")}</p></div><div class="decision-note-box"><h3>Alteração face à versão anterior</h3><p>${previous?decisionEsc(`Decisão: ${DECISION_ACTIONS[previous.action]} → ${DECISION_ACTIONS[entry.action]}\nPreço: ${previous.snapshot?.price??"—"} → ${s.price??"—"}\nQualidade: ${previous.snapshot?.quality_score??"—"} → ${s.quality_score??"—"}\nTécnico: ${previous.snapshot?.technical_score??"—"} → ${s.technical_score??"—"}`):"Primeira versão desta tese."}</p></div></div><h2 style="margin-top:16px">Snapshot da decisão</h2><div class="decision-snapshot"><div class="mini"><span>Preço</span><strong>${s.currency||""} ${s.price??"—"}</strong></div><div class="mini"><span>Qualidade</span><strong>${s.quality_score??"—"}/100</strong></div><div class="mini"><span>Técnico</span><strong>${s.technical_score??"—"}/100</strong></div><div class="mini"><span>Peso carteira</span><strong>${finiteDecisionNumber(s.portfolio_weight)!==null?`${Number(s.portfolio_weight).toFixed(2)}%`:"—"}</strong></div></div><h2 style="margin-top:16px">Histórico da tese</h2><div class="decision-version-list">${versions.map(item=>`<div class="decision-version-row"><strong>v${item.version}</strong><span>${decisionEsc(DECISION_ACTIONS[item.action]||item.action)} · ${decisionEsc(item.created_at.slice(0,10))} · Q ${item.snapshot?.quality_score??"—"} / T ${item.snapshot?.technical_score??"—"}</span><button class="ghost decision-select-version" data-id="${decisionEsc(item.id)}">Abrir</button></div>`).join("")}</div>`;}
  function exportDecisionJournal(){const entries=getDecisionJournal();if(!entries.length){showToast("Não existem decisões para exportar.");return;}const headers=["symbol","name","exchange","version","created_at","action","entry_min","entry_max","initial_weight","max_weight","review_date","horizon","price","quality_score","technical_score","portfolio_weight","rationale","catalysts","thesis_breaks"],escapeCsv=value=>`"${String(value??"").replace(/"/g,'""')}"`,rows=entries.map(item=>headers.map(key=>{if(["price","quality_score","technical_score","portfolio_weight"].includes(key))return escapeCsv(item.snapshot?.[key]);return escapeCsv(item[key]);}).join(";")),blob=new Blob([[headers.join(";"),...rows].join("\n")],{type:"text/csv;charset=utf-8"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=`thesisos_decision_journal_${new Date().toISOString().slice(0,10)}.csv`;link.click();URL.revokeObjectURL(url);}

  function openMonitoringAlertModal(payload=window.currentAnalysisPayload,preset={}){const modal=$("#monitoringAlertModal"),record=currentDecisionRecord(payload);$("#monitoringAlertForm").reset();if(record){$("#alertSymbol").value=record.symbol;$("#alertName").value=record.name;$("#alertExchange").value=record.exchange||"";$("#alertThreshold").value=record.price?Number(record.price).toFixed(2):"";}Object.entries(preset).forEach(([key,value])=>{const map={symbol:"#alertSymbol",name:"#alertName",exchange:"#alertExchange",type:"#alertType",threshold:"#alertThreshold",review_date:"#alertReviewDate",note:"#alertNote"};if(map[key]&&$(map[key]))$(map[key]).value=value??"";});syncAlertModalType();modal.classList.add("open");modal.setAttribute("aria-hidden","false");}
  function closeMonitoringAlertModal(){const modal=$("#monitoringAlertModal");modal.classList.remove("open");modal.setAttribute("aria-hidden","true");}
  function syncAlertModalType(){const review=$("#alertType").value==="review_due";$("#alertThresholdWrap").style.display=review?"none":"block";$("#alertDateWrap").style.display=review?"block":"none";}
  function saveAlertFromForm(){const symbol=$("#alertSymbol").value.trim().toUpperCase(),type=$("#alertType").value;if(!symbol){showToast("Indica o ticker.");return;}const threshold=type==="review_due"?null:finiteDecisionNumber($("#alertThreshold").value),reviewDate=type==="review_due"?$("#alertReviewDate").value:null;if(type!=="review_due"&&threshold===null){showToast("Indica um limite válido.");return;}if(type==="review_due"&&!reviewDate){showToast("Indica a data de revisão.");return;}const alerts=getMonitoringAlerts();alerts.push({id:decisionId(),symbol,name:$("#alertName").value.trim()||symbol,exchange:$("#alertExchange").value.trim().toUpperCase(),type,threshold,review_date:reviewDate,note:$("#alertNote").value.trim(),status:"pending",created_at:new Date().toISOString(),last_checked:null,current_value:null,error:null});saveMonitoringAlerts(alerts);closeMonitoringAlertModal();showToast("Alerta guardado.");}
  function alertCurrentValue(alert,payload){const type=alert.type;if(type.startsWith("price_"))return finiteDecisionNumber(payload?.market?.price??payload?.asset?.price);if(type==="quality_below")return finiteDecisionNumber(payload?.framework_engine?.quantitative_snapshot?.score);if(type==="technical_below")return finiteDecisionNumber(payload?.technical?.score);if(type==="pullback_above")return finiteDecisionNumber(payload?.technical?.indicators?.pullback_from_52w_high_percentage);if(type.startsWith("rsi_"))return finiteDecisionNumber(payload?.technical?.indicators?.rsi_14);return null;}
  function evaluateAlert(alert,payload=null){const checked=new Date().toISOString();if(alert.type==="review_due"){const today=new Date();today.setHours(0,0,0,0);const due=alert.review_date?new Date(`${alert.review_date}T00:00:00`):null;return {...alert,status:due&&due<=today?"triggered":"pending",current_value:alert.review_date,last_checked:checked,error:null};}const current=alertCurrentValue(alert,payload);if(current===null)return {...alert,status:"error",current_value:null,last_checked:checked,error:"Indicador indisponível."};const threshold=Number(alert.threshold);let triggered=false;if(alert.type==="price_below")triggered=current<=threshold;if(alert.type==="price_above")triggered=current>=threshold;if(alert.type==="quality_below")triggered=current<threshold;if(alert.type==="technical_below")triggered=current<threshold;if(alert.type==="pullback_above")triggered=current>=threshold;if(alert.type==="rsi_above")triggered=current>=threshold;if(alert.type==="rsi_below")triggered=current<=threshold;return {...alert,status:triggered?"triggered":"pending",current_value:current,last_checked:checked,error:null};}
  async function refreshMonitoringAlerts(){const alerts=getMonitoringAlerts();if(!alerts.length){showToast("Não existem alertas.");return;}$("#alertsRefreshBtn").disabled=true;$("#alertsRefreshBtn").textContent="A atualizar…";const groups=new Map();alerts.filter(item=>item.type!=="review_due").forEach(item=>{const key=`${item.symbol}|${item.exchange||""}`;if(!groups.has(key))groups.set(key,{symbol:item.symbol,exchange:item.exchange||""});});const payloads=new Map();for(const [key,item] of groups){try{const params=new URLSearchParams();if(item.exchange)params.set("exchange",item.exchange);params.set("ticker",item.symbol);const response=await fetch(`/api/analysis/${encodeURIComponent(item.symbol)}?${params.toString()}`),data=await response.json();if(!response.ok)throw new Error(data.detail||data.error||"Análise indisponível");payloads.set(key,{data});}catch(error){payloads.set(key,{error:error.message});}}const updated=alerts.map(alert=>{if(alert.type==="review_due")return evaluateAlert(alert);const result=payloads.get(`${alert.symbol}|${alert.exchange||""}`);return result?.data?evaluateAlert(alert,result.data):{...alert,status:"error",last_checked:new Date().toISOString(),error:result?.error||"Dados indisponíveis."};});decisionWrite(MONITORING_ALERTS_KEY,updated);$("#alertsUpdated").textContent=`Atualizado ${new Date().toLocaleTimeString("pt-PT",{hour:"2-digit",minute:"2-digit"})}`;$("#alertsUpdated").className="freshness";$("#alertsRefreshBtn").disabled=false;$("#alertsRefreshBtn").textContent="Atualizar todos";renderMonitoringAlerts();showToast("Alertas atualizados.");}
  function alertValueText(alert){if(alert.type==="review_due")return alert.review_date||"—";const suffix=["quality_below","technical_below","rsi_above","rsi_below"].includes(alert.type)?"":"";return `${alert.current_value??"—"}${["pullback_above"].includes(alert.type)?"%":suffix}`;}
  function renderMonitoringAlerts(){const alerts=getMonitoringAlerts(),filtered=alerts.filter(item=>currentAlertFilter==="all"||item.status===currentAlertFilter);$("#alertsTotalCount")&&($("#alertsTotalCount").textContent=alerts.length);$("#alertsTriggeredCount")&&($("#alertsTriggeredCount").textContent=alerts.filter(item=>item.status==="triggered").length);$("#alertsPendingCount")&&($("#alertsPendingCount").textContent=alerts.filter(item=>item.status==="pending").length);$("#alertsErrorCount")&&($("#alertsErrorCount").textContent=alerts.filter(item=>item.status==="error").length);const list=$("#monitoringAlertsList");if(!list)return;if(!filtered.length){list.innerHTML='<div class="monitoring-empty"><strong>Sem alertas nesta vista.</strong><br>Cria uma regra manual ou guarda uma decisão com alertas automáticos.</div>';return;}const order={triggered:0,pending:1,error:2};filtered.sort((a,b)=>(order[a.status]??3)-(order[b.status]??3)||String(b.created_at).localeCompare(String(a.created_at)));list.innerHTML=filtered.map(item=>`<div class="monitoring-card ${decisionEsc(item.status||"pending")}"><div class="monitoring-card-head"><div><strong>${decisionEsc(item.symbol)} · ${decisionEsc(ALERT_LABELS[item.type]||item.type)}</strong><small style="display:block;color:var(--muted);margin-top:3px">${decisionEsc(item.name||item.symbol)}</small></div><span class="${item.status==="triggered"?"priority":item.status==="error"?"pill warn":"pill"}">${item.status==="triggered"?"Ativado":item.status==="error"?"Erro":"Pendente"}</span></div><p>${decisionEsc(item.note||"Sem instrução adicional.")}</p><div class="impact-list"><div class="impact-item"><span>Condição</span><strong>${item.type==="review_due"?decisionEsc(item.review_date):decisionEsc(`${ALERT_LABELS[item.type]} ${item.threshold}`)}</strong></div><div class="impact-item"><span>Valor atual</span><strong>${decisionEsc(alertValueText(item))}</strong></div></div>${item.error?`<div class="logic-note">${decisionEsc(item.error)}</div>`:""}<div class="monitoring-card-footer"><small>${item.last_checked?`Verificado ${decisionEsc(item.last_checked.slice(0,16).replace("T"," "))}`:"Ainda não verificado"}</small><div class="monitoring-actions"><button class="ghost monitoring-analyze" data-symbol="${decisionEsc(item.symbol)}" data-exchange="${decisionEsc(item.exchange||"")}">Analisar</button><button class="danger-btn monitoring-delete" data-id="${decisionEsc(item.id)}">Eliminar</button></div></div></div>`).join("");}

  $("#analysisDecisionBtn")?.addEventListener("click",()=>window.currentAnalysisPayload?openDecisionModal():showToast("Pesquisa primeiro um ativo."));
  $("#journalSaveCurrentBtn")?.addEventListener("click",()=>window.currentAnalysisPayload?openDecisionModal():showToast("Pesquisa primeiro um ativo."));
  $("#journalNewBtn")?.addEventListener("click",()=>openDecisionModal(null));
  $("#journalExportBtn")?.addEventListener("click",exportDecisionJournal);
  $("#decisionJournalClose")?.addEventListener("click",closeDecisionModal);$("#decisionJournalCancel")?.addEventListener("click",closeDecisionModal);$("#decisionJournalModal")?.addEventListener("click",event=>{if(event.target.id==="decisionJournalModal")closeDecisionModal();});
  $("#decisionJournalForm")?.addEventListener("submit",event=>{event.preventDefault();saveDecisionFromForm();});
  $("#journalSearch")?.addEventListener("input",renderDecisionJournal);$("#journalActionFilter")?.addEventListener("change",renderDecisionJournal);$("#journalReviewFilter")?.addEventListener("change",renderDecisionJournal);
  $("#analysisAlertBtn")?.addEventListener("click",()=>window.currentAnalysisPayload?openMonitoringAlertModal():showToast("Pesquisa primeiro um ativo."));
  $("#alertsNewBtn")?.addEventListener("click",()=>openMonitoringAlertModal());
  $("#alertsFromJournalBtn")?.addEventListener("click",()=>{const latest=getDecisionJournal().sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))[0];if(!latest){showToast("Ainda não existem decisões guardadas.");return;}openMonitoringAlertModal(null,{symbol:latest.symbol,name:latest.name,exchange:latest.exchange,type:latest.entry_max?"price_below":"review_due",threshold:latest.entry_max,review_date:latest.review_date,note:`Monitorizar a tese ${latest.symbol} v${latest.version}.`});});
  $("#alertsRefreshBtn")?.addEventListener("click",refreshMonitoringAlerts);$("#monitoringAlertClose")?.addEventListener("click",closeMonitoringAlertModal);$("#monitoringAlertCancel")?.addEventListener("click",closeMonitoringAlertModal);$("#monitoringAlertModal")?.addEventListener("click",event=>{if(event.target.id==="monitoringAlertModal")closeMonitoringAlertModal();});$("#alertType")?.addEventListener("change",syncAlertModalType);$("#monitoringAlertForm")?.addEventListener("submit",event=>{event.preventDefault();saveAlertFromForm();});
  $$("[data-alert-filter]").forEach(button=>button.addEventListener("click",()=>{$$("[data-alert-filter]").forEach(item=>item.classList.remove("active"));button.classList.add("active");currentAlertFilter=button.dataset.alertFilter;renderMonitoringAlerts();}));
  document.addEventListener("click",event=>{const decisionButton=event.target.closest("[data-decision-id]");if(decisionButton){selectedDecisionId=decisionButton.dataset.decisionId;renderDecisionJournal();return;}const selectVersion=event.target.closest(".decision-select-version");if(selectVersion){selectedDecisionId=selectVersion.dataset.id;renderDecisionJournal();return;}const newVersion=event.target.closest(".decision-new-version");if(newVersion){const entry=getDecisionJournal().find(item=>item.id===newVersion.dataset.id);if(entry)openDecisionModal(window.currentAnalysisPayload,entry);return;}const deleteDecision=event.target.closest(".decision-delete");if(deleteDecision){if(confirm("Eliminar esta versão da decisão?")){saveDecisionJournal(getDecisionJournal().filter(item=>item.id!==deleteDecision.dataset.id));selectedDecisionId=null;}return;}const analyzeDecision=event.target.closest(".decision-analyze");if(analyzeDecision){void loadUnifiedAnalysis(analyzeDecision.dataset.symbol,{exchange:analyzeDecision.dataset.exchange||undefined,ticker:analyzeDecision.dataset.symbol});return;}const deleteAlert=event.target.closest(".monitoring-delete");if(deleteAlert){if(confirm("Eliminar este alerta?"))saveMonitoringAlerts(getMonitoringAlerts().filter(item=>item.id!==deleteAlert.dataset.id));return;}const analyzeAlert=event.target.closest(".monitoring-analyze");if(analyzeAlert){void loadUnifiedAnalysis(analyzeAlert.dataset.symbol,{exchange:analyzeAlert.dataset.exchange||undefined,ticker:analyzeAlert.dataset.symbol});}});
  renderDecisionJournal();renderMonitoringAlerts();

'''

js_anchor = '''  // ---------------------------------------------------------------
  // Portfolio Builder
  // ---------------------------------------------------------------'''
if js_anchor not in html:
    raise SystemExit("ERRO: não encontrei o ponto de inserção JS.")
html = html.replace(js_anchor, js + js_anchor, 1)

index_path.write_text(html, encoding="utf-8")

if readme_path.exists():
    readme = readme_path.read_text(encoding="utf-8")
    addition = "- Functional Decision Journal with thesis versioning, snapshots, entry zones, review dates and CSV export.\n- Functional monitoring alerts for price, scores, RSI, pullback and thesis review dates."
    marker = "- Functional local Portfolio Manager"
    if "Functional Decision Journal" not in readme:
        pos = readme.find(marker)
        if pos >= 0:
            line_end = readme.find("\n", pos)
            readme = readme[:line_end+1] + addition + "\n" + readme[line_end+1:]
        else:
            readme += "\n" + addition + "\n"
    readme = readme.replace(
        "7. In an analysis, choose **Imprimir / PDF**.",
        "7. Save the current analysis in **Investment Journal**, create monitoring alerts and update them.\n8. In an analysis, choose **Imprimir / PDF**.",
    )
    readme = readme.replace(
        "- The watchlist and portfolio transactions are stored only in browser `localStorage`.",
        "- The watchlist, portfolio transactions, decision journal and alerts are stored only in browser `localStorage`.",
    )
    readme_path.write_text(readme, encoding="utf-8")

print("SUCESSO: Decision Journal e Monitorização & Alertas funcionais instalados.")
print("Foram atualizados index.html e README.md.")
