"""Turning what the radio hears into plain facts. Pure functions: no I/O, no clock of their own, easy to test.

A Meshtastic packet arrives as a dict (the library's JSON form). `observe` reads the parts we keep and checks every value, because
the mesh is full of strangers' devices: a broken sensor, a node that names itself with control characters, a position of 0,0.
"""
import math
import re
from dataclasses import dataclass, field

NODE_ID_RE = re.compile(r"^![0-9a-f]{8}$")
NAME_MAX, SHORT_MAX = 40, 8

# a sensor outside these limits is broken, not weather: it must not skew an average later
LIMITS = {"battery": (0, 101), "voltage": (0, 60), "channel_util": (0, 100), "air_util_tx": (0, 100), "uptime_s": (0, 10 * 365 * 86400),
          "temperature": (-90, 70), "humidity": (0, 100), "pressure": (300, 1100)}
DEVICE_KEYS = {"batteryLevel": "battery", "voltage": "voltage", "channelUtilization": "channel_util", "airUtilTx": "air_util_tx",
               "uptimeSeconds": "uptime_s"}
METRICS_KEYS = tuple(LIMITS)
ENVIRONMENT_KEYS = {"temperature": "temperature", "relativeHumidity": "humidity", "barometricPressure": "pressure"}


def csv_cell(value):
    """A value made safe to open in a spreadsheet: text that starts with =, +, - or @ (or a tab or return) would be run as a formula,
    and names and messages come from strangers. Numbers pass through unchanged."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def num(v) -> bool:
    """A real, finite int or float (not a bool, NaN or infinity)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean(s, n=NAME_MAX):
    """Node names are chosen by strangers: drop control characters and cap the length before showing or storing them."""
    if not isinstance(s, str):
        return None
    s = "".join(ch for ch in s if ch.isprintable()).strip()
    return s[:n] or None


def node_id_of(packet: dict):
    n = packet.get("from")
    nid = packet.get("fromId") if isinstance(packet.get("fromId"), str) and NODE_ID_RE.match(packet.get("fromId")) else None
    if nid is None and isinstance(n, int) and not isinstance(n, bool) and 0 < n < 2**32 - 1:
        nid = f"!{n:08x}"
    return nid, (n if isinstance(n, int) and not isinstance(n, bool) else None)


def position_of(pos) -> tuple[float, float, float | None] | None:
    """(lat, lon, altitude) from a position dict, in either of the library's forms, or None for anything not believable."""
    if not isinstance(pos, dict):
        return None
    lat, lon = pos.get("latitude"), pos.get("longitude")
    if not (num(lat) and num(lon)):
        li, lo = pos.get("latitudeI"), pos.get("longitudeI")
        lat, lon = (li / 1e7, lo / 1e7) if num(li) and num(lo) else (None, None)
    if lat is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None  # 0,0 is what a node with no fix reports
    alt = pos.get("altitude")
    return float(lat), float(lon), float(alt) if num(alt) and -1000 <= alt <= 100000 else None


def metrics_of(raw: dict, keys: dict) -> dict:
    out = {}
    for src, name in keys.items():
        v = raw.get(src)
        lo, hi = LIMITS[name]
        if num(v) and lo <= v <= hi:
            out[name] = float(v)
    return out


def hops_of(packet: dict):
    """Relays a packet passed through, or None when the sender does not say (hopStart 0, older firmware)."""
    hl, hs = packet.get("hopLimit"), packet.get("hopStart")
    ok = lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 7
    return hs - hl if ok(hl) and ok(hs) and hs > 0 and hl <= hs else None


@dataclass
class Observation:
    node_id: str
    num: int | None
    ts: float
    portnum: str
    hops: int | None = None
    snr: float | None = None       # the last hop's signal: it describes the sender only when hops == 0
    rssi: int | None = None
    via_mqtt: bool = False
    user: dict | None = None       # name, short, hw, role, has_key
    position: tuple | None = None  # lat, lon, altitude
    telemetry: dict | None = None  # {"kind": "device" | "environment", "metrics": {...}}
    direct: bool = field(init=False, default=False)

    def __post_init__(self):
        # Heard with no relay in between AND as a real radio reception (an MQTT-relayed packet has no RSSI of its own).
        self.direct = self.hops == 0 and self.rssi is not None


def observe(packet: dict, my_num: int | None, now: float) -> Observation | None:
    """What one received packet tells us, or None if it is from ourselves, malformed, or has nothing we keep."""
    if not isinstance(packet, dict):
        return None
    nid, n = node_id_of(packet)
    if nid is None or (my_num is not None and n == my_num):
        return None
    dec = packet.get("decoded") if isinstance(packet.get("decoded"), dict) else {}
    portnum = str(dec.get("portnum") or "ENCRYPTED")[:40]
    snr, rssi = packet.get("rxSnr"), packet.get("rxRssi")
    obs = Observation(
        node_id=nid, num=n, ts=now, portnum=portnum, hops=hops_of(packet),
        snr=float(snr) if num(snr) and -40 <= snr <= 40 else None,
        rssi=int(rssi) if num(rssi) and -200 <= rssi < 0 else None,
        via_mqtt=bool(packet.get("viaMqtt")))
    pos = position_of(dec.get("position"))
    if pos:
        obs.position = pos
    user = dec.get("user")
    if isinstance(user, dict):
        obs.user = {"name": clean(user.get("longName")), "short": clean(user.get("shortName"), SHORT_MAX),
                    "hw": clean(str(user.get("hwModel")), 40) if user.get("hwModel") else None,
                    "role": clean(str(user.get("role")), 40) if user.get("role") else None, "has_key": bool(user.get("publicKey"))}
    tel = dec.get("telemetry")
    if isinstance(tel, dict):
        for kind, key, keys in (("device", "deviceMetrics", DEVICE_KEYS), ("environment", "environmentMetrics", ENVIRONMENT_KEYS)):
            if isinstance(tel.get(key), dict):
                m = metrics_of(tel[key], keys)
                if m:
                    obs.telemetry = {"kind": kind, "metrics": m}
                    break
    return obs


def _believable_time(t, now: float):
    """When a position was fixed, if the radio says so and it is a plausible moment in the past; else None."""
    return float(t) if num(t) and 1_000_000_000 < t <= now else None


def node_row(entry: dict, my_num: int | None, now: float, key: str | None = None) -> dict | None:
    """One entry of the radio's own node list (`key` is its key in that list, the node id), flattened. None if it has no usable id."""
    if not isinstance(entry, dict):
        return None
    u = entry.get("user") if isinstance(entry.get("user"), dict) else {}
    dm = entry.get("deviceMetrics") if isinstance(entry.get("deviceMetrics"), dict) else {}
    em = entry.get("environmentMetrics") if isinstance(entry.get("environmentMetrics"), dict) else {}
    n = entry.get("num") if isinstance(entry.get("num"), int) and not isinstance(entry.get("num"), bool) else None
    nid = u.get("id") if isinstance(u.get("id"), str) and NODE_ID_RE.match(u["id"]) else (f"!{n:08x}" if n is not None and 0 < n < 2**32 - 1 else None)
    if nid is None and isinstance(key, str) and NODE_ID_RE.match(key):
        nid = key
    if nid is None:
        return None
    last = entry.get("lastHeard")
    pos = position_of(entry.get("position"))
    metrics = {**metrics_of(dm, DEVICE_KEYS), **metrics_of(em, ENVIRONMENT_KEYS)}
    hops = entry.get("hopsAway")
    snr = entry.get("snr")
    return {
        "id": nid, "num": n, "name": clean(u.get("longName")), "short": clean(u.get("shortName"), SHORT_MAX),
        "hw": clean(str(u.get("hwModel")), 40) if u.get("hwModel") else None, "role": clean(str(u.get("role")), 40) if u.get("role") else None,
        "hops": hops if isinstance(hops, int) and not isinstance(hops, bool) and 0 <= hops <= 7 else None,
        "snr": float(snr) if num(snr) and -40 <= snr <= 40 else None,
        "last_heard": float(last) if num(last) and 1_000_000_000 < last < 4_300_000_000 else None,  # the radio sends 0 with no clock
        "via_mqtt": bool(entry.get("viaMqtt")), "favorite": bool(entry.get("isFavorite")), "has_key": bool(u.get("publicKey")),
        "lat": pos[0] if pos else None, "lon": pos[1] if pos else None, "alt": pos[2] if pos else None,
        "pos_time": _believable_time((entry.get("position") or {}).get("time") if isinstance(entry.get("position"), dict) else None, now),
        "is_self": (n is not None and n == my_num) or (my_num is not None and nid == f"!{my_num:08x}"), **{k: metrics.get(k) for k in LIMITS},
    }
