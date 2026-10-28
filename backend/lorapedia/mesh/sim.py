"""A made-up mesh, so the node list and the pages built on it can be seen and tested without a radio.

Feeds the same `Observation`s a real radio would produce, with realistic shapes: a few direct neighbours and some far ones, batteries
that drain, a solar node that recovers, a weather station, positions that mostly stay put and one that moves. Deterministic.
"""
import random

from .observe import Observation
from .service import MeshService

CENTER = (46.85, -121.76)

NODES = [  # id, long name, short, hardware, role, hops, base snr, lat/lon offset (degrees), battery start, has sensors
    ("!5d000001", "Demo Ridge Repeater", "RDGE", "RAK4631", "ROUTER", 0, 8.5, (0.020, 0.015), 100, False),
    ("!5d000002", "Demo Lodge", "LDGE", "HELTEC_V3", "CLIENT", 0, 5.0, (-0.004, 0.006), 72, False),
    ("!5d000003", "Demo Weather Station", "WX01", "TBEAM", "SENSOR", 1, 1.5, (0.031, -0.02), 88, True),
    ("!5d000004", "Demo Hiker Anna", "ANNA", "TLORA_V2_1_1P6", "CLIENT", 2, -4.0, (0.05, 0.04), 41, False),
    ("!5d000005", "Demo Trailhead", "TRLH", "RAK4631", "CLIENT_MUTE", 1, 3.0, (-0.03, 0.01), 95, False),
    ("!5d000006", "Demo Solar Relay", "SLR1", "HELTEC_V3", "ROUTER_LATE", 0, 6.0, (0.012, -0.03), 64, False),
    ("!5d000007", "Demo Far Peak", "PEAK", "TBEAM", "REPEATER", 3, -9.0, (0.09, 0.07), 100, False),
    ("!5d000008", "Demo Truck", "TRCK", "T_ECHO", "CLIENT", 1, 0.5, (-0.01, -0.02), 55, False),
]
DAY = 86400


def seed(service: MeshService, now: float, hours: int = 24, seed_value: int = 7) -> int:
    """Record `hours` of simulated activity ending at `now`. Returns the number of observations. Call `flush()` afterwards."""
    rng = random.Random(seed_value)
    events: list[Observation] = []
    start = now - hours * 3600
    for i, (nid, name, short, hw, role, hops, snr0, (dlat, dlon), batt0, sensors) in enumerate(NODES):
        num = int(nid[1:], 16)
        lat, lon = CENTER[0] + dlat, CENTER[1] + dlon
        t = start + rng.uniform(0, 600)
        step = 0
        while t < now - 30:
            direct = hops == 0
            snr = round(snr0 + rng.uniform(-2.5, 2.5), 1)
            common = dict(node_id=nid, num=num, ts=t, hops=hops, snr=snr, rssi=int(-95 + snr * 2 + rng.uniform(-6, 6)) if direct else int(-100 + snr))
            if step % 8 == 0:
                events.append(Observation(**common, portnum="NODEINFO_APP", user={"name": name, "short": short, "hw": hw, "role": role, "has_key": True}))
            if step % 6 == 1:
                moving = nid == "!5d000008"
                lat_t, lon_t = (lat + step * 0.0004, lon + step * 0.0003) if moving else (lat, lon)
                events.append(Observation(**common, portnum="POSITION_APP", position=(lat_t, lon_t, 1500.0 + i * 40)))
            if step % 3 == 2:
                frac = (t - start) / (now - start)
                drain = batt0 - 25 * frac if nid != "!5d000006" else min(100, batt0 + 30 * abs(0.5 - frac))
                events.append(Observation(**common, portnum="TELEMETRY_APP", telemetry={"kind": "device", "metrics": {
                    "battery": round(max(5, drain), 1), "voltage": round(3.3 + max(5, drain) / 100 * 0.9, 2),
                    "channel_util": round(rng.uniform(2, 14), 1), "air_util_tx": round(rng.uniform(0.2, 3), 1),
                    "uptime_s": float(int(t - start) + 86400 * (i + 1))}}))
            if sensors and step % 3 == 0:
                events.append(Observation(**common, portnum="TELEMETRY_APP", telemetry={"kind": "environment", "metrics": {
                    "temperature": round(8 + 6 * rng.random() + 4 * (1 - abs(0.5 - ((t - start) / (now - start))) * 2), 1),
                    "humidity": round(rng.uniform(40, 80), 1), "pressure": round(rng.uniform(1005, 1020), 1)}}))
            events.append(Observation(**common, portnum="TEXT_MESSAGE_APP" if step % 17 == 5 else "ROUTING_APP"))  # traffic that carries nothing we keep
            t += rng.uniform(240, 900)
            step += 1
    events.sort(key=lambda o: o.ts)
    for o in events:
        service.record(o)
    return len(events)
