import json
from urllib.request import urlopen

BASE = "http://127.0.0.1:3000"

with urlopen(f"{BASE}/api/analysis/AAPL", timeout=120) as response:
    analysis = json.load(response)

evidence = analysis.get("evidence") or {}
assert analysis.get("framework_engine", {}).get("version") == "0.9"
assert evidence.get("status") in {"ok", "partial", "unavailable"}
assert "events" in evidence
assert "source_hierarchy" in evidence

with urlopen(f"{BASE}/api/evidence/AAPL", timeout=120) as response:
    direct = json.load(response)

assert direct.get("symbol") == "AAPL"
assert "official_filings_count" in direct
assert "news_count" in direct

print("Evidence & Events Engine: PASS")
print("Status:", direct.get("status"))
print("Official filings:", direct.get("official_filings_count"))
print("News:", direct.get("news_count"))
print("Material events:", direct.get("material_events_count"))
