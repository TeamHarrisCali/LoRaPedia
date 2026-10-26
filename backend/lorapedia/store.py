"""SQLite persistence for settings and the query log (stdlib sqlite3, no ORM)."""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    sender_id TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,        -- parts joined with newlines
    bytes INTEGER NOT NULL,      -- size of the largest part
    byte_limit INTEGER NOT NULL,
    parts INTEGER NOT NULL,
    latency_s REAL NOT NULL,
    sources TEXT NOT NULL,       -- JSON list of titles
    debug TEXT NOT NULL          -- JSON: chunks + prompt, for the Live Feed drill-down
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def load_settings(self) -> Settings:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k='settings'").fetchone()
        return Settings.model_validate_json(row["v"]) if row else Settings()

    def save_settings(self, settings: Settings) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO kv(k, v) VALUES('settings', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (settings.model_dump_json(),),
            )
            self._db.commit()

    def log_query(self, *, sender_id, sender_name, question, parts: list[str], byte_limit, latency_s,
                  sources: list[str], debug: dict) -> int:
        size = max((len(p.encode("utf-8")) for p in parts), default=0)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO queries(ts, sender_id, sender_name, question, answer, bytes, byte_limit,"
                " parts, latency_s, sources, debug) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), sender_id, sender_name, question, "\n".join(parts), size, byte_limit,
                 len(parts), latency_s, json.dumps(sources), json.dumps(debug)),
            )
            self._db.commit()
            return cur.lastrowid

    def recent_queries(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM queries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["sources"] = json.loads(d["sources"])
            d["debug"] = json.loads(d["debug"])
            out.append(d)
        return out

    def today_stats(self) -> dict:
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, AVG(latency_s) AS avg FROM queries WHERE ts >= ?", (midnight,)
            ).fetchone()
        return {"queries": row["n"], "avg_latency_s": round(row["avg"] or 0.0, 2)}
