"""Runs the supplies brief repeatedly through the running product and saves what the model actually chose.

Each trial resets the app and makes one real sandbox payment if the model buys.
Run with the server up:  python scripts/run_trials.py http://127.0.0.1:8123 5
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
api = httpx.Client(base_url=BASE, timeout=240)
trials = []
for i in range(N):
    api.post("/api/reset")
    r = api.post("/api/run/supplies")
    run = r.json() if r.status_code == 200 else {"brain": f"http-{r.status_code}", "steps": [], "model_error": r.text[:200], "final": ""}
    buys = [s for s in run["steps"] if s["tool"] == "paypal_purchase"]
    trials.append({"trial": i + 1, "brain": run["brain"], "model_error": run.get("model_error", ""),
                   "tools_called": [s["tool"] for s in run["steps"]],
                   "bought": [{"args": s["args"], "http": s["http"], "capture_id": s["result"].get("capture_id")} for s in buys],
                   "final": run.get("final", "")[:200]})
    print(json.dumps(trials[-1])[:300])
counts = Counter((t["bought"][-1]["args"].get("sku") if t["bought"] else "no purchase") for t in trials)
out = {"run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "trials_run": N,
       "runs_by_model": sum(t["brain"] == "model" for t in trials), "last_purchase_by_sku": counts, "trials": trials}
Path("proof_results").mkdir(exist_ok=True)
Path("proof_results/model_trials.json").write_text(json.dumps(out, indent=1))
print(dict(counts))
