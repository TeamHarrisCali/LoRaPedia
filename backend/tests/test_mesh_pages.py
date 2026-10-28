"""The queries and endpoints behind the Map, Telemetry and Trends pages."""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.mesh import sim
from lorapedia.mesh.observe import csv_cell
from lorapedia.mesh.store import METRICS, MeshStore, _thin

NOW = 1_800_000_000.0
A, B, C = "!aaaa0001", "!bbbb0002", "!cccc0003"


def write(store, **kw):
    base = dict(nodes={}, packets={}, talkers={}, links={}, hops={}, telemetry=[], positions=[])
    store.write(**{**base, **kw})


def tel(node, ts, kind="device", **m):
    return {"ts": ts, "node_id": node, "kind": kind, **m}


# ---- the queries -------------------------------------------------------------------------------------------------------


def test_thinning_keeps_the_first_and_last_point_and_spreads_the_rest():
    pts = list(range(1000))
    out = _thin(pts, 50)
    assert len(out) == 50 and out[0] == 0 and out[-1] == 999 and out == sorted(out)
    assert _thin([1, 2, 3], 50) == [1, 2, 3]


def test_a_trail_needs_at_least_two_points_and_respects_the_window_and_the_limit():
    s = MeshStore()
    write(s, positions=[{"node_id": A, "ts": NOW - i * 600, "lat": 46.0 + i * 0.001, "lon": -121.0} for i in range(300)]
          + [{"node_id": B, "ts": NOW, "lat": 1.0, "lon": 1.0}]                      # one point: not a trail
          + [{"node_id": C, "ts": NOW - 40 * 86400, "lat": 2.0, "lon": 2.0}, {"node_id": C, "ts": NOW - 39 * 86400, "lat": 2.1, "lon": 2.0}])
    trails = s.trails(NOW - 7 * 86400, per_node=100)
    assert set(trails) == {A} and len(trails[A]) == 100
    assert trails[A][0][2] < trails[A][-1][2]                      # oldest first
    assert all(len(p) == 3 for p in trails[A])


def test_a_series_is_per_node_thinned_and_the_metric_is_checked():
    s = MeshStore()
    write(s, telemetry=[tel(A, NOW - i * 60, battery=100 - i * 0.1) for i in range(500)] + [tel(B, NOW - 10, battery=50.0)]
          + [tel(B, NOW - 5, "environment", temperature=20.0)])
    out = s.series("battery", NOW - 86400, max_points=100)
    assert set(out) == {A, B} and len(out[A]) == 100 and out[B] == [[NOW - 10, 50.0]]
    assert out[A][0][0] < out[A][-1][0]
    assert set(s.series("battery", NOW - 86400, [B])) == {B}
    assert set(s.series("temperature", NOW - 86400)) == {B}       # only rows that carry the metric
    for bad in ("nope", "battery; DROP TABLE nodes", "ts", ""):
        with pytest.raises(ValueError):
            s.series(bad, NOW - 86400)
    assert s._db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 502  # nothing was injected or dropped by those names
    assert set(METRICS) >= {"battery", "temperature"}


def test_readings_carry_the_nodes_name_or_your_label_newest_first():
    s = MeshStore()
    write(s, nodes={A: {"last_seen": NOW, "name": "Ridge", "short": "RDG"}}, telemetry=[tel(A, NOW - 100, battery=80.0), tel(A, NOW - 50, battery=79.0), tel(B, NOW - 10, battery=5.0)])
    s.set_note(A, "My ridge", None)
    rows = s.readings(NOW - 1000)
    assert [r["ts"] for r in rows] == [NOW - 10, NOW - 50, NOW - 100]
    assert rows[1]["node_name"] == "Ridge" and rows[1]["node_label"] == "My ridge" and rows[0]["node_name"] is None
    assert [r["node_id"] for r in s.readings(NOW - 1000, node_id=A)] == [A, A] and len(s.readings(NOW - 1000, limit=1)) == 1


def test_sensor_averages_use_only_environment_readings_and_each_nodes_own_average():
    s = MeshStore()
    write(s, telemetry=[tel(A, NOW - 10 * i, "environment", temperature=10.0 + i, humidity=50.0) for i in range(10)]   # 10 readings
          + [tel(B, NOW - 5, "environment", temperature=30.0)]                                                         # 1 reading
          + [tel(C, NOW - 5, "device", battery=50.0, voltage=3.9)])
    rows = {r["node_id"]: r for r in s.sensor_averages(NOW - 3600)}
    assert set(rows) == {A, B} and rows[A]["n"] == 10 and rows[A]["temperature"] == pytest.approx(14.5) and rows[B]["temperature"] == 30.0
    assert s.sensor_averages(NOW + 10) == []


def test_packet_and_hop_hours():
    s = MeshStore()
    write(s, packets={(100, "TEXT_MESSAGE_APP"): 2, (100, "TELEMETRY_APP"): 5, (101, "TELEMETRY_APP"): 1, (50, "X"): 9},
          hops={(100, 0): 3, (100, 2): 1, (101, 1): 4, (40, 0): 9})
    assert s.packet_hours(60) == [[100, 7], [101, 1]] and s.packet_hours(60, "TELEMETRY_APP") == [[100, 5], [101, 1]]
    assert s.packet_kinds(60) == [["TELEMETRY_APP", 6], ["TEXT_MESSAGE_APP", 2]]
    assert s.hop_hours(60) == [[100, 0, 3], [100, 2, 1], [101, 1, 4]]


@pytest.mark.parametrize("value,safe", [("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2+3", "'-2+3"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"),
                                        ("\rx", "'\rx"), ("normal", "normal"), ("a=b", "a=b"), (5, 5), (-5, -5), (None, None), ("", "")])
def test_csv_cells_cannot_become_spreadsheet_formulas(value, safe):
    assert csv_cell(value) == safe


# ---- the endpoints -------------------------------------------------------------------------------------------------------


def client(tmp_path):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    c = TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts))
    c.post("/api/mesh/demo")
    return c


def test_api_trails_show_only_the_node_that_moved(tmp_path):
    c = client(tmp_path)
    t = c.get("/api/mesh/trails?days=7").json()["trails"]
    assert set(t) == {"!5d000008"} and len(t["!5d000008"]) > 3 and len(t["!5d000008"][0]) == 3  # the demo truck
    assert len(c.get("/api/mesh/trails?max_points=10").json()["trails"]["!5d000008"]) <= 10
    assert c.get("/api/mesh/trails?days=9999&max_points=1").status_code == 200     # out-of-range values are clamped, not errors


def test_api_series_defaults_to_the_most_recent_reporters_and_validates_its_input(tmp_path):
    c = client(tmp_path)
    r = c.get("/api/mesh/telemetry/series?metric=battery&hours=24").json()
    assert r["metric"] == "battery" and len(r["series"]) == len(sim.NODES) and all(len(v) > 5 for v in r["series"].values())
    one = c.get("/api/mesh/telemetry/series?metric=temperature&nodes=!5d000003&hours=24").json()["series"]
    assert set(one) == {"!5d000003"}
    for bad in ("metric=ts", "metric=battery;--", "metric=", "metric=battery&nodes=!zzzz", "metric=battery&nodes=bob",
                "metric=battery&nodes=" + ",".join(f"!5d00000{i}" for i in range(1, 9)) + ",!5d000001,!5d000002,!5d000003,!5d000004,!5d000005"):
        assert c.get(f"/api/mesh/telemetry/series?{bad}").status_code == 400, bad


def test_api_without_asking_for_nodes_at_most_eight_are_returned(tmp_path):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    c = TestClient(app)
    write(app.state.mesh_store, telemetry=[tel(f"!aaaa00{i:02x}", app.state.mesh._clock() - 10 - i, battery=50.0) for i in range(12)])
    r = c.get("/api/mesh/telemetry/series?metric=battery").json()["series"]
    assert len(r) == 8 and "!aaaa0000" in r and "!aaaa000b" not in r                      # the eight that spoke most recently


def test_api_readings_and_their_validation(tmp_path):
    c = client(tmp_path)
    r = c.get("/api/mesh/telemetry/readings?hours=24&limit=5").json()["readings"]
    assert len(r) == 5 and r[0]["ts"] >= r[-1]["ts"] and "battery" in r[0]
    assert all(x["node_id"] == "!5d000001" for x in c.get("/api/mesh/telemetry/readings?node=!5d000001").json()["readings"])
    assert c.get("/api/mesh/telemetry/readings?node=bob").status_code == 404
    assert len(c.get("/api/mesh/telemetry/readings?limit=999999").json()["readings"]) <= 1000


def test_api_csv_export_is_oldest_first_complete_and_safe_to_open_in_a_spreadsheet(tmp_path):
    c = client(tmp_path)
    c.put("/api/mesh/nodes/!5d000001/note", json={"label": "=HYPERLINK(\"http://evil\")"})
    r = c.get("/api/mesh/telemetry/export.csv?days=2")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:4] == ["time", "node_id", "node_name", "kind"] and len(rows) > 100
    times = [x[0] for x in rows[1:]]
    assert times == sorted(times)
    names = {x[2] for x in rows[1:] if x[1] == "!5d000001"}
    assert names == {"'=HYPERLINK(\"http://evil\")"}  # the label is inert text
    assert not any(cell[:1] in "=+-@" for x in rows[1:] for cell in x[:4] if cell)


def test_api_the_question_log_csv_is_protected_too(tmp_path):
    """Questions and names come from strangers, and this export is opened in spreadsheets."""
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    c = TestClient(app)
    app.state.engine.store.log_query(sender_id="!a1b2c3d4", sender_name="=cmd|' /c calc'!A0", question="=1+1", parts=["+SUM(1)"], byte_limit=200,
                                     latency_s=1.0, sources=["@Source"], debug={})
    rows = list(csv.reader(io.StringIO(c.get("/api/queries/export.csv").text)))
    assert rows[1][2] == "'=cmd|' /c calc'!A0" and rows[1][3] == "'=1+1" and rows[1][4] == "'+SUM(1)" and rows[1][9] == "'@Source"


def test_api_sensors_average_each_nodes_own_average_so_a_chatty_node_does_not_outweigh_a_quiet_one(tmp_path):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    c = TestClient(app)
    now = app.state.mesh._clock()
    write(app.state.mesh_store, telemetry=[tel(A, now - 10 * i, "environment", temperature=10.0, humidity=40.0) for i in range(20)]
          + [tel(B, now - 5, "environment", temperature=30.0, pressure=1000.0)])
    r = c.get("/api/mesh/sensors?hours=1").json()
    assert r["average"]["temperature"] == 20.0 and r["average"]["humidity"] == 40.0 and r["average"]["pressure"] == 1000.0  # not (200+30)/21
    assert {n["id"]: n["readings"] for n in r["nodes"]} == {A: 20, B: 1}
    assert c.get("/api/mesh/sensors?hours=0").json()["hours"] == 0.25  # the window is clamped


def test_api_sensors_with_nothing_heard_say_nothing_rather_than_zero(tmp_path):
    c = TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60}))
    r = c.get("/api/mesh/sensors").json()
    assert r["average"] == {"temperature": None, "humidity": None, "pressure": None} and r["nodes"] == []


def test_api_trends(tmp_path):
    c = client(tmp_path)
    p = c.get("/api/mesh/trends/packets?days=2").json()
    assert p["hours"] and all(len(h) == 2 for h in p["hours"]) and {k for k, _ in p["kinds"]} >= {"TELEMETRY_APP"}
    only = c.get("/api/mesh/trends/packets?days=2&kind=TEXT_MESSAGE_APP").json()["hours"]
    assert sum(n for _, n in only) < sum(n for _, n in p["hours"])
    h = c.get("/api/mesh/trends/hops?days=2").json()
    assert h["summary"]["packets"] > 0 and 0 <= h["summary"]["share_direct"] <= 1 and h["summary"]["furthest"] == 3


def test_api_hop_summary_math(tmp_path):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    c = TestClient(app)
    hour = int(app.state.mesh._clock() // 3600)
    write(app.state.mesh_store, hops={(hour, 0): 6, (hour, 1): 2, (hour, 3): 2})
    s = c.get("/api/mesh/trends/hops?days=1").json()["summary"]
    assert s == {"packets": 10, "average_relays": 0.8, "share_direct": 0.6, "furthest": 3}
    empty = TestClient(create_app(str(tmp_path / "e"), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60}))
    assert empty.get("/api/mesh/trends/hops").json()["summary"] == {"packets": 0, "average_relays": None, "share_direct": None, "furthest": None}


def test_the_unit_settings_default_to_metric_and_reject_anything_else():
    s = Settings()
    assert (s.temperature_unit, s.distance_unit) == ("C", "km")
    assert Settings.model_validate_json('{"temperature_unit": "F", "distance_unit": "mi"}').temperature_unit == "F"
    with pytest.raises(ValueError):
        Settings(temperature_unit="K")
