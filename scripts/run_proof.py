"""Drives the running product through the browser against the real PayPal sandbox and saves what happened.

Run with the server already up:  python scripts/run_proof.py http://127.0.0.1:8123
"""
import json
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
OUT = Path("proof_results")
OUT.mkdir(exist_ok=True)
api = httpx.Client(base_url=BASE, timeout=240)
log, console_errors = [], []


def stamp(name, data):
    log.append({"step": name, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "data": data})


def state():
    return api.get("/api/state").json()


def agent(name):
    return next(a for a in state()["agents"] if a["id"] == name)


def expect(step, ok):
    """Stops on a missing real outcome. A failed run is saved as failed and never replaces the good proof."""
    if ok:
        return
    name = f"failed_run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    (OUT / name).write_text(json.dumps({"failed_at": step, "steps": log, "state": state()}, indent=1))
    print("FAILED at", step, "saved", name)
    sys.exit(1)


def bought(name):
    return [x for x in state()["purchases"] if x["agent_id"] == name]


def click_step(page, shot):
    page.get_by_role("button", name="Run this step").click()
    page.wait_for_function("!document.querySelector('#banner').textContent.startsWith('Working')", timeout=200000)
    page.wait_for_timeout(500)
    text = page.locator("#banner").inner_text()
    page.screenshot(path=str(OUT / f"{shot}.png"), full_page=True)
    return text


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None)
    page.on("dialog", lambda d: d.accept("The brief was stale, so this is on the brief. Back on demoted terms." if d.type == "prompt" else ""))
    api.post("/api/reset")
    page.goto(BASE)
    page.wait_for_selector("#roster .ag-row")
    stamp("start", {"agents": state()["agents"]})
    page.screenshot(path=str(OUT / "01_start.png"), full_page=True)

    stamp("supplies_buys", {"banner": click_step(page, "02_supplies_bought")})
    expect("supplies_buys", bought("supplies"))
    stamp("goods_received", {"banner": click_step(page, "03_receiving_check")})
    expect("goods_received", state()["tickets"])
    stamp("refund", {"banner": click_step(page, "04_refund")})
    expect("refund", state()["tickets"][0]["state"] == "refunded")
    stamp("reconcile", {"banner": click_step(page, "05_fired"), "supplies": agent("supplies")})
    expect("reconcile", agent("supplies")["tier"] == "fired")
    stamp("direct_retry", {"banner": click_step(page, "06_retry_denied")})
    expect("direct_retry", any(g["agent_id"] == "supplies" and "tier fired" in g["detail"] for g in state()["gateway"]))
    stamp("ads_over_limit", {"banner": click_step(page, "07_ads_denied"), "ads": agent("ads")})
    expect("ads_over_limit", any(g["agent_id"] == "ads" and g["outcome"] == "denied" for g in state()["gateway"]))

    cell = page.locator('#roster .ag-row[row-id="ads"] [col-id="limit_usd"]')
    cell.click()
    page.locator("#roster input").first.fill("75")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('#banner').textContent.includes('75.00')")
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "08_limit_edited.png"), full_page=True)
    stamp("limit_edited_in_grid", {"ads": agent("ads")})

    stamp("ads_clears", {"banner": click_step(page, "09_ads_cleared"), "ads": agent("ads")})
    expect("ads_clears", bought("ads"))
    stamp("handover", {"banner": click_step(page, "10_handover")})
    expect("handover", bought("refunds"))
    stamp("readback", {"banner": click_step(page, "11_readback")})
    readback = api.get("/api/readback").json()
    stamp("readback_detail", readback)
    expect("readback", sorted(c["paypal_status"] for c in readback["captures"]) == ["COMPLETED", "COMPLETED", "REFUNDED"])
    stamp("reinstate", {"banner": click_step(page, "12_reinstated"), "supplies": agent("supplies")})

    page.get_by_role("tab", name="Risk and spend").click()
    page.wait_for_timeout(6000)
    page.screenshot(path=str(OUT / "13_studio_views.png"), full_page=True)
    studio_text = page.locator("#studio").inner_text()
    page.locator("#ask-text").fill("How much money did each agent spend?")
    page.locator("#ask-view button").click()
    page.wait_for_function("!document.querySelector('#banner').textContent.startsWith('Working')", timeout=120000)
    page.wait_for_timeout(3000)
    ai_view = {"banner": page.locator("#banner").inner_text(), "is_error": page.locator("#banner.error").count() > 0}
    stamp("ai_view", ai_view)
    page.screenshot(path=str(OUT / "13b_ai_view.png"), full_page=True)
    page.get_by_role("tab", name="Activity").click()
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "14_gateway_log.png"), full_page=True)
    b.close()

final = state()
brains = [{"agent": r["agent_id"], "brain": r["brain"], "model_error": r["transcript"]["model_error"]} for r in final["runs"]]
summary = {
    "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "environment": "PayPal sandbox, driven through the real UI in headless Chromium",
    "agent_runs": brains,
    "model_spend_usd_since_server_start": final["model_spend_usd"],
    "agent_runs_by_model": sum(1 for x in brains if x["brain"] == "model"),
    "agent_runs_by_scripted_fallback": sum(1 for x in brains if x["brain"] != "model"),
    "paypal_end_state": readback,
    "denied_gateway_calls": [g for g in final["gateway"] if g["outcome"] == "denied"],
    "final_agents": final["agents"],
    "events": final["events"],
    "decisions": final["decisions"],
    "wrong_spend_refunded_usd": sum(c["amount_usd"] for c in readback["captures"] if c["paypal_status"] == "REFUNDED"),
    "studio_text_found": studio_text[:600],
    "ai_view": ai_view,
    "browser_console_errors": console_errors,
}
(OUT / "demo_run.json").write_text(json.dumps(summary, indent=1))
(OUT / "demo_steps.json").write_text(json.dumps(log, indent=1))
print(json.dumps({k: summary[k] for k in ("agent_runs_by_model", "agent_runs_by_scripted_fallback", "wrong_spend_refunded_usd", "browser_console_errors")}, indent=1))
print([(c["agent_id"], c["sku"], c["paypal_status"]) for c in readback["captures"]])
print("final", [(a["id"], a["tier"], a["score"], a["limit_usd"]) for a in final["agents"]])
print("ai view:", ai_view)
