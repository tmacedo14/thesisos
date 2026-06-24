from pathlib import Path

html = Path("index.html").read_text(encoding="utf-8")
required = [
    'id="investorPolicyScore"',
    'id="policyPositionSizingCard"',
    'const INVESTOR_POLICY_KEY=',
    'function renderInvestorPolicy()',
    'function renderPolicySizing(payload)',
    'function syncPortfolioBuilderFromPolicy()',
]
missing = [marker for marker in required if marker not in html]
if missing:
    raise SystemExit("Investor Policy Engine: FAIL — " + ", ".join(missing))
if '<h1>Perfil de investidor</h1>' in html:
    raise SystemExit("Investor Policy Engine: FAIL — static profile remains")
print("Investor Policy Engine: PASS")
