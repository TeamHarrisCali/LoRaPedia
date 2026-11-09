"""Clearing the question log, like clearing browser history: the last N hours, everything, or one question."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.store import Store

HOUR = 3600.0
A, B = "!aaaa0001", "!bbbb0002"


def add(store: Store, age_h: float, sender: str = A, question: str = "what is a quasar?") -> int:
    """Log a question and make it `age_h` hours old."""
    qid = store.log_query(sender_id=sender, sender_name="N", question=question, parts=["An answer."], byte_limit=200, latency_s=0.1, sources=[], debug={})
    store._db.execute("UPDATE queries SET ts=? WHERE id=?", (time.time() - age_h * HOUR, qid))
    store._db.commit()
    return qid


def recv(ws, wait: float = 5.0) -> dict:
    """The next live event, or a failed test (not a test that hangs for ever) when none arrives."""
    box: list = []
    t = threading.Thread(target=lambda: box.append(ws.receive_json()), daemon=True)
    t.start()
    t.join(wait)
    assert box, "no live event arrived"
    return box[0]


def ids(store: Store) -> list[int]:
    return sorted(r["id"] for r in store.recent_queries(1000))


# ---- the store ------------------------------------------------------------------------------------------------------------------------


def test_count_matches_what_a_clear_of_the_same_range_removes():
    s = Store()
    for age in (0.2, 0.9, 3, 30, 200):
        add(s, age)
    since_1h, since_1d = time.time() - HOUR, time.time() - 24 * HOUR
    assert (s.count_queries(since_1h), s.count_queries(since_1d), s.count_queries(None)) == (2, 3, 5)
    assert s.delete_queries(since_1d)["deleted"] == 3 and s.count_queries(None) == 2


def test_clearing_a_range_keeps_everything_older_and_reports_the_newest_it_removed():
    s = Store()
    old = [add(s, 30), add(s, 200)]
    new = [add(s, 0.5), add(s, 2)]
    r = s.delete_queries(time.time() - 3 * HOUR)
    assert r["deleted"] == 2 and r["newest"] == pytest.approx(time.time() - 0.5 * HOUR, abs=5)
    assert ids(s) == sorted(old) and not set(new) & set(ids(s))


def test_clearing_everything_and_clearing_nothing():
    s = Store()
    assert s.delete_queries(None) == {"deleted": 0, "newest": None}               # an empty log is fine
    add(s, 1), add(s, 500)
    assert s.delete_queries(None)["deleted"] == 2 and s.count_queries(None) == 0
    assert s.delete_queries(time.time() - HOUR) == {"deleted": 0, "newest": None}


def test_the_delivery_records_of_a_deleted_answer_go_with_it_and_the_others_stay():
    s = Store()
    gone, kept = add(s, 0.5), add(s, 40)
    s.delivery_sent(101, A, 0, query_id=gone)
    s.delivery_sent(102, A, 1, query_id=gone)
    s.delivery_sent(103, A, 0, query_id=kept)
    s.delivery_sent(104, B, 0, query_id=None)                                       # a message you typed yourself: not a question
    s.delete_queries(time.time() - 24 * HOUR)
    left = {r["packet_id"] for r in s._db.execute("SELECT packet_id FROM deliveries")}
    assert left == {103, 104}


def test_deleting_one_question_leaves_the_rest_and_reports_whose_it_was():
    s = Store()
    a, b = add(s, 1, A), add(s, 2, B)
    s.delivery_sent(7, A, 0, query_id=a)
    out = s.delete_query(a)
    assert out["sender_id"] == A and out["ts"] == pytest.approx(time.time() - HOUR, abs=5)
    assert ids(s) == [b] and s._db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
    assert s.delete_query(a) is None and s.delete_query(99999) is None


def test_a_deleted_question_is_gone_from_the_file_not_just_from_the_list(tmp_path):
    secret = "zebra-quartz-7731-unmistakable"
    path = tmp_path / "q.db"
    s = Store(path)
    add(s, 1, question=f"tell me about {secret}")
    add(s, 2, question="what is rain?")
    assert secret.encode() in path.read_bytes()
    s.delete_queries(None)
    assert secret.encode() not in path.read_bytes()
    one = Store(tmp_path / "one.db")
    qid = add(one, 1, question=f"and again {secret}")
    add(one, 2, question="what is snow?")
    one.delete_query(qid)
    assert secret.encode() not in (tmp_path / "one.db").read_bytes()               # a single delete overwrites it too (secure_delete)


def test_a_big_clear_gives_the_space_back(tmp_path):
    path = tmp_path / "big.db"
    s = Store(path)
    for i in range(300):
        s.log_query(sender_id=A, sender_name="N", question=f"question number {i}", parts=["x" * 150], byte_limit=200, latency_s=0.1, sources=[], debug={"prompt": "p" * 4000})
    before = path.stat().st_size
    s.delete_queries(None)
    assert path.stat().st_size < before / 3


# ---- the web API ----------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    with TestClient(app) as c:
        c.store = app.state.engine.store
        c.engine = app.state.engine
        yield c


def test_count_and_clear_by_hours_or_all(client):
    for age in (0.3, 2, 50):
        add(client.store, age)
    assert client.get("/api/queries/count?hours=1").json() == {"count": 1}
    assert client.get("/api/queries/count?hours=24").json() == {"count": 2}
    assert client.get("/api/queries/count?all=true").json() == {"count": 3}
    assert client.delete("/api/queries?hours=24").json() == {"deleted": 2}
    assert [q["id"] for q in client.get("/api/queries").json()] == [ids(client.store)[0]] and client.get("/api/queries/count?all=true").json() == {"count": 1}
    assert client.delete("/api/queries?all=true").json() == {"deleted": 1}
    assert client.get("/api/queries").json() == []


@pytest.mark.parametrize("query", ["", "?hours=0", "?hours=-5", "?hours=999999", "?hours=2&all=true", "?all=false", "?hours=abc"])
def test_a_clear_must_say_exactly_how_much(client, query):
    add(client.store, 1)
    r = client.delete(f"/api/queries{query}")
    assert r.status_code in (400, 422) and client.store.count_queries(None) == 1       # nothing is deleted by an unclear request
    assert client.get(f"/api/queries/count{query}").status_code in (400, 422)


def test_one_question_can_be_deleted_and_a_missing_one_is_a_404(client):
    a, b = add(client.store, 1), add(client.store, 2)
    assert client.delete(f"/api/queries/{a}").json() == {"deleted": 1}
    assert ids(client.store) == [b]
    assert client.delete(f"/api/queries/{a}").status_code == 404


def test_only_delete_can_clear_so_another_web_page_cannot_do_it(client):
    add(client.store, 1)
    for method in ("post", "put", "patch"):
        assert getattr(client, method)("/api/queries?all=true").status_code == 405
        assert getattr(client, method)("/api/queries/1").status_code in (404, 405)
    assert client.get("/api/queries?all=true").status_code == 200 and client.get("/api/queries/1").status_code in (404, 405)   # 404 when the built UI answers
    assert client.store.count_queries(None) == 1 and "access-control-allow-origin" not in client.get("/api/queries").headers


def test_clearing_forgets_the_follow_up_memory_that_is_made_of_those_questions(client):
    client.engine.memory.remember(A, "what is the capital of japan?", "Tokyo.", 3600)
    client.engine.memory.remember(B, "what is a quasar?", "A bright galactic core.", 3600)
    add(client.store, 1, A), add(client.store, 1, B)
    assert client.get("/api/memory").json()["people"] == 2
    client.delete("/api/queries?hours=2")
    assert client.get("/api/memory").json()["people"] == 0
    client.engine.memory.remember(A, "q", "a", 3600), client.engine.memory.remember(B, "q", "a", 3600)
    only_a = add(client.store, 1, A)
    add(client.store, 1, B)
    client.delete(f"/api/queries/{only_a}")                                              # one question: only that person's follow-ups
    assert client.engine.memory.recall(A, 3600) == [] and client.engine.memory.recall(B, 3600) != []


def test_clearing_nothing_does_not_touch_the_memory(client):
    client.engine.memory.remember(A, "q", "a", 3600)
    assert client.delete("/api/queries?all=true").json() == {"deleted": 0}
    assert client.get("/api/memory").json()["people"] == 1


def test_clearing_todays_questions_gives_back_todays_daily_allowance(client):
    client.put("/api/settings", json={"daily_limit_default": 2, "rate_limit_queries": 0, "send_delay_s": 0})
    add(client.store, 0.1), add(client.store, 0.2)                                       # two questions from A, today
    sim = lambda: client.post("/api/simulator/send", json={"sender_id": A, "sender_name": "N", "text": "what is a quasar?"}).json()["outcome"]
    assert sim() == "daily_limit"
    client.delete("/api/queries?all=true")
    assert sim() == "queued"


def test_clearing_only_old_questions_leaves_todays_allowance_alone(client):
    client.put("/api/settings", json={"daily_limit_default": 2, "rate_limit_queries": 0, "send_delay_s": 0})
    add(client.store, 0.05), add(client.store, 0.06)
    sim = lambda: client.post("/api/simulator/send", json={"sender_id": A, "sender_name": "N", "text": "what is a quasar?"}).json()["outcome"]
    assert sim() == "daily_limit"
    add(client.store, 24 * 40, B)
    client.delete(f"/api/queries?hours={24 * 60}")                                       # reaches back 60 days, so it takes today's too
    assert sim() == "queued"
    add(client.store, 0.05), add(client.store, 0.06)
    client.engine.forget_counts()
    assert sim() == "daily_limit"
    old = add(client.store, 24 * 5, A)
    client.delete(f"/api/queries/{old}")                                                 # an old question: today's count is not reset
    assert sim() == "daily_limit"


def test_open_pages_are_told_so_they_can_drop_what_they_show(client):
    a, b = add(client.store, 1), add(client.store, 2)
    with client.websocket_connect("/ws") as ws:
        client.delete(f"/api/queries/{a}")
        assert recv(ws) == {"type": "cleared", "ids": [a]}
        before = time.time()
        client.delete("/api/queries?hours=5")
        ev = recv(ws)
        assert ev["type"] == "cleared" and "ids" not in ev and before - 5 * HOUR - 5 < ev["since"] < before - 5 * HOUR + 5
        add(client.store, 1)
        client.delete("/api/queries?all=true")
        assert recv(ws) == {"type": "cleared", "since": 0}
        client.delete("/api/queries?all=true")                                           # nothing left: nothing to announce
        client.delete(f"/api/queries/{b}")                                               # (already gone: a 404, no event)


def test_the_csv_export_and_the_limits_page_no_longer_show_cleared_questions(client):
    add(client.store, 1, A, question="remove me please"), add(client.store, 100, B, question="keep me")
    client.delete("/api/queries?hours=24")
    csv = client.get("/api/queries/export.csv").text
    assert "remove me please" not in csv and "keep me" in csv
    people = {p["id"]: p for p in client.get("/api/limits").json()["nodes"]}
    assert A not in people or people[A]["total"] == 0


def test_the_in_memory_daily_counts_are_reset_only_when_something_from_today_was_deleted(client):
    eng = client.engine
    eng._roll_day()
    eng._quota[A] = 5                                                                   # five accepted today, some never logged (a failed answer)
    old = add(client.store, 24 * 5, A)
    client.delete(f"/api/queries/{old}")
    assert eng.quota_today()[A] == 5                                                    # an old question: today's count is left alone
    add(client.store, 24 * 6, B)
    client.delete("/api/queries?hours=2000")                                            # reaches back far but holds nothing from today
    assert eng.quota_today()[A] == 5
    today = add(client.store, 0.1, A)
    client.delete(f"/api/queries/{today}")
    assert A not in eng.quota_today()                                                   # something from today went: counts are read again from the log
    eng._quota[A] = 5
    add(client.store, 0.1, B)
    client.delete("/api/queries?hours=1")
    assert eng.quota_today() == {}
