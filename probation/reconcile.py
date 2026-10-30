"""Scores come from PayPal readback here, never from what an agent says it did."""
import json

from . import authority


def reconcile(store, paypal) -> list:
    changes = []
    for p in store.all("SELECT * FROM purchases ORDER BY created"):
        c = paypal.get_capture(p["capture_id"])
        status, agent = c["status"], json.loads(c.get("custom_id") or "{}").get("agent", p["agent_id"])
        store.run("UPDATE purchases SET paypal_status=? WHERE capture_id=?", status, p["capture_id"])
        if status == "REFUNDED":
            kind = "full_refund"
        elif status == "PARTIALLY_REFUNDED":
            kind = "partial_refund"
        elif status == "COMPLETED" and not store.one("SELECT 1 FROM tickets WHERE capture_id=?", p["capture_id"]):
            kind = "clean_settlement"
        else:
            continue
        detail = f"{p['qty']} x {p['sku']} for ${p['amount_usd']:.2f}, PayPal status {status}"
        e = authority.apply_event(store, agent, kind, p["capture_id"], detail, "PayPal capture readback")
        if e:
            changes.append({**e, "paypal_status": status, "refund_id": p["refund_id"]})
    for d in paypal.list_disputes():
        capture = next((t.get("seller_transaction_id") for t in d.get("disputed_transactions", [])), None)
        p = store.one("SELECT * FROM purchases WHERE capture_id=?", capture) if capture else None
        if p:
            kind = "dispute_lost" if d.get("dispute_outcome", {}).get("outcome_code") == "RESOLVED_BUYER_FAVOUR" else "dispute_open"
            e = authority.apply_event(store, p["agent_id"], kind, d["dispute_id"], f"on {capture}", "PayPal Disputes readback")
            if e:
                changes.append(e)
    return changes
