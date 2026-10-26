"""Radio tests: no hardware needed. A fake meshtastic interface is driven through the real
pypubsub topics, from a separate thread, exactly like the library's receive thread does."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pubsub import pub

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.engine import Engine
from lorapedia.fitter import byte_len
from lorapedia.llm.base import MockLLM
from lorapedia.radio.base import Packet
from lorapedia.radio.manager import NO_RADIO, RadioManager
from lorapedia.radio.meshtastic_radio import (
    BROADCAST_NUM, ConnectionSpec, MeshtasticRadio, RadioError, _friendly)
from lorapedia.retrieval.base import DemoRetriever
from lorapedia.store import Store

MY_NUM = 0x1234ABCD
BOB = {"from": 0xA1B2C3D4, "fromId": "!a1b2c3d4"}


class FakeIface:
    def __init__(self):
        self.myInfo = SimpleNamespace(my_node_num=MY_NUM)
        self.nodes = {
            "!a1b2c3d4": {"user": {"shortName": "BOB", "longName": "Bob's node", "hwModel": "HELTEC_V3"},
                          "snr": 5.5, "lastHeard": 200, "hopsAway": 1},
            "!1234abcd": {"user": {"shortName": "LORA", "longName": "LoRaPedia", "id": "!1234abcd"}, "lastHeard": 300},
        }
        self.localNode = SimpleNamespace(channels=[
            SimpleNamespace(index=0, role=1, settings=SimpleNamespace(name="")),
            SimpleNamespace(index=1, role=2, settings=SimpleNamespace(name="Hikers")),
            SimpleNamespace(index=2, role=0, settings=SimpleNamespace(name="off")),
        ])
        self.sent: list[dict] = []
        self.closed = False

    def getMyNodeInfo(self):
        return {"user": {"id": "!1234abcd", "shortName": "LORA", "longName": "LoRaPedia", "hwModel": "TBEAM"}}

    def sendText(self, text, destinationId, wantAck, channelIndex, hopLimit):
        self.sent.append(dict(text=text, to=destinationId, ack=wantAck, channel=channelIndex, hops=hopLimit))

    def close(self):
        self.closed = True


def text_packet(text, to=MY_NUM, pid=1, **extra):
    return {**BOB, "to": to, "id": pid, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text},
            "rxRssi": -92, "rxSnr": 6.0, **extra}


def from_radio_thread(packet, iface):
    """Publish like the meshtastic library: on a thread that is not the asyncio loop."""
    t = threading.Thread(target=lambda: pub.sendMessage("meshtastic.receive.text", packet=packet, interface=iface))
    t.start()
    t.join()


async def until(predicate, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


async def connected_radio(received=None):
    iface = FakeIface()

    async def on_packet(pkt, meta):
        if received is not None:
            received.append((pkt, meta))

    radio = MeshtasticRadio(ConnectionSpec("serial", "/dev/ttyACM0"), on_packet=on_packet, factory=lambda spec: iface)
    await radio.connect()
    return radio, iface


# ---- packet conversion --------------------------------------------------------------------


async def test_connect_reads_own_node_id():
    radio, iface = await connected_radio()
    assert radio.node_id == "!1234abcd" and radio.connected
    await radio.close()
    assert iface.closed and not radio.connected


async def test_direct_message_conversion():
    radio, _ = await connected_radio()
    pkt = radio.to_packet(text_packet("what is a quasar?", channel=0))
    assert pkt == Packet("!a1b2c3d4", "BOB", "what is a quasar?", True, 0)


async def test_broadcast_is_a_channel_message_with_its_index():
    radio, _ = await connected_radio()
    pkt = radio.to_packet(text_packet("?quasar", to=BROADCAST_NUM, channel=2))
    assert pkt.is_dm is False and pkt.channel == 2


async def test_packet_for_another_node_is_ignored():
    radio, _ = await connected_radio()
    assert radio.to_packet(text_packet("hi", to=0x77777777)) is None


async def test_non_text_and_empty_are_ignored():
    radio, _ = await connected_radio()
    assert radio.to_packet({**BOB, "to": MY_NUM, "decoded": {"portnum": "POSITION_APP"}}) is None
    assert radio.to_packet({**BOB, "to": MY_NUM}) is None
    assert radio.to_packet(text_packet("")) is None


async def test_duplicate_packet_ids_are_dropped():
    radio, _ = await connected_radio()
    assert radio.to_packet(text_packet("hi", pid=7)) is not None
    assert radio.to_packet(text_packet("hi", pid=7)) is None
    assert radio.to_packet(text_packet("hi", pid=8)) is not None


async def test_unknown_sender_falls_back_to_id_suffix_and_synthesised_id():
    radio, _ = await connected_radio()
    pkt = radio.to_packet({"from": 0x0000BEEF, "to": MY_NUM, "decoded": {"text": "hi"}})
    assert pkt.sender_id == "!0000beef" and pkt.sender_name == "beef"


# ---- receive over pubsub from another thread ----------------------------------------------


async def test_receive_hops_from_library_thread_onto_the_loop():
    got = []
    radio, iface = await connected_radio(got)
    from_radio_thread(text_packet("what is a quasar?"), iface)
    await until(lambda: got)
    (pkt, meta), = got
    assert pkt.text == "what is a quasar?" and meta["rssi"] == -92 and meta["snr"] == 6.0
    await radio.close()


async def test_messages_from_other_interfaces_are_ignored():
    got = []
    radio, _ = await connected_radio(got)
    from_radio_thread(text_packet("hi"), FakeIface())
    await asyncio.sleep(0.05)
    assert got == []
    await radio.close()


async def test_no_delivery_after_close():
    got = []
    radio, iface = await connected_radio(got)
    await radio.close()
    from_radio_thread(text_packet("hi"), iface)
    await asyncio.sleep(0.05)
    assert got == []


# ---- sending ------------------------------------------------------------------------------


async def test_send_dm_and_broadcast_arguments():
    radio, iface = await connected_radio()
    await radio.send_text("hello", "!a1b2c3d4", 0, 3)
    await radio.send_text("hello all", None, 2, 5)
    assert iface.sent == [dict(text="hello", to="!a1b2c3d4", ack=True, channel=0, hops=3),
                          dict(text="hello all", to="^all", ack=False, channel=2, hops=5)]


async def test_send_refuses_over_meshtastic_hard_limit():
    radio, iface = await connected_radio()
    with pytest.raises(RadioError):
        await radio.send_text("x" * 234, "!a1b2c3d4", 0, 3)
    with pytest.raises(RadioError):
        await radio.send_text("é" * 117, "!a1b2c3d4", 0, 3)  # 117 chars but 234 bytes
    await radio.send_text("x" * 233, "!a1b2c3d4", 0, 3)
    assert len(iface.sent) == 1


async def test_send_when_disconnected_raises():
    radio, _ = await connected_radio()
    await radio.close()
    with pytest.raises(RadioError, match="not connected"):
        await radio.send_text("hi", None, 0, 3)


async def test_nodes_and_channels_for_the_ui():
    radio, _ = await connected_radio()
    nodes = radio.nodes()
    assert [n["id"] for n in nodes] == ["!1234abcd", "!a1b2c3d4"]  # most recently heard first
    assert nodes[0]["is_self"] and nodes[1]["short"] == "BOB" and nodes[1]["snr"] == 5.5
    assert radio.channels() == [{"index": 0, "name": "Primary"}, {"index": 1, "name": "Hikers"}]


# ---- error messages -----------------------------------------------------------------------


@pytest.mark.parametrize("exc,expected", [
    (PermissionError(13, "Permission denied: '/dev/ttyACM0'"), "dialout"),
    (OSError(16, "Device or resource busy"), "busy"),
    (SystemExit(1), "did not respond"),
    (TimeoutError("Timed out waiting for connection completion"), "did not respond"),
    (RuntimeError("boom"), "boom"),
])
def test_friendly_errors(exc, expected):
    assert expected in str(_friendly(exc, ConnectionSpec("serial", "/dev/ttyACM0")))


def test_friendly_tcp_error_names_the_address():
    err = _friendly(ConnectionRefusedError(111, "Connection refused"), ConnectionSpec("tcp", "10.0.0.7"))
    assert "10.0.0.7" in str(err) and "WiFi" in str(err)


async def test_connect_failure_raises_radio_error_even_for_system_exit():
    def factory(spec):
        raise SystemExit(1)  # what the meshtastic library does for some failures

    radio = MeshtasticRadio(ConnectionSpec("serial", "/dev/x"), on_packet=None, factory=factory)
    with pytest.raises(RadioError):
        await radio.connect()


# ---- manager ------------------------------------------------------------------------------


def make_manager(settings=None, ports=("/dev/ttyACM0",), factory=None, submitted=None):
    s = settings or Settings()
    fake = FakeIface()

    async def submit(pkt):
        if submitted is not None:
            submitted.append(pkt)

    mgr = RadioManager(settings=lambda: s, submit=submit, factory=factory or (lambda spec: fake),
                       port_lister=lambda: list(ports), poll_s=0.05)
    mgr.fake, mgr.s = fake, s
    return mgr


async def test_auto_with_no_device_uses_simulator_and_explains():
    mgr = make_manager(ports=())
    await mgr.reconcile()
    assert mgr.active == "simulator" and mgr.status()["error"] == NO_RADIO and mgr.node_id == mgr.sim.node_id


async def test_auto_with_one_device_connects():
    mgr = make_manager()
    await mgr.reconcile()
    st = mgr.status()
    assert st["active"] == "meshtastic" and st["connected"] and st["address"] == "/dev/ttyACM0"
    assert st["node_id"] == "!1234abcd" and st["error"] is None and st["device"]["short_name"] == "LORA"


async def test_auto_with_several_devices_asks_the_user_to_pick():
    mgr = make_manager(ports=("/dev/ttyACM0", "/dev/ttyACM1"))
    await mgr.reconcile()
    assert mgr.active == "simulator" and "Several radios" in mgr.status()["error"]


async def test_simulator_method_never_touches_hardware():
    calls = []
    mgr = make_manager(Settings(radio_method="simulator"), factory=lambda spec: calls.append(spec))
    await mgr.reconcile()
    assert mgr.active == "simulator" and calls == [] and mgr.status()["error"] is None


async def test_explicit_tcp_and_address_passed_to_factory():
    seen = []
    mgr = make_manager(Settings(radio_method="tcp", radio_address="192.168.1.50"),
                       factory=lambda spec: (seen.append(spec), FakeIface())[1])
    await mgr.reconcile()
    assert seen == [ConnectionSpec("tcp", "192.168.1.50")] and mgr.active == "meshtastic"


async def test_connect_failure_falls_back_and_retry_recovers():
    state = {"fail": True}

    def factory(spec):
        if state["fail"]:
            raise PermissionError(13, "Permission denied")
        return FakeIface()

    mgr = make_manager(factory=factory)
    await mgr.reconcile()
    assert mgr.active == "simulator" and "dialout" in mgr.status()["error"] and mgr._failures == 1
    state["fail"] = False
    status = await mgr.retry()
    assert status["active"] == "meshtastic" and status["error"] is None and mgr._failures == 0


async def test_connection_loss_falls_back_then_reconnects():
    mgr = make_manager()
    await mgr.reconcile()
    old = mgr._real
    mgr._loop = asyncio.get_running_loop()
    old._on_connection_lost(mgr.fake)  # what the library publishes when USB is unplugged
    assert mgr.active == "simulator"
    await mgr.reconcile()
    assert mgr.active == "meshtastic" and mgr._real is not old


async def test_changing_settings_switches_radio():
    mgr = make_manager()
    await mgr.reconcile()
    assert mgr.active == "meshtastic"
    mgr.s.radio_method = "simulator"
    await mgr.reconcile()
    assert mgr.active == "simulator" and mgr.fake.closed


async def test_supervisor_picks_up_a_device_plugged_in_later():
    ports: list[str] = []
    mgr = RadioManager(settings=lambda: Settings(), submit=lambda p: asyncio.sleep(0), factory=lambda s: FakeIface(),
                       port_lister=lambda: ports, poll_s=0.02)
    mgr.start()
    await asyncio.sleep(0.05)
    assert mgr.active == "simulator"
    ports.append("/dev/ttyACM0")
    await until(lambda: mgr.active == "meshtastic")
    await mgr.stop()


async def test_status_changes_are_emitted_but_signal_updates_are_not():
    events = []

    async def emit(e):
        events.append(e)

    mgr = make_manager(ports=())
    mgr._emit = emit
    await mgr.reconcile()
    await mgr.reconcile()  # unchanged: no second event
    assert [e["type"] for e in events] == ["radio"]


# ---- full flow: radio -> engine -> radio --------------------------------------------------


async def full_stack(settings):
    mgr = make_manager(settings)
    engine = Engine(settings=lambda: settings, radio=mgr, sim_radio=mgr.sim, llm=MockLLM(),
                    retriever=DemoRetriever(), store=Store(), sleep=lambda d: asyncio.sleep(0))
    mgr._submit = engine.submit
    await mgr.reconcile()
    worker = asyncio.create_task(engine.run_worker())
    return mgr, engine, worker


async def test_end_to_end_dm_question_answered_over_the_real_radio():
    s = Settings(send_delay_s=0, hop_limit=5)
    mgr, engine, worker = await full_stack(s)
    from_radio_thread(text_packet("what is a quasar?"), mgr.fake)
    await until(lambda: mgr.fake.sent)
    sent = mgr.fake.sent[0]
    assert sent["to"] == "!a1b2c3d4" and sent["ack"] and sent["hops"] == 5
    assert "quasar" in sent["text"].lower() and byte_len(sent["text"]) <= 200
    assert mgr.sim.sent == []
    worker.cancel()


async def test_end_to_end_channel_reply_uses_the_incoming_channel_and_prefix():
    s = Settings(send_delay_s=0, trigger_mode="channel", channel_index=1)
    mgr, engine, worker = await full_stack(s)
    from_radio_thread(text_packet("?what is photosynthesis", to=BROADCAST_NUM, pid=2, channel=1), mgr.fake)
    await until(lambda: mgr.fake.sent)
    sent = mgr.fake.sent[0]
    assert sent["to"] == "^all" and sent["channel"] == 1 and sent["text"].startswith("@BOB ")
    assert byte_len(sent["text"]) <= 200
    worker.cancel()


async def test_channel_trigger_ignores_other_channels():
    s = Settings(send_delay_s=0, trigger_mode="channel", channel_index=1)
    mgr, engine, worker = await full_stack(s)
    from_radio_thread(text_packet("?quasar", to=BROADCAST_NUM, pid=3, channel=0), mgr.fake)
    await asyncio.sleep(0.1)
    assert mgr.fake.sent == [] and engine.queue.empty()
    worker.cancel()


async def test_simulated_sender_is_answered_in_the_simulator_even_with_a_real_radio():
    s = Settings(send_delay_s=0)
    mgr, engine, worker = await full_stack(s)
    await engine.submit(Packet("!sim00001", "SIM", "what is a quasar?", True, 0, simulated=True))
    await until(lambda: mgr.sim.sent)
    assert mgr.fake.sent == []  # nothing leaked onto the real mesh
    worker.cancel()


async def test_multipart_reply_goes_out_as_separate_radio_messages():
    class Long(MockLLM):
        async def generate(self, system, user, *, temperature, max_tokens):
            return "Quasars are bright objects powered by black holes. " * 12

    s = Settings(send_delay_s=0, max_parts=3)
    mgr, engine, worker = await full_stack(s)
    engine.llm = Long()
    from_radio_thread(text_packet("what is a quasar?", pid=9), mgr.fake)
    await until(lambda: len(mgr.fake.sent) == 3)
    assert [m["text"][:6] for m in mgr.fake.sent] == ["(1/3) ", "(2/3) ", "(3/3) "]
    assert all(byte_len(m["text"]) <= 200 for m in mgr.fake.sent)
    worker.cancel()


# ---- API ----------------------------------------------------------------------------------


def test_api_radio_endpoints(tmp_path):
    fake = FakeIface()
    opts = {"factory": lambda spec: fake, "port_lister": lambda: ["/dev/ttyACM0"], "poll_s": 60}
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts)
    with TestClient(app) as client:
        client.post("/api/radio/retry")  # connect now rather than waiting on the supervisor
        st = client.get("/api/radio/status").json()
        assert st["active"] == "meshtastic" and st["node_id"] == "!1234abcd"
        assert client.get("/api/health").json()["radio"] == "meshtastic"
        assert client.get("/api/radio/ports").json() == {"serial": ["/dev/ttyACM0"]}
        assert [c["name"] for c in client.get("/api/radio/channels").json()] == ["Primary", "Hikers"]

        nodes = {n["id"]: n for n in client.get("/api/radio/nodes").json()}
        assert nodes["!a1b2c3d4"]["blocked"] is False
        assert client.post("/api/radio/nodes/!a1b2c3d4/block", json={"on": True}).json() == {"blocklist": ["!a1b2c3d4"]}
        assert client.get("/api/radio/nodes").json()[1]["blocked"] is True
        assert client.post("/api/radio/nodes/!a1b2c3d4/block", json={"on": False}).json() == {"blocklist": []}
        assert client.post("/api/radio/nodes/!a1b2c3d4/allow", json={"on": True}).json() == {"allowlist": ["!a1b2c3d4"]}
        assert client.post("/api/radio/nodes/!a1b2c3d4/nope", json={"on": True}).status_code == 404
        assert client.post("/api/radio/nodes/!a1b2c3d4/allow", json={"on": False}).json() == {"allowlist": []}

        # Simulated messages never reach the real radio
        assert client.post("/api/simulator/send", json={"text": "what is a quasar?"}).json() == {"outcome": "queued"}

        assert client.post("/api/radio/use-simulator").json()["active"] == "simulator"
        assert client.get("/api/settings").json()["radio_method"] == "simulator"
        assert fake.closed and fake.sent == []


def test_api_without_a_device_defaults_to_simulator_with_actionable_error(tmp_path):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts)) as client:
        st = client.post("/api/radio/retry").json()
        assert st["active"] == "simulator" and st["error"] == NO_RADIO
