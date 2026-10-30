"""Repeats the full proof and keeps every outcome, pass or fail, so the pass rate is measured rather than assumed.

Run with the server up:  python scripts/repeat_proof.py http://127.0.0.1:8123 3
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = Path("proof_results")
results = []
for i in range(1, N + 1):
    r = subprocess.run([sys.executable, "-X", "utf8", "scripts/run_proof.py", BASE], capture_output=True, text=True, encoding="utf-8")
    passed = r.returncode == 0
    if passed:
        shutil.copy(OUT / "demo_run.json", OUT / f"repeat_{i}_pass.json")
    results.append({"attempt": i, "passed": passed, "tail": r.stdout.strip().splitlines()[-1:] or r.stderr[-300:]})
    print(results[-1])
(OUT / "repeat_summary.json").write_text(json.dumps({
    "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "attempts": N,
    "passed": sum(x["passed"] for x in results), "results": results}, indent=1))
