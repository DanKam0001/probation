"""SQLite state: agents, purchases, score events, tickets, grants, reviews, the gateway log and owner decisions."""
import json
import secrets
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT, job TEXT, token TEXT, tier TEXT, score INTEGER,
    limit_usd REAL, review_over_usd REAL, clean_count INTEGER DEFAULT 0, last_txn TEXT DEFAULT '', status_note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS purchases (capture_id TEXT PRIMARY KEY, order_id TEXT, agent_id TEXT, sku TEXT, qty INTEGER,
    amount_usd REAL, task_id TEXT, paypal_status TEXT, refund_id TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, kind TEXT, txn_id TEXT, delta INTEGER,
    tier_after TEXT, detail TEXT, source TEXT, created REAL, UNIQUE(agent_id, kind, txn_id));
CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, capture_id TEXT UNIQUE, reason TEXT, state TEXT, created REAL);
CREATE TABLE IF NOT EXISTS grants (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, from_agent TEXT, sku TEXT, qty INTEGER,
    max_usd REAL, state TEXT, created REAL);
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, sku TEXT, qty INTEGER, amount_usd REAL,
    state TEXT, capture_id TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS gateway_log (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, tool TEXT, outcome TEXT, http INTEGER,
    detail TEXT, created REAL);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, action TEXT, reason TEXT, evidence TEXT, created REAL);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, task TEXT, brain TEXT, transcript TEXT, created REAL);
"""
SEED = [("ads", "Ads agent", "Buys ad placements", "active", 70, 40.0, 500.0),
        ("supplies", "Supplies agent", "Reorders cafe stock", "active", 70, 100.0, 500.0),
        ("refunds", "Refunds agent", "Handles returns and refunds", "active", 70, 25.0, 500.0)]


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)
        if not self.one("SELECT 1 FROM agents"):
            self.seed()

    def seed(self):
        for a, name, job, tier, score, limit, review in SEED:
            self.run("INSERT INTO agents (id,name,job,token,tier,score,limit_usd,review_over_usd) VALUES (?,?,?,?,?,?,?,?)",
                     a, name, job, secrets.token_urlsafe(24), tier, score, limit, review)

    def reset(self):
        with self.lock:
            for t in ("agents", "purchases", "events", "tickets", "grants", "reviews", "gateway_log", "decisions", "runs"):
                self.db.execute(f"DELETE FROM {t}")
            self.db.commit()
            self.seed()

    def run(self, sql: str, *args) -> int:
        with self.lock:
            c = self.db.execute(sql, args)
            self.db.commit()
            return c.lastrowid

    def all(self, sql: str, *args) -> list:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, *args) -> dict | None:
        rows = self.all(sql, *args)
        return rows[0] if rows else None

    def agent(self, agent_id: str) -> dict | None:
        return self.one("SELECT * FROM agents WHERE id=?", agent_id)

    def agent_by_token(self, token: str) -> dict | None:
        return self.one("SELECT * FROM agents WHERE token=?", token)

    def log_gateway(self, agent_id: str, tool: str, outcome: str, http: int, detail: dict | str) -> int:
        return self.run("INSERT INTO gateway_log (agent_id,tool,outcome,http,detail,created) VALUES (?,?,?,?,?,?)",
                        agent_id, tool, outcome, http, json.dumps(detail) if not isinstance(detail, str) else detail, time.time())
