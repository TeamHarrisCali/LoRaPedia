"""Traceroute (the one thing here that transmits on request) and link quality: parsing, guardrails, the radio adapter and the API."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.mesh.store import MeshStore
from lorapedia.mesh.traceroute import Busy, TracerouteError, TracerouteService, build_path, check_target, parse_reply
from lorapedia.radio.meshtastic_radio import ConnectionSpec, MeshtasticRadio, RadioError

ME, A, B, T = 0x11111111, 0xAAAA0001, 0xBBBB0002, 0xCCCC0003
BY_NUM = {A: {"id": "!aaaa0001", "name": "Ridge", "lat": 46.1, "lon": -121.1}, B: {"id": "!bbbb0002", "name": "Lodge", "lat": 46.2, "lon": -121.2},
          T: {"id": "!cccc0003", "name": "Target", "lat": 46.3, "lon": -121.3}}


def reply(route=(A, B), back=(B, A), towards=(28, -8, 20), snr_back=(12, 4, -128), **extra):
    return {"from": T, "to": ME, "hopStart": 3, "decoded": {"portnum": "TRACEROUTE_APP", "traceroute": {
        "route": list(route), "routeBack": list(back), "snrTowards": list(towards), "snrBack": list(snr_back)}}, **extra}


# ---- the parser ----------------------------------------------------------------------------------------------------------


def test_a_reply_becomes_two_paths_with_names_positions_and_signal_in_db():
    towards, back, raw = parse_reply(reply(), ME, BY_NUM)
    assert [h["id"] for h in towards] == ["!11111111", "!aaaa0001", "!bbbb0002", "!cccc0003"]
    assert [h["snr"] for h in towards] == [None, 7.0, -2.0, 5.0]            # quarter-dB steps; the sender has no signal
    assert towards[1]["name"] == "Ridge" and towards[1]["lat"] == 46.1 and towards[0]["name"] is None
    assert [h["id"] for h in back] == ["!cccc0003", "!bbbb0002", "!aaaa0001", "!11111111"]
    assert [h["snr"] for h in back] == [None, 3.0, 1.0, None]                 # -128 means unknown
    assert raw["route"] == [A, B]


def test_a_hop_the_radio_could_not_name_is_shown_as_unknown_not_as_everyone():
    towards, _, _ = parse_reply(reply(route=(0xFFFFFFFF, B), towards=(4, 4, 4)), ME, BY_NUM)
    assert towards[1] == {"num": None, "id": None, "name": "Unknown node", "snr": 1.0, "lat": None, "lon": None}


def test_signal_lists_of_the_wrong_length_are_ignored_rather_than_misaligned():
    towards, back, _ = parse_reply(reply(towards=(4, 4)), ME, BY_NUM)
    assert [h["snr"] for h in towards] == [None, None, None, None]
    assert back is not None
    assert parse_reply(reply(snr_back=(4,)), ME, BY_NUM)[1] is None           # the way back needs a matching list


def test_the_way_back_needs_a_hop_counter():
    packet = reply()
    del packet["hopStart"]
    assert parse_reply(packet, ME, BY_NUM)[1] is None


def test_a_direct_neighbour_has_no_relays():
    towards, back, _ = parse_reply(reply(route=(), back=(), towards=(20,), snr_back=(8,)), ME, BY_NUM)
    assert len(towards) == 2 and len(back) == 2 and towards[1]["snr"] == 5.0


def test_a_radio_error_reply_is_reported_in_words():
    with pytest.raises(TracerouteError, match="NO_ROUTE"):
        parse_reply({"from": T, "decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": "NO_ROUTE"}}}, ME, BY_NUM)


def test_a_raw_payload_is_decoded_with_the_protocol_definition():
    from meshtastic.protobuf import mesh_pb2
    msg = mesh_pb2.RouteDiscovery()
    msg.route.extend([A])
    msg.snr_towards.extend([8, 12])
    towards, _, _ = parse_reply({"from": T, "to": ME, "decoded": {"portnum": "TRACEROUTE_APP", "payload": msg.SerializeToString()}}, ME, BY_NUM)
    assert [h["id"] for h in towards] == ["!11111111", "!aaaa0001", "!cccc0003"] and towards[2]["snr"] == 3.0


@pytest.mark.parametrize("packet", [{}, {"decoded": {}}, {"from": T, "decoded": {"traceroute": "x"}}, {"from": True, "decoded": {"traceroute": {}}},
                                    {"from": "bob", "decoded": {"traceroute": {}}}, {"decoded": {"traceroute": {"route": [1]}}}])
def test_a_reply_that_is_not_a_route_is_an_error_not_a_crash(packet):
    with pytest.raises(TracerouteError):
        parse_reply(packet, None, BY_NUM)


def test_hostile_route_contents_do_not_break_the_path_builder():
    path = build_path(ME, ["x", None, 5.5, A, True], T, [1, 2], BY_NUM)
    assert [h["num"] for h in path] == [ME, A, T] and [h["snr"] for h in path] == [None, 0.25, 0.5]   # junk is dropped, not trusted
    assert build_path(ME, "not a list", T, None, BY_NUM)[0]["num"] == ME


@pytest.mark.parametrize("node,hops,ok", [("!aaaa0001", 3, True), ("!AAAA0001", 7, True), (" !aaaa0001 ", 1, True), ("!ffffffff", 3, False),
                                          ("!FFFFFFFF", 3, False), ("^all", 3, False), ("", 3, False), (None, 3, False), ("!zzzz0001", 3, False),
                                          ("!aaaa0001", 0, False), ("!aaaa0001", 8, False), ("!aaaa0001", True, False), ("!aaaa0001", "3", False),
                                          ("!aaaa0001", 2.5, False), ("!00510000", 3, False)])
def test_only_one_real_node_and_a_sane_hop_limit_may_be_traced(node, hops, ok):
    if ok:
        assert check_target(node, hops, "!00510000") == node.strip().lower()
    else:
        with pytest.raises(TracerouteError):
            check_target(node, hops, "!00510000")


# ---- the service: one at a time, cooldown, outcomes, saved --------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def service(send, clock=None, **kw):
    clock = clock or Clock()
    return TracerouteService(MeshStore(), send, lambda: BY_NUM, lambda: "!11111111", lambda: ME, clock=clock, **kw), clock


async def done(svc):
    await svc._running


async def ok_send(node_id, hop_limit, timeout):
    return reply()


async def test_a_traceroute_runs_and_its_result_is_saved_and_readable_later():
    svc, _ = service(ok_send)
    rid = svc.start("!cccc0003", 3)
    assert svc.state(rid)["status"] == "waiting"
    await done(svc)
    st = svc.state(rid)
    assert st["status"] == "ok" and st["trace"]["relays_towards"] == 2 and st["trace"]["relays_back"] == 2 and st["trace"]["hop_limit"] == 3
    assert [h["name"] for h in st["trace"]["path_towards"]][1:3] == ["Ridge", "Lodge"]
    (saved,) = svc.store.traceroutes()
    assert saved["id"] == st["trace"]["id"] and saved["node_id"] == "!cccc0003" and saved["path_back"][1]["id"] == "!bbbb0002"


async def test_nobody_answering_is_a_saved_timeout_with_the_reason():
    async def silent(node_id, hop_limit, timeout):
        raise asyncio.TimeoutError

    svc, _ = service(silent, timeout=7)
    rid = svc.start("!cccc0003", 2)
    await done(svc)
    st = svc.state(rid)
    assert st["status"] == "timeout" and "7 s" in st["detail"] and "2 hops" in st["detail"]
    assert svc.store.traceroutes()[0]["status"] == "timeout"


async def test_a_radio_that_refuses_is_a_saved_error_with_its_message():
    async def refuses(node_id, hop_limit, timeout):
        raise RadioError("Radio is not connected.")

    svc, _ = service(refuses)
    rid = svc.start("!cccc0003")
    await done(svc)
    assert svc.state(rid)["status"] == "error" and "not connected" in svc.state(rid)["detail"]


async def test_a_reply_that_cannot_be_read_is_a_saved_error():
    async def junk(node_id, hop_limit, timeout):
        return {"from": T}

    svc, _ = service(junk)
    rid = svc.start("!cccc0003")
    await done(svc)
    assert svc.state(rid)["status"] == "error" and svc.store.traceroutes()[0]["status"] == "error"


async def test_only_one_traceroute_is_on_the_air_at_a_time():
    gate = asyncio.Event()

    async def slow(node_id, hop_limit, timeout):
        await gate.wait()
        return reply()

    svc, _ = service(slow)
    svc.start("!cccc0003")
    with pytest.raises(Busy, match="in progress"):
        svc.start("!aaaa0001")
    gate.set()
    await done(svc)
    svc.start("!aaaa0001")                      # free again once it has finished
    await done(svc)


async def test_the_same_node_is_not_traced_again_within_thirty_seconds():
    svc, clock = service(ok_send)
    svc.start("!cccc0003")
    await done(svc)
    clock.t += 10
    with pytest.raises(Busy, match="moments ago") as e:
        svc.start("!cccc0003")
    assert "21 s" in str(e.value) or "20 s" in str(e.value)
    svc.start("!aaaa0001")                      # another node is fine
    await done(svc)
    clock.t += 31
    svc.start("!cccc0003")                      # and after the cooldown, the first again
    await done(svc)


async def test_a_refused_request_does_not_use_up_the_cooldown_or_leave_a_state_behind():
    svc, _ = service(ok_send)
    with pytest.raises(TracerouteError):
        svc.start("!ffffffff")
    with pytest.raises(TracerouteError):
        svc.start("!cccc0003", 99)
    assert svc._states == {}
    svc.start("!cccc0003")                      # the bad attempts did not start a cooldown for it
    await done(svc)


async def test_only_the_newest_states_are_kept():
    svc, clock = service(ok_send, cooldown=0)
    for _ in range(30):
        svc.start("!cccc0003")
        await done(svc)
    assert len(svc._states) == 20 and svc.state(1) is None and svc.state(30) is not None


async def test_history_is_newest_first_and_pages():
    svc, _ = service(ok_send, cooldown=0)
    for _ in range(5):
        svc.start("!cccc0003")
        await done(svc)
    ids = [t["id"] for t in svc.store.traceroutes(limit=3)]
    assert ids == sorted(ids, reverse=True) and len(ids) == 3
    assert ids == [5, 4, 3] and [t["id"] for t in svc.store.traceroutes(limit=10, before=ids[-1])] == [2, 1]
    assert len(svc.store.traceroutes(limit=9999)) == 5


# ---- the radio adapter ---------------------------------------------------------------------------------------------------------


class Iface:
    def __init__(self, respond=None):
        self.myInfo = SimpleNamespace(my_node_num=ME)
        self.nodes, self.sent, self.texts, self.respond = {}, [], [], respond

    def getMyNodeInfo(self):
        return {"user": {"id": "!11111111"}}

    def sendData(self, data, destinationId, portNum, wantResponse, onResponse, channelIndex, hopLimit):
        self.sent.append(dict(to=destinationId, port=int(portNum), want=wantResponse, channel=channelIndex, hops=hopLimit))
        if self.respond:
            threading.Thread(target=lambda: self.respond(onResponse), daemon=True).start()

    def sendText(self, *a, **k):
        self.texts.append((a, k))

    def close(self):
        pass


async def connected(iface):
    async def on_packet(pkt, meta):
        pass

    radio = MeshtasticRadio(ConnectionSpec("serial", "/dev/x"), on_packet=on_packet, factory=lambda spec: iface)
    await radio.connect()
    return radio


async def test_the_adapter_sends_one_request_to_one_node_and_waits_for_the_real_answer():
    def respond(cb):
        cb({"decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": "NONE"}}})   # an acknowledgement: not the answer
        time.sleep(0.05)
        cb(reply())

    iface = Iface(respond)
    radio = await connected(iface)
    packet = await radio.traceroute("!cccc0003", 4, 2.0)
    assert packet["decoded"]["portnum"] == "TRACEROUTE_APP"
    assert iface.sent == [dict(to="!cccc0003", port=70, want=True, channel=0, hops=4)] and iface.texts == []   # 70 is TRACEROUTE_APP


async def test_the_adapter_times_out_when_nobody_answers():
    radio = await connected(Iface(None))
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await radio.traceroute("!cccc0003", 3, 0.1)


@pytest.mark.parametrize("target", ["!ffffffff", "^all", "", "!zz", None, "!cccc0003; rm"])
async def test_the_adapter_has_no_way_to_trace_everyone_or_anything_odd(target):
    iface = Iface()
    radio = await connected(iface)
    with pytest.raises(RadioError):
        await radio.traceroute(target, 3, 0.1)
    assert iface.sent == []


async def test_the_adapter_refuses_when_there_is_no_connection():
    radio = await connected(Iface())
    await radio.close()
    with pytest.raises(RadioError, match="not connected"):
        await radio.traceroute("!cccc0003", 3, 0.1)


# ---- the API (simulator: a made-up route, nothing transmitted) ----------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path):
    """One persistent event loop (as under uvicorn): a traceroute runs as a background task there and must survive between requests."""
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})) as c:
        c.post("/api/mesh/demo")
        yield c


def client(tmp_path):
    c = TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60}))
    c.post("/api/mesh/demo")
    return c


def wait_for(c, rid):
    for _ in range(200):
        st = c.get(f"/api/mesh/traceroute/{rid}").json()
        if st["status"] != "waiting":
            return st
        time.sleep(0.02)
    raise AssertionError("traceroute did not finish")


def test_api_a_simulated_traceroute_goes_through_known_nodes_and_is_marked_as_made_up(api_client):
    c = api_client
    r = c.post("/api/mesh/traceroute", json={"node_id": "!5d000007", "hop_limit": 4})
    assert r.status_code == 202 and r.json()["simulated"] is True
    st = wait_for(c, r.json()["id"])
    assert st["status"] == "ok" and "Simulated" in st["detail"]
    t = st["trace"]
    assert t["path_towards"][0]["id"] == "!00510000" and t["path_towards"][-1]["id"] == "!5d000007" and t["relays_towards"] >= 1
    assert all(h["name"] for h in t["path_towards"][1:])
    hist = c.get("/api/mesh/traceroutes").json()["traceroutes"]
    assert hist[0]["id"] == t["id"] and hist[0]["node_name"] == "Demo Far Peak"


def test_api_it_refuses_what_it_should(api_client):
    c = api_client
    for body, code in (({"node_id": "!ffffffff"}, 400), ({"node_id": "^all"}, 400), ({"node_id": "!5d0000ff"}, 400),     # never heard
                       ({"node_id": "!5d000001", "hop_limit": 0}, 400), ({"node_id": "!5d000001", "hop_limit": 99}, 400),
                       ({"node_id": "!00510000"}, 400), ({}, 422), ({"node_id": 5}, 422), ({"node_id": "!5d000001", "hop_limit": "x"}, 422)):
        assert c.post("/api/mesh/traceroute", json=body).status_code == code, body
    assert c.get("/api/mesh/traceroutes").json()["traceroutes"] == []        # nothing was sent, nothing recorded
    assert c.get("/api/mesh/traceroute/999").status_code == 404


def test_api_a_second_request_for_the_same_node_is_a_conflict(api_client):
    c = api_client
    first = c.post("/api/mesh/traceroute", json={"node_id": "!5d000007"})
    wait_for(c, first.json()["id"])
    again = c.post("/api/mesh/traceroute", json={"node_id": "!5d000007"})
    assert again.status_code == 409 and "moments ago" in again.json()["detail"]


# ---- link quality -----------------------------------------------------------------------------------------------------------------


def test_api_links_summarise_direct_hearing_and_measure_distance_from_this_radio(tmp_path):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    c = TestClient(app)
    c.post("/api/mesh/demo")
    now = app.state.mesh._clock()
    app.state.mesh_store.write(nodes={"!00510000": {"last_seen": now, "name": "Us", "lat": 46.85, "lon": -121.76}}, packets={}, talkers={}, hops={},
                               telemetry=[], positions=[], links={(int(now // 3600), "!5d000002"): [4, 20.0, 3.0, 8.0, -360, -100, -80]})
    out = c.get("/api/mesh/links?days=7").json()
    assert out["us"] == {"lat": 46.85, "lon": -121.76}
    by = {l["id"]: l for l in out["links"]}
    assert set(by) >= {"!5d000001", "!5d000002", "!5d000006"} and "!5d000003" not in by            # only nodes heard directly
    assert all(by[i]["distance_km"] is not None and by[i]["snr_min"] <= by[i]["snr_avg"] <= by[i]["snr_max"] for i in by)
    assert out["farthest"]["distance_km"] == max(l["distance_km"] for l in out["links"])
    assert [l["packets"] for l in out["links"]] == sorted((l["packets"] for l in out["links"]), reverse=True)
    assert c.get("/api/mesh/links?days=0").json()["days"] == 1 and c.get("/api/mesh/links?days=999").json()["days"] == 30


def test_api_links_without_a_position_for_this_radio_have_no_distances_rather_than_wrong_ones(tmp_path):
    c = client(tmp_path)
    out = c.get("/api/mesh/links").json()
    assert out["us"] is None and out["links"] and all(l["distance_km"] is None for l in out["links"]) and out["farthest"] is None


def test_api_links_ignore_nodes_that_have_since_been_forgotten(tmp_path):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    c = TestClient(app)
    now = app.state.mesh._clock()
    app.state.mesh_store.write(nodes={}, packets={}, talkers={}, hops={}, telemetry=[], positions=[], links={(int(now // 3600), "!dead0001"): [1, 2.0, 2.0, 2.0, -90, -90, -90]})
    assert c.get("/api/mesh/links").json()["links"] == []


def test_a_simulated_route_never_passes_through_this_radio_itself(api_client):
    c = api_client
    for node in ("!5d000007", "!5d000004", "!5d000003", "!5d000008"):
        rid = c.post("/api/mesh/traceroute", json={"node_id": node, "hop_limit": 7}).json()["id"]
        t = wait_for(c, rid)["trace"]
        for path in (t["path_towards"], t["path_back"]):
            ids = [h["id"] for h in path]
            assert ids.count("!00510000") == 1 and len(set(ids)) == len(ids)       # once, at the start or the end, and no node twice
        c.app.state.tracer._last.clear()                                            # the cooldown is not what is being tested here
