"""Questions about the radio mesh itself ("how many nodes are around?", "which node has the lowest battery?"), answered from the mesh
database by a fixed menu of hand-written lookups.

No language model is involved, in either direction. A rule-based router decides whether a question is about the mesh; a handler then
reads mesh.db and writes the reply. So nothing a stranger puts in a node name can steer a model, nothing here can transmit, and a reply
only ever contains numbers read from the database (an answer we do not have is "I don't know", never a guess). A question the router
does not recognise goes on to the offline library as before: a wrong "no" costs a Wikipedia answer, a wrong "yes" would answer the
wrong question, so every rule needs mesh context (or a node that actually exists).

Replies use a node's own long or short name, never the label or note you added on the Nodes page: those are private.
"""
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .mesh.service import MeshService, distance_km

NAME_MAX = 24
LOW_BATTERY = 20              # percent; 101 means "on external power", which is not low
QUIET_MAX_S = 7 * 86400       # a node silent for longer than a week is gone, not quiet
LIST_MAX = 5
ID_RE = re.compile(r"![0-9a-f]{8}\b")
ROLES = ("router", "repeater", "client", "tracker", "sensor")
METRICS = {"battery": "battery", "voltage": "voltage", "temperature": "temperature", "humidity": "humidity",
           "channel use": "channel_util", "channel utilization": "channel_util", "channel utilisation": "channel_util"}
METRIC_WORD = "|".join(sorted(map(re.escape, METRICS), key=len, reverse=True))


@dataclass(frozen=True)
class Intent:
    name: str                       # summary | nodes | info | history | sensors | signal | quiet | busiest | activity | clock | self
    sort: str | None = None         # nodes: recent | battery | nearest | farthest
    role: str | None = None
    metric: str | None = None       # history: a key of METRICS
    target: str | None = None       # the node asked about: a name or an id
    hours: int | None = None
    sure: bool = True               # False: only claim the question when `target` is a node we know (else it is for Wikipedia)


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^\w!' ]", " ", text).split())


def _hours(t: str, default: int | None = None) -> int | None:
    if m := re.search(r"\b(\d{1,3})\s*(?:h|hr|hrs|hour|hours)\b", t):
        return max(1, min(int(m.group(1)), 168))
    if re.search(r"\b(?:an|the last|past|last) hour\b", t):
        return 1
    if re.search(r"\b(?:today|day|24 hours|yesterday)\b", t):
        return 24
    if re.search(r"\bweek\b", t):
        return 168
    return default


def _target(raw: str) -> str:
    raw = re.sub(r"\b(?:doing|now|today|lately|right now|please|at the moment)$", "", raw.strip()).strip(" '")
    return re.sub(r"^(?:the|node)\s+", "", raw).strip(" '")


_CLOCK = re.compile(r"^(?:what(?:'s| is) the (?:current )?(?:time|date|day)(?: is it)?(?: now| today| right now)?|what time is it(?: now| right now)?|"
                    r"what day is (?:it|today)(?: today)?|what(?:'s| is) today'?s date|(?:the )?(?:current )?(?:time|date)(?: please)?|tell me the (?:time|date))$")
_SELF = re.compile(r"^(?:(?:who|what) are you|what(?:'s| is) your name|who is this)$")
_NOT_A_NODE = re.compile(r"\bnode ?js\b")        # "node.js" is software
_SUMMARY = re.compile(r"\bhow(?:'s| is| are) (?:the |our )?mesh\b|\bmesh (?:status|summary|health|report|overview)\b|\b(?:status|summary|overview) of the mesh\b|"
                      r"\bhow many (?:nodes|neighbou?rs|stations|radios)(?: (?:are |do you )?(?:(?:there|you)\b )?(?:around|nearby|in range|on the mesh|in the mesh|heard|known|know|hear|see|have|out there|are there))?(?: right now| now)?$|"
                      r"\bhow many (?:nodes|neighbou?rs|stations|radios) (?:are |do you )?(?:around|nearby|in range|on the mesh|in the mesh|heard|know|hear|see|have|out there)\b|"
                      r"\bwho(?:'s| is) (?:around|nearby|on the mesh|out there|on the air)\b|\bwhat(?:'s| is) on the mesh\b|\b(?:nodes|stations) (?:around|nearby|in range)\b")
_NEAREST = re.compile(r"\b(?P<s>nearest|closest|farthest|furthest)\s+(?:node|(?P<r>router|repeater|client|tracker|sensor))s?\b")
_LOWBATT = re.compile(r"\b(?:lowest|low|weakest|worst|dying)\s+batter(?:y|ies)\b|\bbatter(?:y|ies)\s+(?:is |are )?(?:low|lowest)\b|\bwhich nodes? .*batter")
_RECENT = re.compile(r"\bwho (?:was |has been |did you )?(?:last |most recently |recently )?(?:heard|seen)\b|\b(?:recently|last) heard nodes?\b|\bnewest nodes?\b")
_ROLELIST = re.compile(r"\b(?P<r>router|repeater|tracker|sensor|client)s (?:nearby|around|in range|on the mesh|you (?:know|hear|see|have)|do you (?:know|hear|see|have)|are (?:there|around|nearby))\b")
_SENSORS = re.compile(r"\b(?:temperature|humidity|pressure|weather|how (?:hot|cold|warm|humid)|sensors?)\b")
_SENSOR_CONTEXT = re.compile(r"\b(?:outside|outdoors|out there|weather stations?|around here|locally|nearby|in the area|readings?|(?:on|in|from|across|around) (?:the |our |my )?(?:mesh|nodes)|nodes? (?:report|say|reports|says))\b")
_SENSOR_ASK = re.compile(r"\bsensors? (?:readings?|say|saying|report|reporting|data)\b|\b(?:what|how) (?:are|do|is) (?:the )?sensors\b")
_SENSOR_SHORT = re.compile(r"^(?:what(?:'s| is) the )?(?:temperature|weather|humidity)(?: like)?(?: now| today| here| right now)?$")
_SIGNAL = re.compile(r"\b(?:link quality|direct links?|best links?|weakest links?|strongest links?|signal (?:quality|report|strength) (?:of|on|in|for)? ?(?:the |our |my )?(?:mesh|neighbou?rs|nodes|links)|"
                     r"how well (?:do|can) (?:you|we) hear)\b")
_QUIET = re.compile(r"\b(?:quiet|silent|dark|offline|missing)\s+nodes\b|\bnodes? (?:that |which )?(?:have |has |are |went |gone |have gone |has gone )?(?:gone )?(?:quiet|silent|dark|offline|missing)\b|"
                    r"\bnodes? (?:that |which )?(?:haven't|hasn't|have not|has not|have stopped|stopped|aren't|are not) (?:been )?(?:heard|reporting|checking in|transmitting)\b|"
                    r"\bwho(?:'s| is| has) (?:gone )?(?:quiet|silent|offline)(?: lately| today| now| recently)?$")
_BUSIEST = re.compile(r"\b(?:busiest|quietest|peak|busy)\b.*\b(?:time|hour|times|hours|part of the day)\b|\bwhen is the (?:mesh|channel) (?:busiest|quiet|quietest|busy)\b")
_BUSY_CONTEXT = re.compile(r"\b(?:mesh|traffic|channel|packets?|airtime|messages)\b")
_ACTIVITY = re.compile(r"\bwhat(?:'s| is| has) (?:been )?(?:happening|going on|new|up)(?: lately| recently)? (?:on|in|with) (?:the )?mesh\b|\bmesh activity\b|\brecent (?:mesh )?activity\b|"
                       r"\bany(?:thing)? new (?:on|in) the mesh\b|\bhow (?:much )?(?:traffic|many packets)\b|\bpackets (?:heard|seen|today|this hour)\b|\bany new nodes\b|\bnew nodes\b")
_INFO_NODE = re.compile(r"(?:\babout|\binfo(?:rmation)? (?:on|about)|\bstatus (?:of|for)|\bwhere is|\bhow is|\bfind|\bshow me|\blook up|\bdetails (?:of|on|for)|\bis)\s+(?:the\s+)?node\s+(?:called\s+|named\s+)?(?P<n>.+)$|^node\s+(?:called\s+|named\s+)?(?P<n2>.+)$")
_HIST_OF = re.compile(rf"\b(?P<m>{METRIC_WORD})\s+(?:history\s+|trend\s+|trends\s+|over time\s+)?(?:of|on|for|at)\s+(?P<n>.+)$")
_HIST_POSS = re.compile(rf"(?:^|\b(?:is|has|did|how(?:'s| is)|what(?:'s| is)|show|check)\s+)(?:the\s+)?(?P<n>[^']+?)(?:'s|s') (?P<m>{METRIC_WORD})\b")


def route(question: str) -> Intent | None:
    """Is this a question about the mesh, and which lookup answers it? None: not ours, leave it to the library."""
    t = _norm(question)
    if not t:
        return None
    if _CLOCK.match(t):
        return Intent("clock")
    if _SELF.search(t):
        return Intent("self")
    if _NOT_A_NODE.search(t):
        return None
    ids = ID_RE.findall(t)
    metric_in = re.search(rf"\b({METRIC_WORD})\b", t)
    if ids:                                                       # an exact node id is unmistakable
        return Intent("history", metric=METRICS[metric_in.group(1)], target=ids[0]) if metric_in else Intent("info", target=ids[0])
    if m := _INFO_NODE.search(t):
        name = _target(m.group("n") or m.group("n2"))
        if name and len(name.split()) <= 3:
            return Intent("info", target=name)
    if m := _HIST_OF.search(t) or _HIST_POSS.search(t):
        name = _target(m.group("n"))
        if name and len(name.split()) <= 3:
            return Intent("history", metric=METRICS[m.group("m")], target=name, sure=False)
    if m := _NEAREST.search(t):
        s = m.group("s")
        return Intent("nodes", sort="nearest" if s in ("nearest", "closest") else "farthest", role=m.group("r"))
    if _LOWBATT.search(t):
        return Intent("nodes", sort="battery")
    if _RECENT.search(t):
        return Intent("nodes", sort="recent")
    if m := _ROLELIST.search(t):
        return Intent("nodes", sort="nearest", role=m.group("r"))
    if _SIGNAL.search(t):
        return Intent("signal", hours=_hours(t, 24))
    if _QUIET.search(t):
        return Intent("quiet", hours=_hours(t, 6))
    if _BUSIEST.search(t) and _BUSY_CONTEXT.search(t):
        return Intent("busiest")
    if _ACTIVITY.search(t):
        return Intent("activity", hours=_hours(t, 1))
    if _SUMMARY.search(t):
        return Intent("summary")
    if (_SENSORS.search(t) and _SENSOR_CONTEXT.search(t)) or _SENSOR_SHORT.match(t) or _SENSOR_ASK.search(t):
        return Intent("sensors", hours=_hours(t, 1))
    return None


# ---- wording -------------------------------------------------------------------------------------------------------------------
def clean_name(text) -> str:
    """A name chosen by a stranger, made safe to repeat: no control or invisible characters, one line, short."""
    text = "".join(ch for ch in str(text or "") if unicodedata.category(ch)[0] != "C" or ch == " ")
    return " ".join(text.split())[:NAME_MAX]


def _who(r: dict) -> str:
    return clean_name(r.get("name")) or clean_name(r.get("short")) or r["id"]    # never the private label


def _ago(secs: float | None) -> str:
    if secs is None:
        return "never"
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{round(secs / 60)} min ago"
    if secs < 2 * 86400:
        return f"{round(secs / 3600)} h ago"
    return f"{round(secs / 86400)} d ago"


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


class MeshAsker:
    def __init__(self, mesh: MeshService, settings: Callable[[], Settings], clock: Callable[[], float] = time.time) -> None:
        self.mesh, self._settings, self._now = mesh, settings, clock

    # ---- entry ----------------------------------------------------------------------------------------------------------------
    def answer(self, question: str) -> str | None:
        """The reply to a question about the mesh, or None when the question is not about the mesh (or the feature is off)."""
        if not self._settings().mesh_questions:
            return None
        intent = route(question)
        if intent is None:
            return None
        rows = self.mesh.rows()
        if intent.name in ("info", "history"):
            node, options = self._find(rows, intent.target or "")
            if node is None:
                if options:
                    return "More than one match: " + ", ".join(_who(o) for o in options[:4]) + ". Be more specific."
                if not intent.sure:
                    return None
                return f"I have no node matching '{clean_name(intent.target)}'."
            return self._info(rows, node) if intent.name == "info" else self._history(node, intent.metric or "battery")
        return getattr(self, "_" + intent.name)(rows, intent)

    @staticmethod
    def _find(rows: list[dict], query: str) -> tuple[dict | None, list[dict]]:
        """A node by id, exact name or short name, else by a part of its name. Several matches: (None, the matches)."""
        q = query.strip().lower()
        others = [r for r in rows if not r["us"]] or rows
        if not q:
            return None, []
        for same in (lambda r: r["id"] == q, lambda r: q in ((r.get("name") or "").lower(), (r.get("short") or "").lower())):
            hits = [r for r in others if same(r)]
            if len(hits) == 1:
                return hits[0], []
            if hits:
                return None, hits
        hits = [r for r in others if q in (r.get("name") or "").lower()]
        return (hits[0], []) if len(hits) == 1 else (None, hits)

    def _units(self) -> tuple[str, str]:
        s = self._settings()
        return s.temperature_unit, s.distance_unit

    def _temp(self, c: float) -> str:
        return f"{c * 9 / 5 + 32:.0f}F" if self._units()[0] == "F" else f"{c:.0f}C"

    def _dist(self, km: float) -> str:
        if self._units()[1] == "mi":
            mi = km * 0.621371
            return f"{mi:.1f} mi" if mi >= 0.1 else f"{mi * 5280:.0f} ft"
        return f"{km:.1f} km" if km >= 0.1 else f"{km * 1000:.0f} m"

    @staticmethod
    def _us(rows: list[dict]) -> dict | None:
        return next((r for r in rows if r["us"]), None)

    # ---- the lookups --------------------------------------------------------------------------------------------------------------
    def _summary(self, rows: list[dict], intent: Intent) -> str:
        others = [r for r in rows if not r["us"]]
        if not others:
            return "I have not heard any other node yet."
        s = self.mesh.summary(rows)
        out = [f"Mesh: {len(others)} nodes known. Heard {s['heard_15m']} in 15 min, {s['heard_1h']} in 1 h, {s['heard_24h']} in 24 h; {s['direct']} direct."]
        if s["avg_channel_util"] is not None:
            out.append(f"Channel use {s['avg_channel_util']:.1f}%.")
        hour = int(self._now() // 3600)
        packets = sum(n for _, n in self.mesh.store.packet_hours(hour))
        if packets:
            out.append(f"{packets} packets this hour.")
        low = sorted((r for r in others if _num(r["battery"]) and r["battery"] <= LOW_BATTERY and r["age_s"] is not None and r["age_s"] <= 86400),
                     key=lambda r: r["battery"])
        if low:
            out.append("Low battery: " + ", ".join(f"{_who(r)} {r['battery']:.0f}%" for r in low[:3]) + ".")
        sensors = self._sensor_overall(1, short=True)
        if sensors:
            out.append("Sensors: " + sensors + ".")
        return " ".join(out)

    def _nodes(self, rows: list[dict], intent: Intent) -> str:
        others = [r for r in rows if not r["us"]]
        sort, role = intent.sort or "recent", intent.role
        if role:
            others = [r for r in others if role in (r.get("role") or "").lower()]  # "router" also matches ROUTER_LATE
            if not others:
                return f"I know of no {role}s."
        if not others:
            return "I have not heard any other node yet."
        us = self._us(rows)
        here = (us["lat"], us["lon"]) if us and _num(us.get("lat")) and _num(us.get("lon")) else None
        if sort == "battery":
            pick = sorted((r for r in others if _num(r["battery"]) and r["battery"] <= 100), key=lambda r: r["battery"])
            head, detail = "Lowest battery", lambda r: f"{r['battery']:.0f}% ({_ago(r['age_s'])})"
        elif sort in ("nearest", "farthest") and here:
            pick = sorted((r for r in others if _num(r["lat"]) and _num(r["lon"])), key=lambda r: distance_km(*here, r["lat"], r["lon"]),
                          reverse=sort == "farthest")
            head, detail = ("Nearest" if sort == "nearest" else "Farthest"), lambda r: self._dist(distance_km(*here, r["lat"], r["lon"]))
        elif sort in ("nearest", "farthest"):
            pick = sorted((r for r in others if isinstance(r["hops"], int)), key=lambda r: (r["hops"], r["age_s"] or 0), reverse=sort == "farthest")
            head = "Fewest hops (my own position is unknown)" if sort == "nearest" else "Most hops away"
            detail = lambda r: f"{r['hops']} hop{'s' if r['hops'] != 1 else ''}"
        else:
            pick = [r for r in others if r["age_s"] is not None]
            head, detail = "Heard most recently", lambda r: _ago(r["age_s"])
        if not pick:
            return "No node has reported that yet."
        return head + ": " + "; ".join(f"{_who(r)} {detail(r)}" for r in pick[:LIST_MAX]) + "."

    def _info(self, rows: list[dict], r: dict) -> str:
        bits = [f"{_who(r)} ({r['id']})" + (f", {r['hw']}" if r.get("hw") else "")]
        bits.append(f"heard {_ago(r['age_s'])}" if r["age_s"] is not None else "never heard directly")
        if isinstance(r["hops"], int):
            bits.append(f"{r['hops']} hop{'s' if r['hops'] != 1 else ''} away")
        if _num(r["snr"]):
            bits.append(f"SNR {r['snr']:.1f} dB")
        us = self._us(rows)
        if us and all(_num(x) for x in (us.get("lat"), us.get("lon"), r["lat"], r["lon"])):
            bits.append(f"{self._dist(distance_km(us['lat'], us['lon'], r['lat'], r['lon']))} from me")
        elif _num(r["lat"]):
            bits.append("it has a known position")
        if _num(r["battery"]):
            bits.append("external power" if r["battery"] > 100 else f"battery {r['battery']:.0f}%" + (f" ({r['voltage']:.2f} V)" if _num(r["voltage"]) else ""))
        elif _num(r["voltage"]):
            bits.append(f"{r['voltage']:.2f} V")
        if _num(r["temperature"]):
            bits.append(self._temp(r["temperature"]) + (f", {r['humidity']:.0f}% humidity" if _num(r["humidity"]) else ""))
        if _num(r["channel_util"]):
            bits.append(f"channel use {r['channel_util']:.1f}%")
        if not any(_num(r[k]) for k in ("battery", "voltage", "temperature", "channel_util")):
            bits.append("no telemetry recorded for it yet")
        return "; ".join(bits) + "."

    def _history(self, r: dict, metric: str) -> str:
        label = {"channel_util": "channel use"}.get(metric, metric)
        since = self._now() - 86400
        pts = [(t["ts"], t[metric]) for t in self.mesh.store.telemetry(r["id"], since) if _num(t.get(metric))]
        name = _who(r)
        if not pts:
            return f"No {label} readings for {name} in the last 24 h."
        fmt = (lambda v: self._temp(v)) if metric == "temperature" else {"battery": lambda v: f"{v:.0f}%", "voltage": lambda v: f"{v:.2f} V",
                                                                       "humidity": lambda v: f"{v:.0f}%", "channel_util": lambda v: f"{v:.1f}%"}[metric]
        (t0, v0), (t1, v1) = pts[0], pts[-1]
        span_h = (t1 - t0) / 3600
        if len(pts) < 2 or span_h < 0.25:
            return f"{name} {label}: {fmt(v1)} ({_ago(self._now() - t1)}); only {len(pts)} reading in 24 h, so no trend yet."
        lo, hi, delta = min(v for _, v in pts), max(v for _, v in pts), v1 - v0
        out = f"{name} {label}: {fmt(v0)} to {fmt(v1)} over {span_h:.0f} h ({len(pts)} readings, low {fmt(lo)}, high {fmt(hi)})"
        steady = abs(delta) < {"battery": 0.5, "humidity": 0.5, "channel_util": 0.5, "voltage": 0.03, "temperature": 0.3}[metric]
        out += "; steady" if steady else f"; {'up' if delta > 0 else 'down'}"
        return out + "."

    def _sensor_overall(self, hours: int, short: bool = False) -> str:
        per = self.mesh.store.sensor_averages(self._now() - hours * 3600)
        if not per:
            return ""
        avg = lambda key: (sum(p[key] for p in per if _num(p[key])) / n) if (n := sum(1 for p in per if _num(p[key]))) else None
        t, h, p = avg("temperature"), avg("humidity"), avg("pressure")
        bits = [self._temp(t) if t is not None else None, f"{h:.0f}%" if short and h is not None else f"{h:.0f}% humidity" if h is not None else None,
                f"{p:.0f} hPa" if p is not None else None]
        if short:
            return f"{len(per)} node{'s' if len(per) != 1 else ''}, avg " + ", ".join(b for b in bits if b)
        return f"{len(per)} node{'s' if len(per) != 1 else ''} report" + ("s" if len(per) == 1 else "") + ", average " + ", ".join(b for b in bits if b)

    def _sensors(self, rows: list[dict], intent: Intent) -> str:
        hours = intent.hours or 1
        per = self.mesh.store.sensor_averages(self._now() - hours * 3600)
        if not per:
            return f"No node has reported temperature or humidity in the last {hours} h."
        names = {r["id"]: _who(r) for r in rows}
        each = "; ".join(f"{names.get(p['node_id'], p['node_id'])} " + ", ".join(
            b for b in (self._temp(p["temperature"]) if _num(p["temperature"]) else "", f"{p['humidity']:.0f}%" if _num(p["humidity"]) else "") if b)
            for p in per[:3])
        return f"Sensors, last {hours} h: {self._sensor_overall(hours)}. {each}."

    def _signal(self, rows: list[dict], intent: Intent) -> str:
        hours = intent.hours or 24
        links = [l for l in self.mesh.store.links_summary(int((self._now() - hours * 3600) // 3600)) if _num(l["snr_avg"])]
        if not links:
            return f"No node has been heard directly in the last {hours} h."
        by_id = {r["id"]: r for r in rows}
        us = self._us(rows)
        links.sort(key=lambda l: l["snr_avg"], reverse=True)

        def show(l):
            r = by_id.get(l["node_id"], {"id": l["node_id"]})
            far = ""
            if us and all(_num(x) for x in (us.get("lat"), us.get("lon"), r.get("lat"), r.get("lon"))):
                far = f" ({self._dist(distance_km(us['lat'], us['lon'], r['lat'], r['lon']))})"
            return f"{_who(r)} {l['snr_avg']:+.1f} dB{far}"
        out = f"Direct links, last {hours} h: {len(links)} node{'s' if len(links) != 1 else ''}. Best: " + "; ".join(show(l) for l in links[:2])
        if len(links) > 2:
            out += ". Weakest: " + "; ".join(show(l) for l in links[2:][::-1][:2])
        return out + "."

    def _quiet(self, rows: list[dict], intent: Intent) -> str:
        hours = intent.hours or 6
        quiet = sorted((r for r in rows if not r["us"] and r["age_s"] is not None and hours * 3600 <= r["age_s"] <= QUIET_MAX_S), key=lambda r: r["age_s"])
        if not quiet:
            return f"No node heard in the last week has been quiet for {hours}+ h."
        out = f"Quiet for {hours}+ h (heard within a week): " + "; ".join(f"{_who(r)} {_ago(r['age_s']).replace(' ago', '')}" for r in quiet[:LIST_MAX])
        return out + (f" (+{len(quiet) - LIST_MAX} more)." if len(quiet) > LIST_MAX else ".")

    def _busiest(self, rows: list[dict], intent: Intent) -> str:
        now_h = int(self._now() // 3600)
        data = dict(self.mesh.store.packet_hours(now_h - 7 * 24))
        if len(data) < 6:
            return "Not enough traffic has been counted yet to say."
        sums, seen = defaultdict(int), defaultdict(int)
        for h in range(min(data), max(data) + 1):                      # hours with no packet count as zero, not as missing
            hod = time.localtime(h * 3600).tm_hour
            sums[hod] += data.get(h, 0)
            seen[hod] += 1
        avg = {hod: sums[hod] / seen[hod] for hod in sums}
        best, quiet = max(avg, key=avg.get), min(avg, key=avg.get)
        hl = lambda x: f"{x % 12 or 12} {'AM' if x < 12 else 'PM'}"
        span = max(data) - min(data) + 1
        return (f"Busiest hour {hl(best)} (about {avg[best]:.0f} packets an hour), quietest {hl(quiet)} ({avg[quiet]:.0f}). "
                f"Based on {span} hour{'s' if span != 1 else ''} of data.")

    def _activity(self, rows: list[dict], intent: Intent) -> str:
        hours = intent.hours or 1
        now = self._now()
        since = now - hours * 3600
        others = [r for r in rows if not r["us"]]
        packets = sum(n for _, n in self.mesh.store.packet_hours(int(since // 3600)))
        heard = sum(1 for r in others if r["age_s"] is not None and r["age_s"] <= hours * 3600)
        out = f"Last {hours} h: {packets} packets counted; {heard} node{'s' if heard != 1 else ''} heard"
        readings = len(self.mesh.store.readings(since, limit=1000))
        if readings:
            out += f"; {readings}{'+' if readings >= 1000 else ''} telemetry readings"
        earliest = min((r["first_seen"] for r in others if r.get("first_seen")), default=None)
        if earliest is not None and earliest <= since:                  # else we were not recording the whole window: everything looks new
            new = [r for r in others if r.get("first_seen") and r["first_seen"] >= since and r["age_s"] is not None]
            if new:
                out += "; new: " + ", ".join(_who(r) for r in new[:3]) + (f" (+{len(new) - 3})" if len(new) > 3 else "")
        return out + "."

    def _self(self, rows: list[dict], intent: Intent) -> str:
        us = self._us(rows)
        name = _who(us) if us else "a LoRaPedia node"
        return f"I am {name}: an offline Wikipedia on this mesh. Send me a question by DM; ?help lists my commands."

    def _clock(self, rows: list[dict], intent: Intent) -> str:
        return time.strftime("It is %A %d %B %Y, %H:%M %Z.", time.localtime(self._now()))
