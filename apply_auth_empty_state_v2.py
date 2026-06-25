#!/usr/bin/env python3
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
source_index = root / "index_auth_empty_state_v2.html"
source_test = root / "test_supabase_auth_v2.py"
target_index = root / "index.html"
target_test = root / "test_supabase_auth.py"

for path in (source_index, source_test, target_index, target_test):
    if not path.exists():
        raise SystemExit(f"[FAIL] Ficheiro em falta: {path.name}")

shutil.copy2(target_index, root / "index_before_auth_empty_state_v2.html")
shutil.copy2(target_test, root / "test_supabase_auth_before_empty_state_v2.py")
shutil.copy2(source_index, target_index)
shutil.copy2(source_test, target_test)

compile(target_test.read_text(encoding="utf-8"), "test_supabase_auth.py", "exec")

print("[PASS] Backup criado")
print("[PASS] index.html substituído")
print("[PASS] test_supabase_auth.py substituído")
print()
print("Executa agora:")
print("  python3 test_supabase_auth.py")
