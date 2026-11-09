"""The credential-holding gateway. Agents authenticate with a token and can only reach PayPal through here."""
import json
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import authority
from .paypal import PayPalError

CATALOG = [{"sku": "BEAN-HOUSE-1KG", "name": "House blend coffee beans 1kg", "unit_usd": 24.0},
           {"sku": "BEAN-DECAF-1KG", "name": "Decaf coffee beans 1kg (clearance)", "unit_usd": 18.0},
           {"sku": "CUP-12OZ-100", "name": "12oz paper cups, pack of 100", "unit_usd": 15.0},
           {"sku": "AD-LOCAL-DAY", "name": "Local search ad placement, one day", "unit_usd": 15.0},
           {"sku": "AD-LOCAL-WEEK", "name": "Local search ad placement, one week", "unit_usd": 60.0}]
TOOL_SCHEMAS = {
    "catalog_search": ("List the supplier catalog with SKUs and unit prices.", {}, []),
    "paypal_purchase": ("Pay for a SKU through PayPal.", {"sku": {"type": "string"}, "qty": {"type": "integer"}}, ["sku", "qty"]),
    "paypal_get_capture": ("Read a payment's status from PayPal.", {"capture_id": {"type": "string"}}, ["capture_id"]),
    "paypal_refund_capture": ("Refund a payment that has an open return ticket.", {"capture_id": {"type": "string"}}, ["capture_id"]),
    "paypal_list_disputes": ("List PayPal disputes.", {}, []),
}


class Call(BaseModel):
    tool: str
    args: dict = {}


def schema(name: str) -> dict:
    desc, props, req = TOOL_SCHEMAS[name]
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}


def build_router(store, paypal) -> APIRouter:
    r = APIRouter(prefix="/gateway")

    def auth(authorization: str | None):
        a = store.agent_by_token((authorization or "").removeprefix("Bearer ").strip())
        if not a:
            raise HTTPException(401, "unknown agent token")
        return a

    def deny(a, tool, status, reason, detail=None):
        log_id = store.log_gateway(a["id"], tool, "denied", status, {"reason": reason, **(detail or {})})
        return log_id, HTTPException(status, {"denied": reason, "gateway_log_id": log_id, "tool": tool})

    @r.get("/tools")
    def discover(authorization: str | None = Header(None)):
        a = auth(authorization)
        names = authority.tools_for(store, a)
        store.log_gateway(a["id"], "discovery", "listed", 200, {"tools": names})
        return {"agent": a["id"], "tier": a["tier"], "limit_usd": a["limit_usd"], "tools": [schema(n) for n in names]}

    @r.post("/call")
    def call(body: Call, authorization: str | None = Header(None)):
        a = auth(authorization)       # re-read on every call, so grid edits and firings apply to the very next one
        if body.tool not in authority.tools_for(store, a):
            raise deny(a, body.tool, 403, f"tool not available to {a['id']} at tier {a['tier']}")[1]
        try:
            result = dispatch(a, body)
        except PayPalError as e:
            store.log_gateway(a["id"], body.tool, "paypal_error", 502, str(e))
            raise HTTPException(502, {"error": str(e)})
        store.log_gateway(a["id"], body.tool, "allowed", 200, result)
        return result

    def dispatch(a, body):
        if body.tool == "catalog_search":
            return {"catalog": CATALOG}
        if body.tool == "paypal_get_capture":
            c = paypal.get_capture(body.args.get("capture_id", ""))
            return {"capture_id": c["id"], "status": c["status"]}
        if body.tool == "paypal_list_disputes":
            return {"disputes": paypal.list_disputes()}
        if body.tool == "paypal_refund_capture":
            return refund(a, body.args.get("capture_id", ""))
        return purchase(a, body.args)

    def purchase(a, args):
        item = next((c for c in CATALOG if c["sku"] == args.get("sku")), None)
        qty = args.get("qty")
        if not item or not isinstance(qty, int) or qty < 1:
            raise HTTPException(422, {"error": "unknown sku or bad qty"})
        amount = round(item["unit_usd"] * qty, 2)
        grant = store.one("SELECT * FROM grants WHERE agent_id=? AND state='open' AND sku=? AND qty=?", a["id"], item["sku"], qty)
        if "paypal_purchase" not in authority.ROLE_TOOLS[a["id"]] and not grant:
            raise deny(a, "paypal_purchase", 403, "purchase does not match the handed-over reorder")[1]
        cap = grant["max_usd"] if grant else a["limit_usd"]
        if amount > cap:
            log_id, err = deny(a, "paypal_purchase", 403, f"${amount:.2f} is over the ${cap:.2f} limit", {"amount_usd": amount})
            authority.apply_event(store, a["id"], "over_limit_attempt", f"gateway-{log_id}",
                                  f"tried ${amount:.2f} against ${cap:.2f}", "gateway log")
            raise err
        if not grant and amount > a["review_over_usd"]:
            rid = store.run("INSERT INTO reviews (agent_id,sku,qty,amount_usd,state,created) VALUES (?,?,?,?,'pending',?)",
                            a["id"], item["sku"], qty, amount, time.time())
            return {"status": "HELD_FOR_OWNER_REVIEW", "review_id": rid, "amount_usd": amount}
        result = execute_purchase(store, paypal, a["id"], item, qty, amount, grant["id"] if grant else None)
        if grant:
            store.run("UPDATE grants SET state='used' WHERE id=?", grant["id"])
        return result

    def refund(a, capture_id):
        t = store.one("SELECT * FROM tickets WHERE capture_id=? AND state='open'", capture_id)
        if not t:
            raise deny(a, "paypal_refund_capture", 403, "no open return ticket for this capture")[1]
        result = paypal.refund(capture_id)
        store.run("UPDATE tickets SET state='refunded' WHERE id=?", t["id"])
        store.run("UPDATE purchases SET refund_id=? WHERE capture_id=?", result["refund_id"], capture_id)
        return {"capture_id": capture_id, **result}

    return r


def execute_purchase(store, paypal, agent_id, item, qty, amount, task_id=None) -> dict:
    """Pays and records. custom_id names the agent inside PayPal, so the reconciler attributes from PayPal's own record."""
    p = paypal.purchase(amount, f"{qty} x {item['name']}", json.dumps({"agent": agent_id, "sku": item["sku"], "qty": qty}))
    store.run("INSERT INTO purchases (capture_id,order_id,agent_id,sku,qty,amount_usd,task_id,paypal_status,created) VALUES (?,?,?,?,?,?,?,?,?)",
              p["capture_id"], p["order_id"], agent_id, item["sku"], qty, amount, str(task_id or ""), p["status"], time.time())
    return {**p, "sku": item["sku"], "qty": qty, "amount_usd": amount}
