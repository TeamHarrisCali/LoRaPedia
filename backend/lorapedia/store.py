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
CREATE TABLE IF NOT EXISTS deliveries (   -- every message sent over a real radio that asked for an acknowledgement
    packet_id INTEGER PRIMARY KEY,
    query_id INTEGER,            -- the answer it was part of (NULL for a message you typed yourself)
    to_id TEXT NOT NULL, part INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL,
    state TEXT NOT NULL,         -- sent | relayed | delivered | failed
    detail TEXT, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS deliveries_query ON deliveries (query_id);
CREATE TABLE IF NOT EXISTS messages (     -- what you typed to a node or a channel, and what a node said back during a chat with you
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
    direction TEXT NOT NULL,     -- out | in
    node_id TEXT,                -- NULL = a post to a public channel
    channel INTEGER, text TEXT NOT NULL, packet_id INTEGER,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_node ON messages (node_id, id);
CREATE TABLE IF NOT EXISTS radio_config_backups (   -- the radio's own settings, copied before each change (never secrets: see radio/config.py)
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
    reason TEXT NOT NULL,        -- pulled | before changes | before restore
    radio TEXT, config TEXT NOT NULL   -- JSON: {section: {field: value}}
);
CREATE TABLE IF NOT EXISTS chats (        -- nodes you wrote to: until then, what they send back is for you, not for the AI
    node_id TEXT PRIMARY KEY, until REAL NOT NULL
);
"""

UNCONFIRMED_AFTER_S = 180   # a "sent" message with no acknowledgement this long afterwards is reported as unconfirmed
# worst first: an answer is only "delivered" when every part was
DELIVERY_ORDER = ("failed", "unconfirmed", "sent", "relayed", "delivered")


def summarize_delivery(states: list[str]) -> str | None:
    """One word for a whole answer from its parts' states (None when nothing was tracked, e.g. the simulator)."""
    return next((s for s in DELIVERY_ORDER if s in states), None) if states else None


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
        sent = self.deliveries_for([r["id"] for r in rows])
        out = []
        for r in rows:
            d = dict(r)
            parts = sent.get(d["id"], [])
            d["delivery"] = {"state": summarize_delivery([p["state"] for p in parts]), "parts": parts}
            d["sources"] = json.loads(d["sources"])
            d["debug"] = json.loads(d["debug"])
            # Same shape as the live WebSocket event: `parts` is the list of messages, the count is separate.
            d["part_count"] = d["parts"]
            d["parts"] = d["answer"].split("\n")
            out.append(d)
        return out

    # ---- delivery tracking ------------------------------------------------------------------------------------------------
    def delivery_sent(self, packet_id: int, to_id: str, part: int = 0, query_id: int | None = None, ts: float | None = None) -> None:
        now = ts or time.time()
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO deliveries (packet_id, query_id, to_id, part, ts, state, detail, updated) "
                             "VALUES (?,?,?,?,?,'sent',NULL,?)", (packet_id, query_id, to_id, part, now, now))
            self._db.commit()

    def delivery_update(self, packet_id: int, state: str, detail: str | None = None, ts: float | None = None) -> bool:
        """Record what the radio learned. Never takes back a delivery, and never goes from relayed back to sent."""
        if state not in ("relayed", "delivered", "failed"):
            raise ValueError("Unknown delivery state.")
        with self._lock:
            row = self._db.execute("SELECT state FROM deliveries WHERE packet_id=?", (packet_id,)).fetchone()
            if row is None or row["state"] == "delivered" or (state == "relayed" and row["state"] != "sent"):
                return False
            self._db.execute("UPDATE deliveries SET state=?, detail=?, updated=? WHERE packet_id=?", (state, detail, ts or time.time(), packet_id))
            self._db.commit()
        return True

    def delivery_attach(self, packet_ids: list[int], query_id: int) -> None:
        with self._lock:
            self._db.executemany("UPDATE deliveries SET query_id=? WHERE packet_id=?", [(query_id, p) for p in packet_ids])
            self._db.commit()

    @staticmethod
    def _delivery_view(r, now: float) -> dict:
        state = "unconfirmed" if r["state"] == "sent" and now - r["ts"] > UNCONFIRMED_AFTER_S else r["state"]
        return {"packet_id": r["packet_id"], "part": r["part"], "state": state, "detail": r["detail"], "to": r["to_id"], "updated": r["updated"]}

    def deliveries_for(self, query_ids: list[int], now: float | None = None) -> dict[int, list[dict]]:
        """{query id: [one entry per message that went out for it, in order]}"""
        if not query_ids:
            return {}
        now = now or time.time()
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM deliveries WHERE query_id IN ({', '.join('?' * len(query_ids))}) ORDER BY part, packet_id",
                                    query_ids).fetchall()
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["query_id"], []).append(self._delivery_view(r, now))
        return out

    def delivery_states(self, packet_ids: list[int], now: float | None = None) -> dict[int, dict]:
        packet_ids = [p for p in packet_ids if p is not None]
        if not packet_ids:
            return {}
        now = now or time.time()
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM deliveries WHERE packet_id IN ({', '.join('?' * len(packet_ids))})", packet_ids).fetchall()
        return {r["packet_id"]: self._delivery_view(r, now) for r in rows}

    # ---- copies of the radio's settings -------------------------------------------------------------------------------------
    def add_radio_backup(self, reason: str, radio: str | None, config: dict, keep: int = 40) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO radio_config_backups (ts, reason, radio, config) VALUES (?,?,?,?)", (time.time(), reason, radio, json.dumps(config)))
            self._db.execute("DELETE FROM radio_config_backups WHERE id NOT IN (SELECT id FROM radio_config_backups ORDER BY id DESC LIMIT ?)", (keep,))
            self._db.commit()
            return cur.lastrowid

    def radio_backups(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT id, ts, reason, radio FROM radio_config_backups ORDER BY id DESC")]

    def radio_backup(self, backup_id: int) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM radio_config_backups WHERE id=?", (backup_id,)).fetchone()
        return {**dict(r), "config": json.loads(r["config"])} if r else None

    # ---- what you typed, and chats --------------------------------------------------------------------------------------
    def add_message(self, direction: str, node_id: str | None, text: str, *, channel: int | None = None, packet_id: int | None = None,
                    simulated: bool = False, ts: float | None = None) -> int:
        if direction not in ("out", "in"):
            raise ValueError("A message goes out or comes in.")
        with self._lock:
            cur = self._db.execute("INSERT INTO messages (ts, direction, node_id, channel, text, packet_id, simulated) VALUES (?,?,?,?,?,?,?)",
                                   (ts or time.time(), direction, node_id, channel, text, packet_id, int(simulated)))
            self._db.commit()
            return cur.lastrowid

    def _with_delivery(self, rows) -> list[dict]:
        out = [dict(r) for r in rows]
        states = self.delivery_states([m["packet_id"] for m in out])
        for m in out:
            m["simulated"] = bool(m["simulated"])
            m["delivery"] = states.get(m["packet_id"])
        return out

    def thread(self, node_id: str, limit: int = 100) -> list[dict]:
        """Your conversation with one node, oldest first."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM messages WHERE node_id=? ORDER BY id DESC LIMIT ?", (node_id, limit)).fetchall()
        return self._with_delivery(reversed(rows))

    def public_posts(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM messages WHERE node_id IS NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return self._with_delivery(rows)

    def threads(self, limit: int = 100) -> list[dict]:
        """One row per node you have talked to, newest first, with the last message and when the chat window closes."""
        with self._lock:
            rows = self._db.execute(
                "SELECT m.node_id, m.id, m.ts, m.direction, m.text, c.until FROM messages m JOIN "
                "(SELECT node_id, MAX(id) AS last FROM messages WHERE node_id IS NOT NULL GROUP BY node_id) l ON m.id = l.last "
                "LEFT JOIN chats c ON c.node_id = m.node_id ORDER BY m.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def open_chat(self, node_id: str, until: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO chats (node_id, until) VALUES (?,?) ON CONFLICT(node_id) DO UPDATE SET until=excluded.until", (node_id, until))
            self._db.commit()

    def end_chat(self, node_id: str) -> bool:
        with self._lock:
            n = self._db.execute("DELETE FROM chats WHERE node_id=?", (node_id,)).rowcount
            self._db.commit()
        return bool(n)

    def chat_until(self, node_id: str) -> float | None:
        with self._lock:
            row = self._db.execute("SELECT until FROM chats WHERE node_id=?", (node_id,)).fetchone()
        return row["until"] if row else None

    def prune_messages(self, cutoff: float, now: float | None = None) -> int:
        """Forget what you typed (and what came back in chats) older than `cutoff`, with its delivery records, and chats long over."""
        with self._lock:
            n = self._db.execute("DELETE FROM messages WHERE ts < ?", (cutoff,)).rowcount
            self._db.execute("DELETE FROM deliveries WHERE query_id IS NULL AND ts < ?", (cutoff,))
            self._db.execute("DELETE FROM chats WHERE until < ?", (now or time.time(),))
            self._db.commit()
        return n

    def backup_to(self, dest: str | Path) -> None:
        """A consistent copy of the database, safe to take while the app is running (SQLite's online backup)."""
        out = sqlite3.connect(str(dest))
        try:
            with self._lock:
                self._db.backup(out)
        finally:
            out.close()

    def restore_from(self, src_path: str | Path) -> None:
        """Replace everything in the live database with a backup's contents (SQLite's online backup, in reverse)."""
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        try:
            with self._lock:
                src.backup(self._db)
                self._db.executescript(SCHEMA)  # a backup from an older version may lack a table added since
        finally:
            src.close()

    def last_query_ts(self) -> float | None:
        with self._lock:
            row = self._db.execute("SELECT MAX(ts) AS t FROM queries").fetchone()
        return row["t"]

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
