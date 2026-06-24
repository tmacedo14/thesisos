from pathlib import Path

html = Path("index.html").read_text(encoding="utf-8")
required = [
    'id="liveDashboardUpdated"',
    'id="dashboardPortfolioValue"',
    'id="dashboardRadarCandidates"',
    'id="dashboardAttentionList"',
    'id="dashboardEvidenceList"',
    'function renderLiveDashboard()',
    'async function dashboardCheckHealth()',
]
missing = [item for item in required if item not in html]
forbidden = ["€18.450", "4 oportunidades a aproximar-se", "Supabase + motor próprio"]
present_forbidden = [item for item in forbidden if item in html]
if missing or present_forbidden:
    raise SystemExit({"missing": missing, "demonstrative_content": present_forbidden})
print("Live Dashboard: PASS")
