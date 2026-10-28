"""Phase 6: the Radio settings page (the radio's own configuration), validated and backed up, never touching secrets."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from meshtastic.protobuf import localonly_pb2

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.radio import config as rc
from lorapedia.radio.config import ConfigError, RadioConfig
from lorapedia.store import Store

ME = "!1234abcd"
SECRETS = ("S3CRETKEYBYTES", "hunter2hunter2", "mqtt-pass-word", "987654")


class FakeNode:
    """The library's local node: real protobuf messages, and a record of what was written to the radio."""

    def __init__(self):
        self.localConfig, self.moduleConfig = localonly_pb2.LocalConfig(), localonly_pb2.LocalModuleConfig()
        self.localConfig.lora.region = self.localConfig.lora.RegionCode.Value("US")
        self.localConfig.lora.hop_limit = 3
        self.localConfig.lora.tx_enabled = True
        self.localConfig.device.role = self.localConfig.device.Role.Value("CLIENT")
        self.localConfig.position.position_broadcast_secs = 900
        self.localConfig.security.private_key = SECRETS[0].encode()          # secrets the editor must never show, copy or write
        self.localConfig.network.wifi_psk = SECRETS[1]
        self.moduleConfig.mqtt.password = SECRETS[2]
        self.localConfig.bluetooth.fixed_pin = int(SECRETS[3])
        self.moduleConfig.telemetry.device_update_interval = 1800
        self.calls, self.fail_on = [], None

    def beginSettingsTransaction(self):
        self.calls.append(("begin",))

    def writeConfig(self, name):
        self.calls.append(("write", name))
        if self.fail_on == name:
            raise self.fail_with

    def commitSettingsTransaction(self):
        self.calls.append(("commit",))

    def setOwner(self, long_name, short_name):
        self.calls.append(("owner", long_name, short_name))

    def setFixedPosition(self, lat, lon, alt):
        self.calls.append(("fixed", lat, lon, alt))

    def removeFixedPosition(self):
        self.calls.append(("unfixed",))

    fail_with = RuntimeError("radio said no")


def make(enabled=True, connected=True, radio=ME):
    node, store = FakeNode(), Store()
    state = {"enabled": enabled}

    def get_node():
        if not connected:
            raise ConfigError("No radio is connected.", 409)
        return node

    cfg = RadioConfig(node=get_node, store=store, radio_id=lambda: radio, enabled=lambda: state["enabled"])
    return cfg, node, store, state


def everything(obj) -> str:
    return json.dumps(obj, default=str)


# ---- what can be seen --------------------------------------------------------------------------------------------------------------


def test_the_form_lists_only_the_allowed_sections_and_no_secret_ever_appears_in_it():
    cfg, node, store, _ = make()
    view = cfg.view()
    assert view["connected"] and view["radio"] == ME and view["writable"]
    assert [s["name"] for s in view["sections"]] == list(rc.SECTIONS)
    assert not {"network", "security", "mqtt"} & {s["name"] for s in view["sections"]}
    assert {e["name"] for e in view["excluded"]} == {"network", "security", "mqtt"}
    lora = next(s for s in view["sections"] if s["name"] == "lora")
    region = next(f for f in lora["fields"] if f["name"] == "region")
    assert region["kind"] == "enum" and region["value"] == "US" and "EU_868" in region["choices"] and region["risk"] and region["help"]
    hop = next(f for f in lora["fields"] if f["name"] == "hop_limit")
    assert (hop["kind"], hop["value"], hop["min"], hop["max"]) == ("int", 3, 0, 7)
    names = {(s["name"], f["name"]) for s in view["sections"] for f in s["fields"]}
    assert ("bluetooth", "fixed_pin") not in names
    pulled = cfg.pull()
    for blob in (everything(view), everything(pulled), everything(rc.snapshot(node)), everything(store.radio_backups()),
                 everything(store.radio_backup(pulled["backup_id"]))):
        assert not any(s in blob for s in SECRETS)


def test_with_no_radio_the_page_says_so_and_nothing_can_be_written():
    cfg, node, store, _ = make(connected=False)
    view = cfg.view()
    assert view["connected"] is False and view["sections"] == [] and view["backups"] == []
    with pytest.raises(ConfigError) as e:
        cfg.save({"lora": {"hop_limit": 4}})
    assert e.value.status == 409 and node.calls == [] and store.radio_backups() == []
    with pytest.raises(ConfigError):
        cfg.pull()


# ---- the checks before anything is sent ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("changes, why", [
    ({}, "nothing to change"), (None, "nothing to change"), ([], "nothing to change"), ({"lora": []}, "Malformed"), ({5: {"a": 1}}, "Malformed"),
    ({"network": {"wifi_psk": "x"}}, "can't be viewed or changed"), ({"security": {"private_key": "x"}}, "can't be viewed or changed"),
    ({"mqtt": {"password": "x"}}, "can't be viewed or changed"), ({"version": {}}, "can't be viewed or changed"),
    ({"bluetooth": {"fixed_pin": 111111}}, "isn't a setting you can change"), ({"lora": {"nonsense": 1}}, "isn't a setting you can change"),
    ({"lora": {"hop_limit": 8}}, "between 0 and 7"), ({"lora": {"hop_limit": -1}}, "between 0 and 7"), ({"lora": {"hop_limit": 2.5}}, "whole number"),
    ({"lora": {"hop_limit": True}}, "whole number"), ({"lora": {"hop_limit": "3"}}, "whole number"), ({"lora": {"hop_limit": float("nan")}}, "whole number"),
    ({"lora": {"tx_power": 31}}, "between 0 and 30"), ({"lora": {"region": "MARS"}}, "isn't one of the choices"), ({"lora": {"region": 1}}, "isn't one of the choices"),
    ({"lora": {"tx_enabled": 1}}, "on or off"), ({"lora": {"tx_enabled": "true"}}, "on or off"),
    ({"device": {"tzdef": "a\x00b"}}, "plain text"), ({"device": {"tzdef": "x" * 201}}, "plain text"), ({"device": {"tzdef": 5}}, "plain text"),
    ({"lora": {"frequency_offset": "1"}}, "must be a number"), ({"lora": {"frequency_offset": float("inf")}}, "must be a number"),
])
def test_a_change_that_is_not_valid_is_refused_before_anything_is_touched(changes, why):
    cfg, node, store, _ = make()
    before = rc.snapshot(node)
    with pytest.raises(ConfigError, match=why):
        cfg.save(changes)
    assert node.calls == [] and rc.snapshot(node) == before and store.radio_backups() == []


def test_one_bad_value_stops_the_whole_change():
    cfg, node, store, _ = make()
    with pytest.raises(ConfigError):
        cfg.save({"lora": {"hop_limit": 5}, "device": {"role": "NOT_A_ROLE"}})
    assert node.localConfig.lora.hop_limit == 3 and node.calls == []


# ---- writing ---------------------------------------------------------------------------------------------------------------------------


def test_a_change_is_backed_up_then_written_in_one_transaction_and_only_the_changed_sections():
    cfg, node, store, _ = make()
    out = cfg.save({"lora": {"hop_limit": 5, "region": "US"}, "device": {"role": "CLIENT"}, "telemetry": {"device_update_interval": 3600}})
    assert node.calls == [("begin",), ("write", "lora"), ("write", "telemetry"), ("commit",)]            # device did not change: not written
    assert node.localConfig.lora.hop_limit == 5 and node.moduleConfig.telemetry.device_update_interval == 3600
    assert [(d["section"], d["field"], d["old"], d["new"]) for d in out["changed"]] == [("lora", "hop_limit", 3, 5), ("telemetry", "device_update_interval", 1800, 3600)]
    assert out["restarting"] is True and out["changed"][0]["risk"]
    (backup,) = store.radio_backups()
    assert backup["reason"] == "before changes" and backup["radio"] == ME
    saved = store.radio_backup(backup["id"])["config"]
    assert saved["lora"]["hop_limit"] == 3 and saved["telemetry"]["device_update_interval"] == 1800        # what was replaced, not the new values


def test_a_change_to_what_the_radio_already_has_does_nothing():
    cfg, node, store, _ = make()
    out = cfg.save({"lora": {"hop_limit": 3, "region": "US"}})
    assert out["changed"] == [] and "already" in out["message"] and node.calls == [] and store.radio_backups() == []


def test_a_radio_that_refuses_a_write_leaves_our_picture_of_it_unchanged_and_the_copy_kept():
    cfg, node, store, _ = make()
    node.fail_on = "device"
    with pytest.raises(ConfigError, match="didn't accept") as e:
        cfg.save({"lora": {"hop_limit": 6}, "device": {"role": "ROUTER"}})
    assert e.value.status == 502
    assert node.localConfig.lora.hop_limit == 3 and node.localConfig.device.role == node.localConfig.device.Role.Value("CLIENT")
    assert [b["reason"] for b in store.radio_backups()] == ["before changes"]
    node.fail_on, node.fail_with = "lora", SystemExit(1)                                                       # the library may exit instead of raising
    with pytest.raises(ConfigError, match="didn't accept"):
        cfg.save({"lora": {"hop_limit": 6}})
    assert node.localConfig.lora.hop_limit == 3
    node.fail_with = RuntimeError("radio said no")


def test_two_changes_cannot_overlap():
    cfg, node, _, _ = make()
    assert cfg._busy.acquire(blocking=False)
    try:
        with pytest.raises(ConfigError, match="busy") as e:
            cfg.save({"lora": {"hop_limit": 4}})
        assert e.value.status == 409 and node.calls == []
    finally:
        cfg._busy.release()
    cfg.save({"lora": {"hop_limit": 4}})
    assert node.localConfig.lora.hop_limit == 4


def test_writes_are_off_until_you_turn_them_on_but_looking_is_always_allowed():
    cfg, node, store, state = make(enabled=False)
    assert cfg.view()["writable"] is False and cfg.pull()["backup_id"]
    for call in (lambda: cfg.save({"lora": {"hop_limit": 4}}), lambda: cfg.restore(1), lambda: cfg.set_names("Name", "NM"),
                 lambda: cfg.set_position(46.0, -121.0, 100), cfg.clear_position):
        with pytest.raises(ConfigError, match="turned off") as e:
            call()
        assert e.value.status == 403
    assert node.calls == []
    state["enabled"] = True
    cfg.save({"lora": {"hop_limit": 4}})
    assert node.calls


# ---- going back ------------------------------------------------------------------------------------------------------------------------


def test_a_copy_can_be_restored_and_what_it_replaces_is_copied_first():
    cfg, node, store, _ = make()
    cfg.save({"lora": {"hop_limit": 6}, "device": {"role": "ROUTER"}})
    first = store.radio_backups()[0]["id"]                                                                   # the settings before that change
    node.calls.clear()
    out = cfg.restore(first)
    assert node.localConfig.lora.hop_limit == 3 and node.localConfig.device.role == node.localConfig.device.Role.Value("CLIENT")
    assert node.calls == [("begin",), ("write", "device"), ("write", "lora"), ("commit",)]
    assert out["restarting"] and [b["reason"] for b in store.radio_backups()][:2] == ["before restore", "before changes"]
    assert cfg.restore(first)["changed"] == [] and node.calls[-1] == ("commit",)


def test_a_copy_from_another_radio_is_refused_unless_you_insist():
    cfg, node, store, _ = make()
    other = store.add_radio_backup("pulled", "!99999999", rc.snapshot(node))
    node.localConfig.lora.hop_limit = 5
    with pytest.raises(ConfigError, match="different radio") as e:
        cfg.restore(other)
    assert e.value.status == 409 and node.calls == []
    assert cfg.restore(other, force=True)["restarting"]
    assert node.localConfig.lora.hop_limit == 3
    with pytest.raises(ConfigError) as e:
        cfg.restore(9999)
    assert e.value.status == 404


def test_a_copy_that_was_tampered_with_cannot_reach_a_secret_section_or_a_bad_value():
    cfg, node, store, _ = make()
    snap = rc.snapshot(node)
    snap["network"] = {"wifi_psk": "evil"}
    snap["security"] = {"private_key": "evil"}
    snap["bluetooth"]["fixed_pin"] = 111111
    snap["lora"]["hop_limit"] = 5
    bid = store.add_radio_backup("pulled", ME, snap)
    with pytest.raises(ConfigError, match="isn't a setting"):
        cfg.restore(bid)                                                                                       # the hidden field stops the whole restore
    assert node.calls == [] and node.localConfig.network.wifi_psk == SECRETS[1]
    snap["bluetooth"].pop("fixed_pin")
    snap["lora"]["hop_limit"] = 99
    with pytest.raises(ConfigError, match="between 0 and 7"):
        cfg.restore(store.add_radio_backup("pulled", ME, snap))
    snap["lora"]["hop_limit"] = 5
    cfg.restore(store.add_radio_backup("pulled", ME, snap))                                                     # now valid; the secret sections are ignored
    assert node.localConfig.lora.hop_limit == 5 and node.localConfig.network.wifi_psk == SECRETS[1] and node.localConfig.security.private_key == SECRETS[0].encode()
    assert not any(c[1] in ("network", "security", "mqtt") for c in node.calls if c[0] == "write")


def test_only_the_newest_forty_copies_are_kept():
    cfg, node, store, _ = make()
    for _ in range(rc.BACKUP_KEEP + 7):
        cfg.pull()
    assert len(store.radio_backups()) == rc.BACKUP_KEEP


# ---- name and position ---------------------------------------------------------------------------------------------------------------


def test_the_name_is_checked_and_sent():
    cfg, node, _, _ = make()
    assert cfg.set_names("  Ridge Node 1 ", "RDG1")["long_name"] == "Ridge Node 1" and node.calls == [("owner", "Ridge Node 1", "RDG1")]
    for long, short in (("", "AB"), ("   ", "AB"), ("x" * 40, "AB"), ("é" * 20, "AB"), ("ok", ""), ("ok", "ABCDE"), ("a\x00b", "AB"), ("ok", "A\nB"), (5, "AB"), ("ok", None)):
        with pytest.raises(ConfigError):
            cfg.set_names(long, short)
    assert len(node.calls) == 1
    node.setOwner = lambda *a: (_ for _ in ()).throw(SystemExit(1))                                           # the library exits on some bad input
    with pytest.raises(ConfigError, match="didn't accept"):
        cfg.set_names("Fine", "FN")


def test_a_fixed_position_is_range_checked_sent_and_removable():
    cfg, node, _, _ = make()
    out = cfg.set_position(46.85, -121.76, 1500)
    assert node.calls == [("fixed", 46.85, -121.76, 1500)] and out["alt"] == 1500
    cfg.set_position(0, 0, None)
    assert node.calls[-1] == ("fixed", 0.0, 0.0, 0)
    n = len(node.calls)
    for lat, lon, alt in ((91, 0, 0), (-90.1, 0, 0), (0, 181, 0), (0, -181, 0), (float("nan"), 0, 0), (0, float("inf"), 0), (True, 0, 0), ("46", 0, 0), (0, 0, 9001), (0, 0, "x")):
        with pytest.raises(ConfigError):
            cfg.set_position(lat, lon, alt)
    assert len(node.calls) == n
    cfg.clear_position()
    assert node.calls[-1] == ("unfixed",)


# ---- the web API ---------------------------------------------------------------------------------------------------------------------------


class Iface:
    def __init__(self, node):
        self.myInfo = SimpleNamespace(my_node_num=0x1234ABCD)
        self.nodes, self.localNode = {}, node

    def getMyNodeInfo(self):
        return {"user": {"id": ME, "shortName": "LORA", "longName": "LoRaPedia", "hwModel": "TBEAM"}}

    def close(self):
        pass


def api_client(tmp_path, node=None):
    node = node or FakeNode()
    opts = {"factory": lambda spec: Iface(node), "port_lister": lambda: ["/dev/ttyACM0"], "poll_s": 60}
    return TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts)), node


@pytest.fixture(autouse=True)
def no_radio_left_subscribed():
    """A radio left subscribed to pypubsub's global topics is garbage collected later and breaks whichever test publishes there next."""
    yield
    from pubsub import pub
    for topic in ("meshtastic.receive.text", "meshtastic.receive"):
        try:
            found = list(pub.getDefaultTopicMgr().getTopic(topic).getListeners())
        except Exception:
            continue  # the topic was never created: nobody subscribed
        assert found == [], topic


def wait_connected(c):
    for _ in range(300):
        if c.get("/api/radio/config").json()["connected"]:
            return
        threading.Event().wait(0.02)
    raise AssertionError("radio did not connect")


def test_the_settings_api_end_to_end_with_the_switch_off_then_on(tmp_path):
    with api_client(tmp_path)[0] as c:
        wait_connected(c)
        view = c.get("/api/radio/config").json()
        assert view["writable"] is False and not any(s in json.dumps(view) for s in SECRETS)
        r = c.put("/api/radio/config", json={"changes": {"lora": {"hop_limit": 5}}})
        assert r.status_code == 403
        assert c.post("/api/radio/config/pull").json()["backup_id"] == 1
        c.put("/api/settings", json={**c.get("/api/settings").json(), "operator_radio_config": True})
        assert c.get("/api/radio/config").json()["writable"] is True
        assert c.put("/api/radio/config", json={"changes": {"lora": {"hop_limit": 9}}}).status_code == 400
        ok = c.put("/api/radio/config", json={"changes": {"lora": {"hop_limit": 5}}})
        assert ok.status_code == 200 and ok.json()["changed"][0]["new"] == 5 and ok.json()["restarting"] is True
        backups = c.get("/api/radio/config").json()["backups"]
        assert [b["reason"] for b in backups] == ["before changes", "pulled"]
        body = c.get(f"/api/radio/config/backups/{backups[0]['id']}").json()
        assert body["config"]["lora"]["hop_limit"] == 3 and not any(s in json.dumps(body) for s in SECRETS)
        assert c.get("/api/radio/config/backups/999").status_code == 404
        back = c.post(f"/api/radio/config/backups/{backups[0]['id']}/restore", json={})
        assert back.status_code == 200 and back.json()["changed"][0]["new"] == 3
        assert c.post("/api/radio/config/backups/999/restore", json={}).status_code == 404
        assert c.put("/api/radio/config/names", json={"long_name": "Ridge", "short_name": "RDGE"}).status_code == 200
        assert c.put("/api/radio/config/names", json={"long_name": "", "short_name": "RDGE"}).status_code == 400
        assert c.put("/api/radio/config/position", json={"lat": 46.85, "lon": -121.76, "alt": 1500}).status_code == 200
        assert c.put("/api/radio/config/position", json={"lat": 99, "lon": 0}).status_code == 400
        assert c.delete("/api/radio/config/position").status_code == 200


def test_the_settings_api_without_a_radio(tmp_path):
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})) as c:
        assert c.get("/api/radio/config").json()["connected"] is False
        c.put("/api/settings", json={**c.get("/api/settings").json(), "operator_radio_config": True})
        assert c.put("/api/radio/config", json={"changes": {"lora": {"hop_limit": 5}}}).status_code == 409
        assert c.put("/api/radio/config/names", json={"long_name": "Ridge", "short_name": "RDGE"}).status_code == 409
        assert c.post("/api/radio/config/pull").status_code == 409


# ---- the AI and the mesh are kept out ---------------------------------------------------------------------------------------------------------


def test_nothing_the_ai_or_the_mesh_touches_can_reach_the_radio_settings():
    root = Path(__file__).resolve().parents[1] / "lorapedia"
    users = {p.relative_to(root).as_posix() for p in root.rglob("*.py") if "RadioConfig" in p.read_text() or "radio.config" in p.read_text() or "radio_config" in p.read_text()}
    assert users == {"api.py", "radio/config.py", "store.py", "config.py"}, users
    for module in ("engine.py", "messaging.py", "meshask.py", "memory.py", "triggers.py", "mesh/service.py", "mesh/traceroute.py", "radio/manager.py"):
        text = (root / module).read_text()
        assert "RadioConfig" not in text and "from .radio.config" not in text and "radio.config import" not in text and "setFixedPosition" not in text, module
    assert "local_node" in (root / "radio/manager.py").read_text() and (root / "api.py").read_text().count("radio.local_node()") == 1


def test_the_setting_is_off_by_default_and_the_tables_survive_a_backup(tmp_path):
    assert Settings().operator_radio_config is False
    st = Store()
    bid = st.add_radio_backup("pulled", ME, {"lora": {"hop_limit": 3}})
    st.backup_to(tmp_path / "b.db")
    fresh = Store()
    fresh.restore_from(tmp_path / "b.db")
    assert fresh.radio_backup(bid)["config"] == {"lora": {"hop_limit": 3}}
