"""The mesh database (mesh.db): a separate file from the settings and question log, so a backup of those stays small and a
long-running record of the radio's surroundings can be pruned, exported or deleted on its own.

Every "hour" column is whole hours since 1970 (UTC). All times are this computer's clock, never the radio's, which can be wrong.
Thread-safe: one connection, one lock, like store.py.
"""
import re
import sqlite3
import threading
import time
from pathlib import Path

NODE_ID_RE = re.compile(r"^![0-9a-f]{8}$")
LABEL_MAX, NOTE_MAX = 40, 500
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
NODE_FORGET_S = 90 * 86400  # a node not heard for this long is forgotten (its notes are kept: they are yours)
TRAIL_KEEP_FACTOR = 3        # positions (where nodes have been) are kept this many times as long as the other time series
METRICS = ("battery", "voltage", "channel_util", "air_util_tx", "uptime_s", "temperature", "humidity", "pressure")
NODE_FIELDS = ("num", "name", "short", "hw", "role", "hops", "snr", "rssi", "last_direct", "via_mqtt", "favorite", "has_key",
               "lat", "lon", "alt", "pos_ts", "pos_source", *METRICS, "metrics_ts")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS nodes (          -- every node we have heard of, kept after the radio forgets it
    node_id TEXT PRIMARY KEY, num INTEGER, name TEXT, short TEXT, hw TEXT, role TEXT,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL,     -- 0 = never actually heard (known only from the radio's list)
    hops INTEGER, snr REAL, rssi INTEGER,                   -- as of the last packet
    last_direct REAL,                                       -- when we last heard it with no relay in between
    via_mqtt INTEGER, favorite INTEGER, has_key INTEGER,
    lat REAL, lon REAL, alt REAL, pos_ts REAL, pos_source TEXT,   -- the last position captured: 'packet' or 'radio'
    {", ".join(m + " REAL" for m in METRICS)}, metrics_ts REAL
);
CREATE TABLE IF NOT EXISTS node_notes (      -- what you add yourself; never sent anywhere
    node_id TEXT PRIMARY KEY, label TEXT, note TEXT, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS packets (hour INTEGER NOT NULL, portnum TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (hour, portnum));
CREATE TABLE IF NOT EXISTS talkers (hour INTEGER NOT NULL, node_id TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (hour, node_id));
CREATE TABLE IF NOT EXISTS links (           -- signal of nodes heard DIRECTLY, per hour
    hour INTEGER NOT NULL, node_id TEXT NOT NULL, n INTEGER NOT NULL,
    snr_sum REAL NOT NULL, snr_min REAL NOT NULL, snr_max REAL NOT NULL,
    rssi_sum REAL NOT NULL, rssi_min REAL NOT NULL, rssi_max REAL NOT NULL, PRIMARY KEY (hour, node_id)
);
CREATE TABLE IF NOT EXISTS hops (hour INTEGER NOT NULL, hops INTEGER NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (hour, hops));
CREATE TABLE IF NOT EXISTS telemetry (       -- one row per broadcast heard
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, node_id TEXT NOT NULL, kind TEXT NOT NULL,
    {", ".join(m + " REAL" for m in METRICS)}, snr REAL, rssi INTEGER, hops INTEGER
);
CREATE INDEX IF NOT EXISTS telemetry_node_ts ON telemetry (node_id, ts);
CREATE INDEX IF NOT EXISTS telemetry_ts ON telemetry (ts);
CREATE TABLE IF NOT EXISTS positions (       -- a node's trail: a point each time it is seen somewhere new
    id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL, ts REAL NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, alt REAL
);
CREATE INDEX IF NOT EXISTS positions_node ON positions (node_id, id);
"""


class NoteError(ValueError):
    """Something the operator should see, in plain words (a ValueError so the API shows it as a 400)."""


def _text(value, limit, what, multiline=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise NoteError(f"The {what} must be text.")
    value = CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    value = value.strip() if multiline else " ".join(value.split())
    if len(value) > limit:
        raise NoteError(f"The {what} is too long ({len(value)} of {limit} characters).")
    return value or None


class MeshStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    # ---- writing what was heard --------------------------------------------------------------------------------------
    def write(self, *, nodes: dict, packets: dict, talkers: dict, links: dict, hops: dict, telemetry: list, positions: list) -> None:
        """Everything gathered since the last flush, in one transaction. Counters ADD to the hour's row (flush runs many times an hour)."""
        with self._lock:
            db = self._db
            for node_id, f in nodes.items():
                self._upsert_node(db, node_id, f)
            for (hour, portnum), n in packets.items():
                db.execute("INSERT INTO packets VALUES (?,?,?) ON CONFLICT(hour, portnum) DO UPDATE SET n = n + excluded.n", (hour, portnum, n))
            for (hour, node_id), n in talkers.items():
                db.execute("INSERT INTO talkers VALUES (?,?,?) ON CONFLICT(hour, node_id) DO UPDATE SET n = n + excluded.n", (hour, node_id, n))
            for (hour, node_id), (n, ss, smin, smax, rs, rmin, rmax) in links.items():
                db.execute("INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(hour, node_id) DO UPDATE SET n=n+excluded.n, "
                           "snr_sum=snr_sum+excluded.snr_sum, snr_min=MIN(snr_min, excluded.snr_min), snr_max=MAX(snr_max, excluded.snr_max), "
                           "rssi_sum=rssi_sum+excluded.rssi_sum, rssi_min=MIN(rssi_min, excluded.rssi_min), rssi_max=MAX(rssi_max, excluded.rssi_max)",
                           (hour, node_id, n, ss, smin, smax, rs, rmin, rmax))
            for (hour, h), n in hops.items():
                db.execute("INSERT INTO hops VALUES (?,?,?) ON CONFLICT(hour, hops) DO UPDATE SET n = n + excluded.n", (hour, h, n))
            for t in telemetry:
                cols = ["ts", "node_id", "kind", *[m for m in METRICS if t.get(m) is not None], *[c for c in ("snr", "rssi", "hops") if t.get(c) is not None]]
                db.execute(f"INSERT INTO telemetry ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [t[c] for c in cols])
            for p in positions:
                db.execute("INSERT INTO positions (node_id, ts, lat, lon, alt) VALUES (?,?,?,?,?)", (p["node_id"], p["ts"], p["lat"], p["lon"], p.get("alt")))
            db.commit()

    @staticmethod
    def _upsert_node(db, node_id: str, f: dict) -> None:
        """Insert or update one node. A field that is None is left as it was (the radio's list often drops fields), last_seen only grows."""
        cols = [c for c in NODE_FIELDS if f.get(c) is not None]
        seen = f.get("last_seen") or 0
        first = f.get("first_seen") or seen or time.time()
        names = ["node_id", "first_seen", "last_seen", *cols]
        sets = ", ".join([f"{c}=excluded.{c}" for c in cols] + ["last_seen=MAX(last_seen, excluded.last_seen)", "first_seen=MIN(first_seen, excluded.first_seen)"])
        db.execute(f"INSERT INTO nodes ({', '.join(names)}) VALUES ({', '.join('?' * len(names))}) ON CONFLICT(node_id) DO UPDATE SET {sets}",
                   [node_id, first, seen, *[f[c] for c in cols]])

    def last_position(self, node_id: str):
        with self._lock:
            return self._db.execute("SELECT lat, lon FROM positions WHERE node_id=? ORDER BY id DESC LIMIT 1", (node_id,)).fetchone()

    # ---- reading ------------------------------------------------------------------------------------------------------
    def nodes(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT n.*, u.label, u.note, u.updated AS note_updated FROM nodes n LEFT JOIN node_notes u USING (node_id)").fetchall()
        return [dict(r) for r in rows]

    def node(self, node_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT n.*, u.label, u.note, u.updated AS note_updated FROM nodes n LEFT JOIN node_notes u USING (node_id) "
                                 "WHERE node_id=?", (node_id,)).fetchone()
        return dict(r) if r else None

    def telemetry(self, node_id: str, since: float = 0.0, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM telemetry WHERE node_id=? AND ts>=? ORDER BY ts DESC LIMIT ?", (node_id, since, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def positions(self, node_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT ts, lat, lon, alt FROM positions WHERE node_id=? ORDER BY id DESC LIMIT ?", (node_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def counts(self) -> dict:
        """Rows and time span of each dataset, for the operator to see what is being kept."""
        out = {}
        with self._lock:
            for table, col in (("nodes", "last_seen"), ("telemetry", "ts"), ("positions", "ts"), ("packets", "hour"), ("links", "hour"), ("hops", "hour")):
                r = self._db.execute(f"SELECT COUNT(*) AS n, MIN({col}) AS lo, MAX({col}) AS hi FROM {table}").fetchone()
                out[table] = {"rows": r["n"], "first": r["lo"], "last": r["hi"]}
        return out

    # ---- your own labels and notes -------------------------------------------------------------------------------------
    def set_note(self, node_id: str, label, note, now: float | None = None) -> dict:
        if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
            raise NoteError("That is not a node id.")
        label, note = _text(label, LABEL_MAX, "label"), _text(note, NOTE_MAX, "note", multiline=True)
        with self._lock:
            if label is None and note is None:
                self._db.execute("DELETE FROM node_notes WHERE node_id=?", (node_id,))
            else:
                self._db.execute("INSERT INTO node_notes VALUES (?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET label=excluded.label, "
                                 "note=excluded.note, updated=excluded.updated", (node_id, label, note, now or time.time()))
            self._db.commit()
        return {"node_id": node_id, "label": label, "note": note}

    # ---- keeping it small ----------------------------------------------------------------------------------------------
    def prune(self, now: float, retention_days: int) -> dict:
        """Delete what is older than the retention window. Notes are never deleted."""
        cut, hour_cut = now - retention_days * 86400, int((now - retention_days * 86400) // 3600)
        trail_cut = now - retention_days * TRAIL_KEEP_FACTOR * 86400
        with self._lock:
            d = {}
            for key, sql, arg in (("telemetry", "DELETE FROM telemetry WHERE ts < ?", cut), ("positions", "DELETE FROM positions WHERE ts < ?", trail_cut),
                                  ("packets", "DELETE FROM packets WHERE hour < ?", hour_cut), ("talkers", "DELETE FROM talkers WHERE hour < ?", hour_cut),
                                  ("links", "DELETE FROM links WHERE hour < ?", hour_cut), ("hops", "DELETE FROM hops WHERE hour < ?", hour_cut),
                                  ("nodes", "DELETE FROM nodes WHERE last_seen > 0 AND last_seen < ?", now - NODE_FORGET_S)):
                d[key] = self._db.execute(sql, (arg,)).rowcount
            self._db.commit()
        return d

    def close(self) -> None:
        with self._lock:
            self._db.close()
