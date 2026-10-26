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
            rows = self._db.execute("SELECT * FROM queries ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["sources"] = json.loads(d["sources"])
            d["debug"] = json.loads(d["debug"])
            # Same shape as the live WebSocket event: `parts` is the list of messages, the count is separate.
            d["part_count"] = d["parts"]
            d["parts"] = d["answer"].split("\n")
            out.append(d)
        return out

    def get_json(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set_json(self, key: str, value) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                             (key, json.dumps(value)))
            self._db.commit()

    def activity_24h(self, now: float | None = None) -> list[int]:
        """Query counts for each of the last 24 hours, oldest first (the dashboard sparkline)."""
        now = now or time.time()
        start = now - 24 * 3600
        with self._lock:
            rows = self._db.execute("SELECT ts FROM queries WHERE ts >= ?", (start,)).fetchall()
        buckets = [0] * 24
        for r in rows:
            buckets[min(int((r["ts"] - start) // 3600), 23)] += 1
        return buckets

    def export_rows(self):
        """Every logged query, oldest first, as plain dicts (for CSV export)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, sender_id, sender_name, question, answer, bytes, byte_limit, parts, latency_s, sources "
                "FROM queries ORDER BY id").fetchall()
        for r in rows:
            d = dict(r)
            d["sources"] = "; ".join(json.loads(d["sources"]))
            yield d

    def today_stats(self) -> dict:
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, AVG(latency_s) AS avg FROM queries WHERE ts >= ?", (midnight,)
            ).fetchone()
        return {"queries": row["n"], "avg_latency_s": round(row["avg"] or 0.0, 2)}
