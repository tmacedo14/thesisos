from pathlib import Path

html = Path("index.html").read_text(encoding="utf-8")
required = [
    'id="portfolioConstructionEngine"',
    'id="constructionAllocationBody"',
    'id="constructionPlanList"',
    'id="runStressTestBtn"',
    'id="constructionSimulateBtn"',
    'PORTFOLIO_CONSTRUCTION_KEY',
    'function buildPortfolioConstruction()',
    'function runConstructionStressTest()',
]
missing = [item for item in required if item not in html]
for forbidden in [
    'Concentração tecnológica</strong><p>Incluindo os ETFs, a exposição efetiva é 31%',
    'PWR · AI Infrastructure',
    '62% → 64%',
]:
    if forbidden in html:
        missing.append(f"static demo remains: {forbidden}")
if missing:
    raise SystemExit("Portfolio Construction Engine: FAIL\n" + "\n".join(missing))
print("Portfolio Construction Engine: PASS")
