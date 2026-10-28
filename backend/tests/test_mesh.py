"""What the radio hears of the mesh: parsing, the database, the service, the radio hook, and the API."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pubsub import pub

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.mesh import observe as O
from lorapedia.mesh import sim
from lorapedia.mesh.service import DIRECT_WINDOW_S, MeshService, distance_km
from lorapedia.mesh.store import MeshStore, NoteError
from lorapedia.radio.meshtastic_radio import ConnectionSpec, MeshtasticRadio

NOW = 1_800_000_000.0
BOB = {"from": 0xA1B2C3D4, "fromId": "!a1b2c3d4"}


def pk(portnum="TELEMETRY_APP", **decoded_and_more):
    extra = {k: decoded_and_more.pop(k) for k in list(decoded_and_more) if k in ("rxSnr", "rxRssi", "hopStart", "hopLimit", "viaMqtt", "from", "fromId")}
    return {**BOB, "rxSnr": 5.5, "rxRssi": -90, "hopStart": 3, "hopLimit": 3, **extra, "decoded": {"portnum": portnum, **decoded_and_more}}


# ---- parsing -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pos,expected", [
    ({"latitude": 46.85, "longitude": -121.76, "altitude": 1500}, (46.85, -121.76, 1500.0)),
    ({"latitudeI": 468500000, "longitudeI": -1217600000}, (46.85, -121.76, None)),
    ({"latitude": 0, "longitude": 0}, None),                      # what a node with no fix reports
    ({"latitude": 91, "longitude": 10}, None), ({"latitude": 10, "longitude": 181}, None),
    ({"latitude": float("nan"), "longitude": 5}, None), ({"latitude": "46", "longitude": "7"}, None),
    ({"latitude": 46.85, "longitude": -121.76, "altitude": 10**9}, (46.85, -121.76, None)),  # a nonsense altitude is dropped, not the fix
    ({}, None), (None, None), ("x", None)])
def test_positions_are_believed_only_when_they_are_possible(pos, expected):
    assert O.position_of(pos) == expected


def test_telemetry_values_outside_what_a_sensor_can_report_are_dropped():
    o = O.observe(pk(telemetry={"deviceMetrics": {"batteryLevel": 150, "voltage": 4.1, "channelUtilization": 7.5, "uptimeSeconds": 100},
                                "environmentMetrics": {"temperature": 99}}), None, NOW)
    assert o.telemetry == {"kind": "device", "metrics": {"voltage": 4.1, "channel_util": 7.5, "uptime_s": 100.0}}
    env = O.observe(pk(telemetry={"environmentMetrics": {"temperature": 21.5, "relativeHumidity": 140, "barometricPressure": 1012}}), None, NOW)
    assert env.telemetry["kind"] == "environment" and env.telemetry["metrics"] == {"temperature": 21.5, "pressure": 1012.0}
    assert O.observe(pk(telemetry={"deviceMetrics": {"batteryLevel": True, "voltage": float("inf")}}), None, NOW).telemetry is None


def test_names_chosen_by_strangers_are_cleaned_and_capped():
    o = O.observe(pk("NODEINFO_APP", user={"longName": "Evil\x00\x1b[31m" + "x" * 100, "shortName": "A\nB" + "z" * 20, "hwModel": "TBEAM", "role": "ROUTER"}), None, NOW)
    assert "\x00" not in o.user["name"] and "\x1b" not in o.user["name"] and len(o.user["name"]) <= 40
    assert "\n" not in o.user["short"] and len(o.user["short"]) <= 8 and o.user["hw"] == "TBEAM"


def test_hops_and_the_meaning_of_direct():
    assert O.observe(pk(hopStart=3, hopLimit=3), None, NOW).hops == 0 and O.observe(pk(hopStart=3, hopLimit=3), None, NOW).direct
    assert O.observe(pk(hopStart=3, hopLimit=1), None, NOW).hops == 2 and not O.observe(pk(hopStart=3, hopLimit=1), None, NOW).direct
    assert O.observe(pk(hopStart=0, hopLimit=3), None, NOW).hops is None        # older firmware does not say
    assert O.observe(pk(hopStart=3, hopLimit=7), None, NOW).hops is None        # more remaining than started: nonsense
    assert not O.observe(pk(hopStart=3, hopLimit=3, rxRssi=0), None, NOW).direct  # no real reception (relayed in over MQTT)


def test_signal_values_out_of_range_are_ignored():
    o = O.observe(pk(rxSnr=99, rxRssi=50), None, NOW)
    assert o.snr is None and o.rssi is None and not o.direct


@pytest.mark.parametrize("packet", [None, [], "x", {}, {"from": None}, {"from": True}, {"fromId": "bob"}, {"from": 0}, {"from": 2**32 - 1}, {"from": "5"}])
def test_malformed_packets_are_ignored_not_fatal(packet):
    assert O.observe(packet, None, NOW) is None


def test_our_own_packets_are_not_recorded():
    assert O.observe(pk(), 0xA1B2C3D4, NOW) is None and O.observe(pk(), 0x11111111, NOW) is not None


def test_a_node_list_entry_is_flattened_and_checked():
    r = O.node_row({"num": 0xA1B2C3D4, "user": {"id": "!a1b2c3d4", "longName": "Bob", "shortName": "BOB", "hwModel": "TBEAM", "publicKey": "abc"},
                    "lastHeard": NOW - 60, "snr": 4.5, "hopsAway": 1, "position": {"latitude": 46.9, "longitude": -121.7},
                    "deviceMetrics": {"batteryLevel": 80, "voltage": 3.9}, "viaMqtt": True}, 0x11111111, NOW)
    assert (r["id"], r["name"], r["hops"], r["snr"], r["battery"], r["lat"], r["via_mqtt"], r["has_key"], r["is_self"]) == \
           ("!a1b2c3d4", "Bob", 1, 4.5, 80.0, 46.9, True, True, False)
    assert O.node_row({"user": {}}, None, NOW) is None and O.node_row("x", None, NOW) is None
    assert O.node_row({"user": {}}, None, NOW, key="!00000001")["id"] == "!00000001"
    assert O.node_row({"num": 5, "lastHeard": 0}, 5, NOW)["last_heard"] is None  # a radio with no clock sends 0
    assert O.node_row({"num": 5}, 5, NOW)["is_self"]


# ---- the database ------------------------------------------------------------------------------------------------------


def write(store, **kw):
    base = dict(nodes={}, packets={}, talkers={}, links={}, hops={}, telemetry=[], positions=[])
    store.write(**{**base, **kw})


def test_an_update_keeps_what_it_does_not_mention_and_last_seen_only_grows():
    s = MeshStore()
    write(s, nodes={"!a1b2c3d4": {"first_seen": 100.0, "last_seen": 500.0, "name": "Bob", "battery": 80.0, "lat": 1.0, "lon": 2.0}})
    write(s, nodes={"!a1b2c3d4": {"first_seen": 300.0, "last_seen": 400.0, "hops": 2}})   # the radio's list dropped name, battery, position
    n = s.node("!a1b2c3d4")
    assert (n["name"], n["battery"], n["lat"], n["hops"]) == ("Bob", 80.0, 1.0, 2)
    assert n["last_seen"] == 500.0 and n["first_seen"] == 100.0   # never moves back


def test_counters_add_up_across_flushes_within_an_hour():
    s = MeshStore()
    write(s, packets={(10, "TELEMETRY_APP"): 2}, talkers={(10, "!a1b2c3d4"): 2}, hops={(10, 1): 1}, links={(10, "!a1b2c3d4"): [1, 5.0, 5.0, 5.0, -90, -90, -90]})
    write(s, packets={(10, "TELEMETRY_APP"): 3}, talkers={(10, "!a1b2c3d4"): 1}, hops={(10, 1): 4}, links={(10, "!a1b2c3d4"): [2, 4.0, 1.0, 3.0, -190, -100, -90]})
    c = s._db.execute
    assert c("SELECT n FROM packets").fetchone()[0] == 5 and c("SELECT n FROM talkers").fetchone()[0] == 3 and c("SELECT n FROM hops").fetchone()[0] == 5
    link = dict(c("SELECT * FROM links").fetchone())
    assert (link["n"], link["snr_sum"], link["snr_min"], link["snr_max"], link["rssi_min"]) == (3, 9.0, 1.0, 5.0, -100)


def test_notes_are_validated_cleaned_and_removed_when_emptied():
    s = MeshStore()
    write(s, nodes={"!a1b2c3d4": {"last_seen": 5.0}})
    assert s.set_note("!a1b2c3d4", "  Lodge \x00 repeater ", "line one\r\nline two")["label"] == "Lodge repeater"
    assert s.node("!a1b2c3d4")["label"] == "Lodge repeater" and s.node("!a1b2c3d4")["note"] == "line one\nline two"
    for args in (("bad id", "x", "y"), ("!a1b2c3d4", "x" * 41, None), ("!a1b2c3d4", None, "x" * 501), ("!a1b2c3d4", 5, None), ("!A1B2C3D4", "x", None)):
        with pytest.raises(NoteError):
            s.set_note(*args)
    s.set_note("!a1b2c3d4", "  ", "")
    assert s.node("!a1b2c3d4")["label"] is None  # an empty label and note remove the entry


def test_pruning_applies_the_window_to_each_dataset_and_keeps_notes():
    s = MeshStore()
    old, new = NOW - 40 * 86400, NOW - 3600
    write(s, nodes={"!00000001": {"last_seen": NOW - 100 * 86400, "first_seen": NOW - 200 * 86400}, "!00000002": {"last_seen": new, "first_seen": new},
                    "!00000003": {"last_seen": 0, "first_seen": new}},
          packets={(int(old // 3600), "X"): 1, (int(new // 3600), "X"): 1}, telemetry=[{"ts": old, "node_id": "!00000002", "kind": "device"},
                                                                                       {"ts": new, "node_id": "!00000002", "kind": "device"}],
          positions=[{"node_id": "!00000002", "ts": old, "lat": 1.0, "lon": 1.0}, {"node_id": "!00000002", "ts": NOW - 100 * 86400, "lat": 1.0, "lon": 1.0}])
    s.set_note("!00000001", "kept", None)
    d = s.prune(NOW, 30)
    assert d["nodes"] == 1 and d["telemetry"] == 1 and d["packets"] == 1
    assert d["positions"] == 1  # trails live three times as long: the 40-day-old point stays, the 100-day-old one goes
    assert {n["node_id"] for n in s.nodes()} == {"!00000002", "!00000003"}  # a node we only know from the radio's list (last_seen 0) is kept
    assert s._db.execute("SELECT label FROM node_notes").fetchone()[0] == "kept"


# ---- the service -------------------------------------------------------------------------------------------------------


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def svc(settings=None, clock=None, radio_nodes=(), me="!00000009"):
    s = settings or Settings()
    c = clock or Clock()
    return MeshService(MeshStore(), lambda: s, clock=c, radio_nodes=lambda: list(radio_nodes), my_id=lambda: me), c


async def hear(service, packet, my_num=None):
    service.on_packet(packet, my_num)
    await service.flush()


async def test_a_heard_node_is_remembered_with_everything_it_said():
    service, clock = svc()
    await hear(service, pk("NODEINFO_APP", user={"longName": "Bob", "shortName": "BOB", "hwModel": "TBEAM", "role": "ROUTER"}))
    await hear(service, pk("POSITION_APP", position={"latitude": 46.85, "longitude": -121.76, "altitude": 1500}))
    await hear(service, pk(telemetry={"deviceMetrics": {"batteryLevel": 71, "voltage": 3.8}}))
    (n,) = service.rows()
    assert (n["id"], n["name"], n["hw"], n["role"], n["lat"], n["battery"], n["voltage"], n["last_heard"], n["remembered"], n["us"]) == \
           ("!a1b2c3d4", "Bob", "TBEAM", "ROUTER", 46.85, 71.0, 3.8, NOW, True, False)
    assert n["pos_source"] == "packet" and n["display"] == "Bob"
    assert service.store.telemetry("!a1b2c3d4")[0]["battery"] == 71.0 and len(service.store.positions("!a1b2c3d4")) == 1


async def test_a_node_heard_with_no_relay_counts_as_zero_hops_for_a_while_whatever_the_list_says():
    service, clock = svc()
    await hear(service, pk(hopStart=3, hopLimit=3))
    assert service.rows()[0]["hops"] == 0 and service.rows()[0]["hops_measured"]
    clock.now += DIRECT_WINDOW_S + 1
    await hear(service, pk(hopStart=3, hopLimit=1))  # now it arrives through two relays
    row = service.rows()[0]
    assert row["hops"] == 2 and not row["hops_measured"]


async def test_the_signal_of_a_relayed_packet_is_not_counted_as_the_senders():
    service, _ = svc()
    await hear(service, pk(hopStart=3, hopLimit=1, rxSnr=-8.0, rxRssi=-110))
    assert service.store._db.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0
    await hear(service, pk(hopStart=3, hopLimit=3, rxSnr=6.0, rxRssi=-80))
    assert service.store._db.execute("SELECT snr_min, snr_max FROM links").fetchone()[:] == (6.0, 6.0)


async def test_a_telemetry_broadcast_delivered_twice_is_stored_once_a_minute_per_kind():
    service, clock = svc()
    t = pk(telemetry={"deviceMetrics": {"batteryLevel": 50}})
    await hear(service, t)
    clock.now += 5
    await hear(service, t)
    clock.now += 70
    await hear(service, t)
    assert len(service.store.telemetry("!a1b2c3d4")) == 2


async def test_a_trail_point_is_added_only_when_the_node_has_moved_fifteen_metres():
    service, clock = svc()
    here = {"latitude": 46.85, "longitude": -121.76}
    await hear(service, pk("POSITION_APP", position=here))
    await hear(service, pk("POSITION_APP", position={"latitude": 46.85005, "longitude": -121.76}))   # about 5 m
    await hear(service, pk("POSITION_APP", position={"latitude": 46.8505, "longitude": -121.76}))    # about 55 m
    assert len(service.store.positions("!a1b2c3d4")) == 2
    assert distance_km(46.85, -121.76, 46.8505, -121.76) == pytest.approx(0.0556, abs=0.002)


async def test_nothing_is_recorded_when_recording_is_off_and_reading_still_works():
    service, _ = svc(Settings(mesh_record=False))
    await hear(service, pk())
    assert service.rows() == [] and not service.recording
    assert service.import_radio_nodes([{"id": "!a1b2c3d4"}]) == 0


async def test_the_radios_node_list_adds_names_and_marks_who_is_live_and_who_is_us():
    listed = [{"id": "!a1b2c3d4", "name": "Bob", "short": "BOB", "hops": 1, "battery": 60.0, "last_heard": NOW - 30, "lat": None},
              {"id": "!00000009", "name": "Us", "short": "US", "hops": None, "last_heard": NOW - 5}]
    service, _ = svc(radio_nodes=listed)
    service.import_radio_nodes()
    await service.flush()
    rows = {r["id"]: r for r in service.rows()}
    assert rows["!a1b2c3d4"]["live"] and rows["!a1b2c3d4"]["name"] == "Bob" and rows["!00000009"]["us"] and rows["!00000009"]["last_heard"] == NOW - 5
    listed.clear()  # the radio forgets everyone: we do not
    rows = {r["id"]: r for r in service.rows()}
    assert rows["!a1b2c3d4"]["remembered"] and not rows["!a1b2c3d4"]["live"] and rows["!a1b2c3d4"]["name"] == "Bob"


async def test_what_a_packet_just_said_beats_the_radios_older_list():
    service, _ = svc(radio_nodes=[{"id": "!a1b2c3d4", "battery": 20.0, "last_heard": NOW - 500}])
    service.on_packet(pk(telemetry={"deviceMetrics": {"batteryLevel": 77}}), None)
    service.import_radio_nodes()
    await service.flush()
    assert service.rows()[0]["battery"] == 77.0 and service.rows()[0]["last_heard"] == NOW


async def test_a_radio_stamp_from_the_future_is_not_believed():
    service, _ = svc(radio_nodes=[{"id": "!a1b2c3d4", "last_heard": NOW + 10 * 86400}])
    service.import_radio_nodes()
    await service.flush()
    assert service.rows()[0]["last_heard"] is None  # known, but never actually heard


# ---- the simulated mesh --------------------------------------------------------------------------------------------------


async def demo_service():
    service, clock = svc()
    sim.seed(service, clock())
    await service.flush()
    return service


async def test_the_simulated_mesh_is_deterministic_and_has_every_kind_of_data():
    a, b = await demo_service(), await demo_service()
    assert [(r["id"], r["battery"], r["lat"]) for r in a.rows()] == [(r["id"], r["battery"], r["lat"]) for r in b.rows()]
    s = a.summary()
    assert s["nodes_total"] == len(sim.NODES) and s["direct"] >= 3 and s["with_position"] == len(sim.NODES) and s["with_telemetry"] == len(sim.NODES)
    assert any(r["temperature"] is not None for r in a.rows())  # the weather station
    mover = a.store.positions("!5d000008")
    assert len(mover) > 3  # the truck moves, so its trail grows
    stay = a.store.positions("!5d000001")
    assert len(stay) == 1
    assert len(a.store.telemetry("!5d000001")) > 10


async def test_the_summary_counts_by_recency_and_ignores_us():
    service = await demo_service()
    service._clock.now += 4 * 3600  # past the three-hour window in which "heard with no relay" still counts as direct
    s = service.summary()
    assert s["heard_15m"] == 0 and s["heard_1h"] == 0 and s["heard_24h"] == len(sim.NODES) and s["direct"] == 0
    assert sum(s["roles"].values()) == len(sim.NODES) and s["avg_channel_util"] is None


# ---- the radio hook ------------------------------------------------------------------------------------------------------


class Iface:
    def __init__(self):
        self.myInfo = SimpleNamespace(my_node_num=0x1234ABCD)
        self.nodes = {}

    def getMyNodeInfo(self):
        return {"user": {"id": "!1234abcd"}}

    def close(self):
        pass


def publish(topic, packet, iface):
    t = threading.Thread(target=lambda: pub.sendMessage(topic, packet=packet, interface=iface))
    t.start()
    t.join()


async def test_every_packet_the_radio_receives_reaches_the_mesh_hook_on_the_event_loop():
    got, loop_thread = [], threading.get_ident()
    iface = Iface()

    def on_mesh(packet, my_num):
        got.append((packet["decoded"]["portnum"], my_num, threading.get_ident() == loop_thread))

    async def on_packet(pkt, meta):
        pass

    radio = MeshtasticRadio(ConnectionSpec("serial", "/dev/x"), on_packet=on_packet, on_mesh=on_mesh, factory=lambda spec: iface)
    await radio.connect()
    publish("meshtastic.receive.position", {**BOB, "decoded": {"portnum": "POSITION_APP"}}, iface)
    publish("meshtastic.receive.text", {**BOB, "to": 0x1234ABCD, "id": 9, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hi"}}, iface)
    for _ in range(100):
        if len(got) >= 2:
            break
        await asyncio.sleep(0.01)
    assert sorted(got) == [("POSITION_APP", 0x1234ABCD, True), ("TEXT_MESSAGE_APP", 0x1234ABCD, True)]
    publish("meshtastic.receive.position", {**BOB, "decoded": {"portnum": "POSITION_APP"}}, Iface())  # another interface: ignored
    await radio.close()
    publish("meshtastic.receive.position", {**BOB, "decoded": {"portnum": "POSITION_APP"}}, iface)    # after close: ignored
    await asyncio.sleep(0.05)
    assert len(got) == 2


# ---- the API -------------------------------------------------------------------------------------------------------------


def client(tmp_path, **settings):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    c = TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts))
    if settings:
        c.put("/api/settings", json={**c.get("/api/settings").json(), **settings})
    return c


def test_api_an_empty_mesh_and_then_a_demo_mesh(tmp_path):
    c = client(tmp_path)
    empty = c.get("/api/mesh/nodes").json()
    assert empty["nodes"] == [] and empty["recording"] is True and empty["summary"]["nodes_total"] == 0
    assert c.post("/api/mesh/demo").json()["observations"] > 100
    data = c.get("/api/mesh/nodes").json()
    assert len(data["nodes"]) == len(sim.NODES) and data["summary"]["with_position"] == len(sim.NODES)
    assert data["nodes"][0]["display"] and all(set(("id", "age_s", "live", "us", "hops", "label", "note")) <= set(n) for n in data["nodes"])
    ds = c.get("/api/mesh/data").json()
    assert ds["datasets"]["nodes"]["rows"] == len(sim.NODES) and ds["datasets"]["telemetry"]["rows"] > 50 and ds["size"] > 0


def test_api_a_node_page_has_its_history_and_notes_can_be_set_and_cleared(tmp_path):
    c = client(tmp_path)
    c.post("/api/mesh/demo")
    node = c.get("/api/mesh/nodes/!5d000001").json()
    assert node["node"]["name"] == "Demo Ridge Repeater" and len(node["telemetry"]) > 5 and node["positions"]
    assert c.put("/api/mesh/nodes/!5d000001/note", json={"label": "Lodge repeater", "note": "Roof, north side"}).json()["label"] == "Lodge repeater"
    assert c.get("/api/mesh/nodes/!5d000001").json()["node"]["display"] == "Lodge repeater"
    assert c.put("/api/mesh/nodes/!5d000001/note", json={"label": "x" * 80}).status_code == 400
    c.put("/api/mesh/nodes/!5d000001/note", json={})
    assert c.get("/api/mesh/nodes/!5d000001").json()["node"]["label"] is None


def test_api_bad_ids_and_unknown_nodes(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/mesh/nodes/!5d000001").status_code == 404                # never heard
    for bad in ("bob", "!XYZ", "!5D000001"):
        assert c.get(f"/api/mesh/nodes/{bad}").status_code == 404
        assert c.put(f"/api/mesh/nodes/{bad}/note", json={"label": "x"}).status_code == 404
    assert c.get("/api/mesh/nodes/..%2Fx").status_code == 404
    assert c.get("/api/mesh/nodes/!5d000001?days=0").status_code == 404


def test_api_the_node_list_survives_a_restart(tmp_path):
    first = client(tmp_path)
    first.post("/api/mesh/demo")
    first.put("/api/mesh/nodes/!5d000002/note", json={"label": "Mine"})
    again = client(tmp_path)
    nodes = {n["id"]: n for n in again.get("/api/mesh/nodes").json()["nodes"]}
    assert len(nodes) == len(sim.NODES) and nodes["!5d000002"]["label"] == "Mine"
