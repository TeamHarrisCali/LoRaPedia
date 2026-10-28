"""Traceroute: ask the mesh which way a packet takes to one node and back, and keep the answer.

A traceroute is the only thing here that transmits something other than an answer to a direct message, and every one makes each node
on the path repeat a packet, so it is deliberately hard to do by accident: it starts only when the operator asks (never on a schedule, never
from the AI, never to a channel), one at a time, the same node at most once every 30 seconds, with a 60 second wait for the reply.
"""
import asyncio
import json
import time
from typing import Awaitable, Callable

from .observe import NODE_ID_RE, num

BROADCAST_NUM = 0xFFFFFFFF   # the firmware puts this in a route where a hop's identity was not reported
BROADCAST_ID = "!ffffffff"
UNKNOWN_SNR = -128           # "unknown" in the reply's signal values, which are in quarter-dB steps
COOLDOWN_S = 30.0
TIMEOUT_S = 60.0
KEEP_STATES = 20


class TracerouteError(ValueError):
    """Something the operator should see, in plain words (a ValueError so the API shows it as a 400)."""


class Busy(TracerouteError):
    """Another traceroute is running, or this node was traced a moment ago (the API answers 409)."""


def check_target(node_id, hop_limit, my_id: str = "") -> str:
    """The validated, lower-cased node id, or TracerouteError. Never the broadcast address, never ourselves."""
    node_id = node_id.strip().lower() if isinstance(node_id, str) else ""
    if not NODE_ID_RE.match(node_id) or node_id == BROADCAST_ID:
        raise TracerouteError("Choose one node (an id like !1a2b3c4d). A traceroute is never sent to everyone.")
    if node_id == my_id:
        raise TracerouteError("That is this radio itself.")
    if isinstance(hop_limit, bool) or not isinstance(hop_limit, int) or not 1 <= hop_limit <= 7:
        raise TracerouteError("The hop limit must be a whole number from 1 to 7.")
    return node_id


def _hop(n, snr_raw, by_num: dict) -> dict:
    """One node on a path: who it is, the signal it received with (dB, None if unknown) and where it is."""
    snr = None if not num(snr_raw) or snr_raw == UNKNOWN_SNR else snr_raw / 4  # the firmware sends quarter-dB steps
    if n == BROADCAST_NUM or not isinstance(n, int):
        return {"num": None, "id": None, "name": "Unknown node", "snr": snr, "lat": None, "lon": None}
    node = by_num.get(n) or {}
    return {"num": n, "id": node.get("id") or f"!{n:08x}", "name": node.get("name"), "snr": snr, "lat": node.get("lat"), "lon": node.get("lon")}


def build_path(start: int, relays: list, end: int, snrs, by_num: dict) -> list[dict]:
    """[start] + relays + [end]. `snrs` has one entry per link (len(relays)+1) and is used only if it does."""
    relays = [r for r in relays if isinstance(r, int) and not isinstance(r, bool) and 0 <= r <= BROADCAST_NUM] if isinstance(relays, list) else []
    usable = isinstance(snrs, list) and len(snrs) == len(relays) + 1
    chain = [start, *relays, end]
    return [_hop(n, snrs[i - 1] if usable and i > 0 else None, by_num) for i, n in enumerate(chain)]


def parse_reply(packet: dict, our_num: int | None, by_num: dict) -> tuple[list[dict], list[dict] | None, dict]:
    """A traceroute reply -> (path towards, path back or None, the plain decoded fields). Raises TracerouteError."""
    decoded = packet.get("decoded") if isinstance(packet.get("decoded"), dict) else {}
    if decoded.get("portnum") == "ROUTING_APP":
        raise TracerouteError(f"The radio reported: {(decoded.get('routing') or {}).get('errorReason', 'an error')}.")
    rd = decoded.get("traceroute")
    if rd is None and isinstance(decoded.get("payload"), (bytes, bytearray)):  # a raw payload: decode it ourselves
        from google.protobuf.json_format import MessageToDict
        from meshtastic.protobuf import mesh_pb2
        msg = mesh_pb2.RouteDiscovery()
        msg.ParseFromString(bytes(decoded["payload"]))
        rd = MessageToDict(msg)
    if not isinstance(rd, dict):
        raise TracerouteError("The reply did not contain a route.")
    rd = {k: v for k, v in rd.items() if k != "raw"}  # the library adds the protobuf object itself under "raw"
    target = packet.get("from")
    if not isinstance(target, int) or isinstance(target, bool):
        raise TracerouteError("The reply did not say who it came from.")
    us = packet.get("to") if isinstance(packet.get("to"), int) and not isinstance(packet.get("to"), bool) else our_num
    if us is None:
        raise TracerouteError("The reply did not say who it was for.")
    route, route_back = rd.get("route") or [], rd.get("routeBack") or []
    towards = build_path(us, route, target, rd.get("snrTowards"), by_num)
    snr_back = rd.get("snrBack")
    # The way back counts only if the reply carries a hop counter and its signal list matches the route.
    back_ok = "hopStart" in packet and isinstance(snr_back, list) and len(snr_back) == len(route_back) + 1
    return towards, build_path(target, route_back, us, snr_back, by_num) if back_ok else None, rd


Reply = Callable[[str, int, float], Awaitable[dict]]


class TracerouteService:
    """Runs one traceroute at a time on request and saves each outcome (success, timeout or error) in mesh.db."""

    def __init__(self, store, send: Reply, nodes_by_num: Callable[[], dict], my_id: Callable[[], str], my_num: Callable[[], int | None],
                 clock: Callable[[], float] = time.time, cooldown: float = COOLDOWN_S, timeout: float = TIMEOUT_S) -> None:
        self.store, self._send, self._by_num, self._my_id, self._my_num = store, send, nodes_by_num, my_id, my_num
        self._clock, self.cooldown, self.timeout = clock, cooldown, timeout
        self._last: dict[str, float] = {}      # node id -> when it was last traced (for the cooldown)
        self._states: dict[int, dict] = {}     # request id -> what the page polls
        self._next = 0
        self._running: asyncio.Task | None = None

    def start(self, node_id, hop_limit: int = 3) -> int:
        """Validate and start a traceroute in the background. Returns a request id to poll with state(). Raises TracerouteError / Busy."""
        node_id = check_target(node_id, hop_limit, self._my_id())
        if self._running is not None and not self._running.done():
            raise Busy("Another traceroute is in progress. Wait for it to finish.")
        wait = self.cooldown - (self._clock() - self._last.get(node_id, -1e12))
        if wait > 0:
            raise Busy(f"That node was traced moments ago (every traceroute makes nodes on the path repeat a packet). Try again in {int(wait) + 1} s.")
        self._last[node_id] = self._clock()
        self._next += 1
        rid = self._next
        self._states[rid] = {"id": rid, "status": "waiting", "node_id": node_id}
        for old in sorted(self._states)[:-KEEP_STATES]:
            del self._states[old]
        self._running = asyncio.get_running_loop().create_task(self._run(rid, node_id, hop_limit))
        return rid

    def state(self, rid: int) -> dict | None:
        return self._states.get(rid)

    async def _run(self, rid: int, node_id: str, hop_limit: int) -> None:
        t0 = self._clock()

        def finish(status: str, **fields) -> None:
            try:
                row_id = self.store.add_traceroute(ts=self._clock(), node_id=node_id, hop_limit=hop_limit, status=status,
                                                   latency_ms=int((self._clock() - t0) * 1000), **fields)
                self._states[rid] = {**self._states[rid], "status": status, "detail": fields.get("detail"), "trace": self.store.traceroute(row_id)}
            except Exception as exc:  # a result that cannot be saved must still be reported
                self._states[rid] = {**self._states[rid], "status": "error", "detail": f"The result could not be saved ({exc.__class__.__name__})."}

        try:
            packet = await asyncio.wait_for(self._send(node_id, hop_limit, self.timeout), self.timeout + 5)
        except (asyncio.TimeoutError, TimeoutError):
            return finish("timeout", detail=f"No answer within {self.timeout:.0f} s: the node is off, or out of reach within {hop_limit} hops.")
        except Exception as exc:
            return finish("error", detail=f"Could not send: {getattr(exc, 'message', None) or str(exc) or exc.__class__.__name__}"[:200])
        try:
            towards, back, raw = parse_reply(packet, self._my_num(), self._by_num())
        except TracerouteError as exc:
            return finish("error", detail=str(exc))
        except Exception as exc:
            return finish("error", detail=f"The reply could not be read ({exc.__class__.__name__}).")
        finish("ok", relays_towards=len(towards) - 2, relays_back=None if back is None else len(back) - 2, path_towards=towards, path_back=back,
               raw=raw, detail="Simulated: no radio was used." if packet.get("_simulated") else None)
