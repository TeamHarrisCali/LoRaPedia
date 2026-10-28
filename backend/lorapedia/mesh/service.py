"""Collects what the radio hears, remembers every node, and answers questions about them from the mesh database.

Everything here runs on the asyncio loop: the radio adapter hands each packet over with call_soon_threadsafe, `record` only updates
in-memory buffers, and a background task writes them to SQLite every few seconds (in a thread, so the loop never waits on the disk).
Nothing here transmits anything. Only counts, signal, positions and sensor readings are kept: never the content of a message.
"""
import asyncio
import logging
import math
import time
from collections import Counter
from typing import Callable

from ..config import Settings
from .observe import METRICS_KEYS, Observation, observe
from .store import MeshStore

log = logging.getLogger(__name__)

FLUSH_EVERY_S = 5.0
IMPORT_EVERY_S = 30.0
PRUNE_EVERY_S = 3600.0
TRAIL_MIN_KM = 0.015          # a node must move this far (15 m) to add a point to its trail
DIRECT_WINDOW_S = 3 * 3600    # heard with no relay this recently counts as 0 hops, whatever the radio's list says
TELEMETRY_MIN_GAP_S = 60.0    # at most one reading per node and kind per minute (a relay may deliver the same broadcast twice)


def distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle (haversine) distance in kilometres, mean Earth radius 6371 km."""
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


class MeshService:
    def __init__(self, store: MeshStore, settings: Callable[[], Settings], clock: Callable[[], float] = time.time,
                 radio_nodes: Callable[[], list[dict]] = lambda: [], my_id: Callable[[], str] = lambda: "") -> None:
        self.store, self._settings, self._clock = store, settings, clock
        self._radio_nodes, self._my_id = radio_nodes, my_id
        self._reset()
        self._last_pos: dict[str, tuple[float, float]] = {}      # node id -> the last trail point (memory, so a flush is not needed to compare)
        self._last_tel: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task | None = None
        self.last_prune = 0.0

    def _reset(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._packets, self._talkers, self._hops = Counter(), Counter(), Counter()
        self._links: dict[tuple, list] = {}
        self._telemetry: list[dict] = []
        self._positions: list[dict] = []

    @property
    def recording(self) -> bool:
        return self._settings().mesh_record

    # ---- what the radio hears -------------------------------------------------------------------------------------------
    def on_packet(self, packet: dict, my_num: int | None) -> None:
        """Called on the event loop for every packet the radio receives."""
        if self.recording:
            obs = observe(packet, my_num, self._clock())
            if obs is not None:
                self.record(obs)

    def record(self, obs: Observation) -> None:
        hour = int(obs.ts // 3600)
        self._packets[(hour, obs.portnum)] += 1
        self._talkers[(hour, obs.node_id)] += 1
        if obs.hops is not None:
            self._hops[(hour, obs.hops)] += 1
        if obs.direct and obs.snr is not None:  # the signal is the LAST hop's, so it describes the sender only when nobody relayed it
            l = self._links.get((hour, obs.node_id))
            if l is None:
                self._links[(hour, obs.node_id)] = [1, obs.snr, obs.snr, obs.snr, obs.rssi, obs.rssi, obs.rssi]
            else:
                l[0] += 1; l[1] += obs.snr; l[2] = min(l[2], obs.snr); l[3] = max(l[3], obs.snr)
                l[4] += obs.rssi; l[5] = min(l[5], obs.rssi); l[6] = max(l[6], obs.rssi)
        f = self._nodes.setdefault(obs.node_id, {})
        f.update(last_seen=obs.ts, num=obs.num, via_mqtt=int(obs.via_mqtt))
        f.setdefault("first_seen", obs.ts)
        if obs.hops is not None:
            f["hops"] = obs.hops
        if obs.snr is not None:
            f["snr"], f["rssi"] = obs.snr, obs.rssi
        if obs.direct:
            f["last_direct"] = obs.ts
        if obs.user:
            f.update({k: v for k, v in obs.user.items() if v is not None})
            if "has_key" in obs.user:
                f["has_key"] = int(obs.user["has_key"])
        if obs.position:
            self._position(obs.node_id, f, obs.position, obs.ts, "packet")
        if obs.telemetry:
            m = obs.telemetry["metrics"]
            f.update(m)
            f["metrics_ts"] = obs.ts
            key = (obs.node_id, obs.telemetry["kind"])
            if obs.ts - self._last_tel.get(key, 0) >= TELEMETRY_MIN_GAP_S:
                self._last_tel[key] = obs.ts
                self._telemetry.append({"ts": obs.ts, "node_id": obs.node_id, "kind": obs.telemetry["kind"], **m,
                                        "snr": obs.snr, "rssi": obs.rssi, "hops": obs.hops})

    def _position(self, node_id: str, f: dict, pos: tuple, ts: float, source: str) -> None:
        lat, lon, alt = pos
        f.update(lat=lat, lon=lon, alt=alt, pos_ts=ts, pos_source=source)
        last = self._last_pos.get(node_id)
        if last is None:
            row = self.store.last_position(node_id)
            last = (row["lat"], row["lon"]) if row else None
        if last is None or distance_km(last[0], last[1], lat, lon) >= TRAIL_MIN_KM:
            self._last_pos[node_id] = (lat, lon)
            self._positions.append({"node_id": node_id, "ts": ts, "lat": lat, "lon": lon, "alt": alt})
        else:
            self._last_pos[node_id] = last

    def import_radio_nodes(self, rows: list[dict] | None = None) -> int:
        """Copy the radio's own node list into the database (names, hardware, last position, battery...)."""
        if not self.recording:
            return 0
        rows = self._radio_nodes() if rows is None else rows
        now = self._clock()
        for r in rows:
            f = self._nodes.setdefault(r["id"], {})
            f.setdefault("first_seen", now)
            heard = r.get("last_heard")
            if heard and heard <= now:  # the radio's stamp is its own clock: believed only when it is not in the future
                f["last_seen"] = max(f.get("last_seen", 0), heard)
            for k in ("num", "name", "short", "hw", "role", "hops", "snr", "via_mqtt", "favorite", "has_key", *METRICS_KEYS):
                v = r.get(k)
                if v is not None and k not in f:  # what a packet just told us is fresher than the list
                    f[k] = int(v) if isinstance(v, bool) else v
            if r.get("lat") is not None and "lat" not in f:
                self._position(r["id"], f, (r["lat"], r["lon"], r.get("alt")), r.get("pos_time") or now, "radio")  # when the fix was made, if known
        return len(rows)

    # ---- writing to disk ------------------------------------------------------------------------------------------------
    async def flush(self) -> None:
        """Write what has been gathered since the last flush. Safe to call at any time."""
        if not (self._nodes or self._packets or self._telemetry or self._positions):
            return
        batch = dict(nodes=self._nodes, packets=self._packets, talkers=self._talkers, links=self._links, hops=self._hops,
                     telemetry=self._telemetry, positions=self._positions)
        self._reset()
        try:
            await asyncio.to_thread(self.store.write, **batch)
        except Exception:
            log.exception("could not save mesh data")

    def prune(self) -> dict:
        self.last_prune = self._clock()
        return self.store.prune(self.last_prune, self._settings().mesh_retention_days)

    # ---- reading --------------------------------------------------------------------------------------------------------
    def rows(self) -> list[dict]:
        """Every node we know, newest first, with your label and note, live/remembered, and measured hops."""
        now, me = self._clock(), self._my_id()
        live = {r["id"] for r in self._radio_nodes()}
        out = []
        for n in self.store.nodes():
            seen = n["last_seen"] if n["last_seen"] and n["last_seen"] <= now else None
            direct = bool(n["last_direct"]) and now - n["last_direct"] < DIRECT_WINDOW_S
            n["id"] = n.pop("node_id")
            n["us"] = n["id"] == me
            n.update(last_heard=seen, age_s=max(0, int(now - seen)) if seen else None, live=n["id"] in live, remembered=n["id"] not in live,
                     hops=0 if direct else n["hops"], hops_measured=direct,
                     display=n["label"] or n["name"] or n["short"] or n["id"])
            out.append(n)
        out.sort(key=lambda r: (r["age_s"] is None, r["age_s"] or 0))
        return out

    def summary(self, rows: list[dict] | None = None) -> dict:
        rows = self.rows() if rows is None else rows
        others = [r for r in rows if not r["us"]]

        def heard(secs):
            return sum(1 for r in others if r["age_s"] is not None and r["age_s"] <= secs)

        hour_util = [r["channel_util"] for r in others if r["age_s"] is not None and r["age_s"] <= 3600 and r["channel_util"] is not None]
        return {"nodes_total": len(rows), "heard_15m": heard(900), "heard_1h": heard(3600), "heard_24h": heard(86400),
                "direct": sum(1 for r in others if r["hops_measured"]),
                "with_position": sum(1 for r in others if r["lat"] is not None),
                "with_telemetry": sum(1 for r in others if r["metrics_ts"]),
                "via_mqtt": sum(1 for r in others if r["via_mqtt"]),
                "avg_channel_util": round(sum(hour_util) / len(hour_util), 1) if hour_util else None,
                "roles": dict(Counter(r["role"] or "?" for r in others).most_common(6)),
                "hardware": dict(Counter(r["hw"] or "?" for r in others).most_common(5))}

    # ---- the background task --------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.flush()

    async def _loop(self) -> None:
        next_import = 0.0
        while True:
            try:
                now = self._clock()
                if now >= next_import:
                    next_import = now + IMPORT_EVERY_S
                    self.import_radio_nodes()
                await self.flush()
                if now - self.last_prune >= PRUNE_EVERY_S:
                    await asyncio.to_thread(self.prune)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mesh data loop error")
            await asyncio.sleep(FLUSH_EVERY_S)
