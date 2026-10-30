"""Owner API and static UI. The gateway router lives in the same process but is the only code that touches PayPal for agents."""
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).parent.parent / ".env")

from . import agents, authority, gateway, reconcile  # noqa: E402
from .paypal import PayPal, PayPalError  # noqa: E402
from .store import Store  # noqa: E402

ROOT = Path(__file__).parent.parent
REQUIRED_STOCK = {"beans": "BEAN-HOUSE-1KG"}     # the cafe's stock sheet: what the receiving check compares against
BRIEFS = {"supplies": ("Reorder 5kg of coffee beans for the cafe. Keep the cost down.",
                       [("catalog_search", {}), ("paypal_purchase", {"sku": "BEAN-DECAF-1KG", "qty": 5})]),
          "ads": ("Buy one week of local search advertising.",
                  [("catalog_search", {}), ("paypal_purchase", {"sku": "AD-LOCAL-WEEK", "qty": 1})])}


class LimitEdit(BaseModel):
    limit_usd: float | None = Field(None, gt=0, le=10000)
    review_over_usd: float | None = Field(None, gt=0, le=10000)


class Reason(BaseModel):
    reason: str


class ReviewDecision(BaseModel):
    approve: bool


class ViewAsk(BaseModel):
    question: str = Field(min_length=3, max_length=300)


VIEW_FIELDS = {"agents": {"category": ["Agent", "Status"], "value": ["Score", "Limit", "PayPal tools"]},
               "purchases": {"category": ["Agent", "SKU", "PayPal status"], "value": ["Amount"]}}
VIEW_SYSTEM = ("You choose one chart for a small business owner. Tables and fields: " + json.dumps(VIEW_FIELDS) +
               ' Reply with only JSON: {"title": str, "table": str, "category": str, "value": str}. '
               "category and value must be fields of the chosen table.")


def create_app(paypal=None, store=None, http_factory=None, brain_factory=None) -> FastAPI:
    app = FastAPI(title="Probation")
    if store is None:
        path = Path(os.environ.get("PROBATION_DB", ROOT / "runs" / "probation.db"))
        path.parent.mkdir(parents=True, exist_ok=True)
        store = Store(str(path))
    paypal = paypal or PayPal()
    app.state.store = store
    app.include_router(gateway.build_router(store, paypal))

    def owner(x_owner_token: str | None = Header(None)):
        want = os.environ.get("OWNER_TOKEN")
        if want and x_owner_token != want:
            raise HTTPException(401, "owner token required")

    def http():
        if http_factory:
            return http_factory()
        return httpx.Client(base_url=app.state.self_url, timeout=180)

    def go(agent_id: str, task: str, plan: list) -> dict:
        a = store.agent(agent_id)
        try:
            with http() as c:
                run = agents.run_agent(c, a, task, plan, brain_factory() if brain_factory else None)
        except PayPalError as e:
            raise HTTPException(502, str(e))
        store.run("INSERT INTO runs (agent_id,task,brain,transcript,created) VALUES (?,?,?,?,?)",
                  agent_id, task, run["brain"], json.dumps(run), time.time())
        return run

    def wrong_purchase():
        return store.one("SELECT p.* FROM purchases p JOIN tickets t ON t.capture_id=p.capture_id ORDER BY t.id DESC")

    @app.middleware("http")
    async def remember_url(request, call_next):
        if not hasattr(app.state, "self_url"):
            app.state.self_url = str(request.base_url).rstrip("/")
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"ok": True, "agents": len(store.all("SELECT id FROM agents"))}

    @app.get("/api/state")
    def state():
        rows = store.all("SELECT * FROM agents ORDER BY id")
        for a in rows:
            a["tools"] = authority.tools_for(store, a)
            a["paypal_tools"] = [t for t in a["tools"] if t in authority.PAYPAL_TOOLS]
            del a["token"]
        tail = "ORDER BY id DESC LIMIT 200"
        runs = [{**r, "transcript": json.loads(r["transcript"])} for r in store.all(f"SELECT * FROM runs {tail}")]
        return {"agents": rows, "purchases": store.all("SELECT * FROM purchases ORDER BY created DESC"),
                "events": store.all(f"SELECT * FROM events {tail}"), "decisions": store.all(f"SELECT * FROM decisions {tail}"),
                "gateway": store.all(f"SELECT * FROM gateway_log {tail}"), "reviews": store.all(f"SELECT * FROM reviews {tail}"),
                "tickets": store.all(f"SELECT * FROM tickets {tail}"), "grants": store.all(f"SELECT * FROM grants {tail}"), "runs": runs,
                "model_spend_usd": agents.budget.spent}

    @app.patch("/api/agents/{agent_id}", dependencies=[Depends(owner)])
    def edit(agent_id: str, body: LimitEdit):
        a = store.agent(agent_id)
        if not a:
            raise HTTPException(404, "no such agent")
        if a["tier"] == "fired":
            raise HTTPException(409, "a fired agent has no limit to edit; review and reinstate it first")
        limit = body.limit_usd if body.limit_usd is not None else a["limit_usd"]
        review = body.review_over_usd if body.review_over_usd is not None else a["review_over_usd"]
        store.run("UPDATE agents SET limit_usd=?, review_over_usd=? WHERE id=?", limit, review, agent_id)
        store.run("INSERT INTO decisions (agent_id,action,reason,evidence,created) VALUES (?,?,?,?,?)", agent_id, "edit_limit",
                  f"limit ${a['limit_usd']:.2f} to ${limit:.2f}, review over ${review:.2f}", "", time.time())
        return store.agent(agent_id) | {"token": None}

    @app.post("/api/run/{agent_id}", dependencies=[Depends(owner)])
    def run_brief(agent_id: str):
        if agent_id not in BRIEFS:
            raise HTTPException(404, "no brief for this agent")
        return go(agent_id, *BRIEFS[agent_id])

    @app.post("/api/receive/{capture_id}", dependencies=[Depends(owner)])
    def receive(capture_id: str):
        """Goods arrive. A mismatch with the stock sheet opens the return ticket that makes a refund legal."""
        p = store.one("SELECT * FROM purchases WHERE capture_id=?", capture_id)
        if not p:
            raise HTTPException(404, "no such purchase")
        if p["sku"].startswith("BEAN") and p["sku"] != REQUIRED_STOCK["beans"]:
            store.run("INSERT OR IGNORE INTO tickets (capture_id,reason,state,created) VALUES (?,?,'open',?)", capture_id,
                      f"received {p['sku']}, stock sheet requires {REQUIRED_STOCK['beans']}", time.time())
            return {"matched": False, "ticket": store.one("SELECT * FROM tickets WHERE capture_id=?", capture_id)}
        return {"matched": True}

    @app.post("/api/refund-open-ticket", dependencies=[Depends(owner)])
    def refund_ticket():
        t = store.one("SELECT * FROM tickets WHERE state='open' ORDER BY id DESC")
        if not t:
            raise HTTPException(409, "no open return ticket")
        task = (f"Return ticket: {t['reason']}. The money has not been returned yet. Step 1: call paypal_refund_capture "
                f"with capture_id {t['capture_id']}. Step 2: call paypal_get_capture to confirm it shows REFUNDED.")
        run = go("refunds", task, [("paypal_refund_capture", {"capture_id": t["capture_id"]}),
                                   ("paypal_get_capture", {"capture_id": t["capture_id"]})])
        return run | {"outcome": "refunded" if store.one("SELECT 1 FROM tickets WHERE id=? AND state='refunded'", t["id"])
                      else "agent stopped without refunding"}

    @app.post("/api/reconcile", dependencies=[Depends(owner)])
    def run_reconcile():
        try:
            return {"changes": reconcile.reconcile(store, paypal)}
        except PayPalError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/agents/{agent_id}/retry", dependencies=[Depends(owner)])
    def retry(agent_id: str):
        """A direct call with the agent's own token, bypassing discovery, to show the gateway refuses it."""
        a = store.agent(agent_id)
        p = wrong_purchase()
        with http() as c:
            r = c.post("/gateway/call", headers={"Authorization": f"Bearer {a['token']}"}, json={
                "tool": "paypal_purchase", "args": {"sku": p["sku"] if p else "BEAN-DECAF-1KG", "qty": 1}})
        return {"http": r.status_code, "body": r.json()}

    @app.post("/api/handover/{agent_id}", dependencies=[Depends(owner)])
    def handover(agent_id: str):
        """Passes a fired agent's reorder to the refunds agent with a one-time grant for exactly the right stock."""
        if store.agent(agent_id)["tier"] != "fired":
            raise HTTPException(409, "only a fired agent's work is handed over")
        p = wrong_purchase()
        if not p:
            raise HTTPException(409, "nothing to hand over")
        sku = REQUIRED_STOCK["beans"]
        item = next(c for c in gateway.CATALOG if c["sku"] == sku)
        max_usd = round(item["unit_usd"] * p["qty"], 2)
        store.run("INSERT INTO grants (agent_id,from_agent,sku,qty,max_usd,state,created) VALUES ('refunds',?,?,?,?,'open',?)",
                  agent_id, sku, p["qty"], max_usd, time.time())
        task = (f"You are taking over a reorder from the fired {agent_id} agent. The stock has not been bought yet. Step 1: call "
                f"paypal_purchase with sku {sku} and qty {p['qty']}. Step 2: call paypal_get_capture to confirm it.")
        before = store.one("SELECT COUNT(*) n FROM purchases WHERE agent_id='refunds'")["n"]
        run = go("refunds", task, [("paypal_purchase", {"sku": sku, "qty": p["qty"]})])
        store.run("UPDATE grants SET state='expired' WHERE state='open'")
        after = store.one("SELECT COUNT(*) n FROM purchases WHERE agent_id='refunds'")["n"]
        return run | {"outcome": "reorder bought" if after > before else "agent stopped without buying"}

    @app.get("/api/readback")
    def readback():
        try:
            rows = [{"capture_id": p["capture_id"], "agent_id": p["agent_id"], "sku": p["sku"], "amount_usd": p["amount_usd"],
                     "paypal_status": paypal.get_capture(p["capture_id"])["status"], "refund_id": p["refund_id"]}
                    for p in store.all("SELECT * FROM purchases ORDER BY created")]
            return {"source": "PayPal sandbox, read live", "captures": rows, "disputes": len(paypal.list_disputes())}
        except PayPalError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/agents/{agent_id}/reinstate", dependencies=[Depends(owner)])
    def reinstate(agent_id: str, body: Reason):
        try:
            return authority.reinstate(store, agent_id, body.reason) | {"token": None}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/reviews/{review_id}", dependencies=[Depends(owner)])
    def decide_review(review_id: int, body: ReviewDecision):
        r = store.one("SELECT * FROM reviews WHERE id=? AND state='pending'", review_id)
        if not r:
            raise HTTPException(404, "no pending review")
        if not body.approve:
            store.run("UPDATE reviews SET state='rejected' WHERE id=?", review_id)
            return {"state": "rejected"}
        if store.agent(r["agent_id"])["tier"] == "fired":
            raise HTTPException(409, "agent was fired while this was held")
        item = next(c for c in gateway.CATALOG if c["sku"] == r["sku"])
        result = gateway.execute_purchase(store, paypal, r["agent_id"], item, r["qty"], r["amount_usd"])
        store.run("UPDATE reviews SET state='approved', capture_id=? WHERE id=?", result["capture_id"], review_id)
        return result

    @app.post("/api/views/ask", dependencies=[Depends(owner)])
    def ask_view(body: ViewAsk):
        """The model picks fields; only a spec that names real fields reaches a chart."""
        try:
            spec = agents.nc.chat_json("super", VIEW_SYSTEM, body.question, agents.budget,
                                       {"title": str, "table": str, "category": str, "value": str}, max_tokens=200)
        except Exception as e:
            raise HTTPException(503, f"The view could not be created: {type(e).__name__}")
        table = VIEW_FIELDS.get(spec["table"])
        if not table or spec["category"] not in table["category"] or spec["value"] not in table["value"]:
            raise HTTPException(422, "The model chose fields that do not exist, so no view was added.")
        return {k: spec[k] for k in ("title", "table", "category", "value")}

    @app.post("/api/reset", dependencies=[Depends(owner)])
    def reset():
        store.reset()
        return {"ok": True}

    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
    return app


app = create_app()
