"""Offline: a fake PayPal and scripted brains, so these cost nothing. The fake lives here, not in the product."""
import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from probation import authority
from probation.agents import ScriptedBrain
from probation.main import create_app
from probation.store import Store


class FakePayPal:
    def __init__(self):
        self.captures, self.n, self.disputes, self.down = {}, 0, [], False

    def purchase(self, amount, description, custom_id):
        self.n += 1
        cid = f"CAP{self.n}"
        self.captures[cid] = {"id": cid, "status": "COMPLETED", "custom_id": custom_id}
        return {"order_id": f"ORD{self.n}", "capture_id": cid, "status": "COMPLETED"}

    def refund(self, cid):
        self.captures[cid]["status"] = "REFUNDED"
        return {"refund_id": f"REF-{cid}", "status": "COMPLETED"}

    def get_capture(self, cid):
        return self.captures[cid]

    def list_disputes(self):
        return self.disputes


class ExplodingBrain:
    label = "model"

    def step(self, messages, tools):
        raise RuntimeError("proxy 404")


@pytest.fixture
def env():
    pp, holder = FakePayPal(), {}
    app = create_app(paypal=pp, store=Store(), http_factory=lambda: _Quiet(holder["c"]), brain_factory=ExplodingBrain)
    holder["c"] = TestClient(app)
    return holder["c"], pp, app.state.store


class _Quiet:
    """Lets the same TestClient serve nested calls without closing it."""

    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        return False


def agent(c, name):
    return next(a for a in c.get("/api/state").json()["agents"] if a["id"] == name)


def fire_supplies(c):
    run = c.post("/api/run/supplies").json()
    cap = run["steps"][-1]["result"]["capture_id"]
    assert c.post(f"/api/receive/{cap}").json()["matched"] is False
    c.post("/api/refund-open-ticket")
    c.post("/api/reconcile")
    return cap


def test_full_demo_moment(env):
    c, pp, store = env
    cap = fire_supplies(c)
    s = agent(c, "supplies")
    assert s["tier"] == "fired" and s["last_txn"] == cap and s["paypal_tools"] == []
    retry = c.post("/api/agents/supplies/retry")
    assert retry.status_code == 200 and retry.json()["http"] == 403
    assert c.post("/api/run/ads").json()["steps"][-1]["http"] == 403
    assert c.patch("/api/agents/ads", json={"limit_usd": 75}).status_code == 200
    assert c.post("/api/run/ads").json()["steps"][-1]["result"]["status"] == "COMPLETED"
    run = c.post("/api/handover/supplies").json()
    assert run["steps"][-1]["result"]["sku"] == "BEAN-HOUSE-1KG"
    assert "paypal_purchase" not in agent(c, "refunds")["tools"]
    back = c.get("/api/readback").json()
    assert sorted(r["paypal_status"] for r in back["captures"]) == ["COMPLETED", "COMPLETED", "REFUNDED"]
    r = c.post("/api/agents/supplies/reinstate", json={"reason": "stale brief, not the agent"})
    assert r.status_code == 200
    d = [x for x in c.get("/api/state").json()["decisions"] if x["action"] == "reinstate"][0]
    assert cap in d["evidence"] and agent(c, "supplies")["tier"] == "demoted"


def test_scripted_fallback_is_labelled(env):
    c, _, store = env
    assert c.post("/api/run/supplies").json()["brain"] == "scripted-fallback"
    assert store.one("SELECT brain FROM runs")["brain"] == "scripted-fallback"


def test_refund_needs_open_ticket(env):
    c, _, store = env
    cap = c.post("/api/run/supplies").json()["steps"][-1]["result"]["capture_id"]
    token = store.agent("refunds")["token"]
    r = c.post("/gateway/call", headers={"Authorization": f"Bearer {token}"},
               json={"tool": "paypal_refund_capture", "args": {"capture_id": cap}})
    assert r.status_code == 403


def test_grant_only_buys_the_handed_over_item(env):
    c, _, store = env
    fire_supplies(c)
    store.run("INSERT INTO grants (agent_id,from_agent,sku,qty,max_usd,state,created) VALUES ('refunds','supplies','BEAN-HOUSE-1KG',5,120,'open',0)")
    r = c.post("/gateway/call", headers={"Authorization": f"Bearer {store.agent('refunds')['token']}"},
               json={"tool": "paypal_purchase", "args": {"sku": "CUP-12OZ-100", "qty": 1}})
    assert r.status_code == 403


def test_unknown_token_is_refused(env):
    assert env[0].get("/gateway/tools", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_fired_agent_limit_cannot_be_edited(env):
    c, *_ = env
    fire_supplies(c)
    assert c.patch("/api/agents/supplies", json={"limit_usd": 500}).status_code == 409


def test_events_count_once(env):
    c, *_ = env
    cap = fire_supplies(c)
    before = agent(c, "supplies")["score"]
    c.post("/api/reconcile")
    assert agent(c, "supplies")["score"] == before and cap


def test_score_change_needs_a_transaction():
    with pytest.raises(ValueError):
        authority.apply_event(Store(), "ads", "clean_settlement", "", "x", "y")


def test_promotion_after_clean_settlements(env):
    c, _, store = env
    for i in range(authority.CLEAN_TO_PROMOTE):
        store.run("INSERT INTO purchases (capture_id,order_id,agent_id,sku,qty,amount_usd,task_id,paypal_status,created) VALUES (?,?,?,?,?,?,?,?,?)",
                  f"CAP{i}", "o", "ads", "AD-LOCAL-WEEK", 1, 10, "", "COMPLETED", i)
        authority.apply_event(store, "ads", "clean_settlement", f"CAP{i}", "ok", "test")
    a = agent(c, "ads")
    assert a["tier"] == "trusted" and a["limit_usd"] == 60.0


def test_review_routing_holds_large_purchase(env):
    c, pp, store = env
    store.run("UPDATE agents SET review_over_usd=10 WHERE id='ads'")
    store.run("UPDATE agents SET limit_usd=100 WHERE id='ads'")
    r = c.post("/api/run/ads").json()
    assert r["steps"][-1]["result"]["status"] == "HELD_FOR_OWNER_REVIEW" and pp.n == 0
    assert c.post(f"/api/reviews/{r['steps'][-1]['result']['review_id']}", json={"approve": True}).json()["status"] == "COMPLETED"


def test_agent_runner_cannot_reach_paypal():
    tree = ast.parse(Path("probation/agents.py").read_text())
    imported = {getattr(n, "module", "") or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("paypal" in m for m in imported)


def test_state_never_exposes_tokens(env):
    assert "token" not in env[0].get("/api/state").json()["agents"][0]


def test_scripted_brain_finishes():
    assert ScriptedBrain([]).step([], []) == {"done": "plan finished"}
