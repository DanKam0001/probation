"""The rules: what each tier may do, and how verified outcomes move an agent up or down.

Every score change needs a transaction ID (PayPal) or a gateway log ID, so nothing moves on an agent's own claim.
"""
import time

TIERS = ["fired", "demoted", "active", "trusted"]
ROLE_TOOLS = {"ads": ["paypal_purchase", "paypal_get_capture"],
              "supplies": ["paypal_purchase", "paypal_get_capture"],
              "refunds": ["paypal_refund_capture", "paypal_get_capture", "paypal_list_disputes"]}
PAYPAL_TOOLS = {"paypal_purchase", "paypal_refund_capture", "paypal_get_capture", "paypal_list_disputes"}
CLEAN_TO_PROMOTE = 3
DEMOTED_LIMIT_FACTOR = 0.5
PROMOTED_LIMIT_FACTOR = 1.5
# kind -> (score delta, tier change)
RULES = {"clean_settlement": (5, None), "over_limit_attempt": (-5, None), "partial_refund": (-15, "demote"),
         "dispute_open": (-15, "demote"), "full_refund": (-40, "fire"), "dispute_lost": (-40, "fire")}


def tools_for(store, agent: dict) -> list:
    """Discovery and every call use this one list, so a removed tool is gone from both at once."""
    tools = ["catalog_search"]
    if agent["tier"] != "fired":
        tools += ROLE_TOOLS[agent["id"]]
        if "paypal_purchase" not in tools and store.one(
                "SELECT 1 FROM grants WHERE agent_id=? AND state='open'", agent["id"]):
            tools.append("paypal_purchase")
    return tools


def apply_event(store, agent_id: str, kind: str, txn_id: str, detail: str, source: str) -> dict | None:
    """Apply one verified outcome. The same event on the same transaction only ever counts once."""
    if not txn_id:
        raise ValueError("a score change needs a transaction or gateway log ID")
    delta, change = RULES[kind]
    with store.lock:
        if store.one("SELECT 1 FROM events WHERE agent_id=? AND kind=? AND txn_id=?", agent_id, kind, txn_id):
            return None
        a = store.agent(agent_id)
        tier, limit, clean = a["tier"], a["limit_usd"], a["clean_count"]
        if change == "fire":
            tier, clean = "fired", 0
        elif change == "demote" and tier != "fired":
            tier, limit, clean = "demoted", round(limit * DEMOTED_LIMIT_FACTOR, 2), 0
        elif kind == "clean_settlement" and tier != "fired":
            clean += 1
            if clean >= CLEAN_TO_PROMOTE and TIERS.index(tier) < len(TIERS) - 1:
                tier, limit, clean = TIERS[TIERS.index(tier) + 1], round(limit * PROMOTED_LIMIT_FACTOR, 2), 0
        score = max(0, min(100, a["score"] + delta))
        store.run("UPDATE agents SET tier=?, score=?, limit_usd=?, clean_count=?, last_txn=?, status_note=? WHERE id=?",
                  tier, score, limit, clean, txn_id, f"{kind}: {detail}", agent_id)
        store.run("INSERT INTO events (agent_id,kind,txn_id,delta,tier_after,detail,source,created) VALUES (?,?,?,?,?,?,?,?)",
                  agent_id, kind, txn_id, delta, tier, detail, source, time.time())
    return {"agent_id": agent_id, "kind": kind, "txn_id": txn_id, "delta": delta, "tier": tier}


def reinstate(store, agent_id: str, reason: str) -> dict:
    """An owner decision. The agent returns on demoted terms, and the evidence is frozen into the log."""
    a = store.agent(agent_id)
    if a["tier"] != "fired":
        raise ValueError("only a fired agent can be reinstated")
    if not reason.strip():
        raise ValueError("a reinstatement needs a reason")
    evidence = [e["txn_id"] for e in store.all("SELECT txn_id FROM events WHERE agent_id=? ORDER BY id", agent_id)]
    limit = round(a["limit_usd"] * DEMOTED_LIMIT_FACTOR, 2)
    store.run("UPDATE agents SET tier='demoted', limit_usd=?, clean_count=0, status_note=? WHERE id=?",
              limit, f"reinstated by owner: {reason}", agent_id)
    store.run("INSERT INTO decisions (agent_id,action,reason,evidence,created) VALUES (?,?,?,?,?)",
              agent_id, "reinstate", reason, ",".join(evidence), time.time())
    return store.agent(agent_id)
