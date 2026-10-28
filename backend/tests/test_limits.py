"""Phase 7: per-node limits: a daily cap on the questions one node may ask (a default, and a number of its own), and the Limits page's data."""

import time

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.engine import Engine
from lorapedia.llm.base import MockLLM
from lorapedia.radio.base import Packet
from lorapedia.radio.simulator import SimulatorRadio
from lorapedia.retrieval.base import DemoRetriever
from lorapedia.store import Store

A, B, C = "!aaaa0001", "!bbbb0002", "!cccc0003"
DAY = 86400.0
NOON = time.mktime((2026, 10, 28, 12, 0, 0, 0, 0, -1))   # a fixed local noon, so "today" is the same whatever time the tests run


def make(store=None, wall=None, operator=None, **settings):
    s = Settings(**{"send_delay_s": 0, "rate_limit_queries": 0, **settings})
    eng = Engine(settings=lambda: s, radio=SimulatorRadio(), llm=MockLLM(), retriever=DemoRetriever(), store=store or Store(),
                 sleep=lambda d: _nap(), wall=wall or (lambda: NOON), operator=operator)
    return eng, s


async def _nap():
    return None


def dm(text, sender=A, name="N"):
    return Packet(sender, name, text, True)


def replies(eng):
    """What the engine queued to be sent, without sending it (canned replies carry no question)."""
    jobs = []
    while not eng.queue.empty():
        jobs.append(eng.queue.get_nowait())
    return jobs


async def ask(eng, text="what is a quasar?", sender=A):
    outcome = await eng.submit(dm(text, sender))
    return outcome, replies(eng)


# ---- the cap ------------------------------------------------------------------------------------------------------------------------


async def test_without_a_limit_nobody_is_stopped():
    eng, _ = make()
    assert [(await ask(eng))[0] for _ in range(30)] == ["queued"] * 30


async def test_a_node_can_ask_up_to_the_cap_then_is_told_once_and_after_that_ignored():
    eng, _ = make(daily_limit_default=2)
    assert (await ask(eng))[0] == "queued" and (await ask(eng))[0] == "queued"
    outcome, jobs = await ask(eng)
    assert outcome == "daily_limit" and len(jobs) == 1 and jobs[0].question is None
    assert jobs[0].reply == "Daily limit reached (2 questions). Try again tomorrow."
    outcome, jobs = await ask(eng)
    assert outcome == "daily_limit" and jobs == []                                      # told once: more would cost airtime too
    assert (await ask(eng, sender=B))[0] == "queued"                                    # the cap is per node


async def test_a_node_has_its_own_number_over_the_default_and_zero_means_no_limit_for_it():
    eng, _ = make(daily_limit_default=2, node_daily_limits={A: 4, B: 1, C: 0})
    assert [(await ask(eng, sender=A))[0] for _ in range(5)] == ["queued"] * 4 + ["daily_limit"]
    assert [(await ask(eng, sender=B))[0] for _ in range(2)] == ["queued", "daily_limit"]
    assert [(await ask(eng, sender=C))[0] for _ in range(9)] == ["queued"] * 9
    assert [(await ask(eng, sender="!dddd0004"))[0] for _ in range(3)] == ["queued", "queued", "daily_limit"]     # nobody else: the default


async def test_a_node_with_its_own_number_and_no_default_is_the_only_one_limited():
    eng, _ = make(node_daily_limits={A: 1})
    assert [(await ask(eng, sender=A))[0] for _ in range(2)] == ["queued", "daily_limit"]
    assert [(await ask(eng, sender=B))[0] for _ in range(5)] == ["queued"] * 5


async def test_commands_and_chat_replies_do_not_use_up_the_allowance():
    async def operator(packet, s):
        return packet.text == "see you at noon"                                         # a chat the operator started

    eng, _ = make(daily_limit_default=1, operator=operator)
    for command in ("?ping", "?stats", "?help", "?more", "?reset", "help"):
        assert (await ask(eng, command))[0] == "command"
    assert (await ask(eng, "see you at noon"))[0] == "operator"
    assert (await ask(eng))[0] == "queued"                                              # still has its one question
    assert (await ask(eng))[0] == "daily_limit"


async def test_a_node_that_has_reached_its_limit_can_still_use_the_commands():
    eng, _ = make(daily_limit_default=1)
    assert (await ask(eng))[0] == "queued" and (await ask(eng))[0] == "daily_limit"
    for command in ("?ping", "?stats", "?help", "?reset"):
        assert (await ask(eng, command))[0] == "command"                                 # a range test costs no model time


async def test_a_question_about_the_mesh_counts_like_any_other():
    eng, _ = make(daily_limit_default=1)
    assert (await ask(eng, "how many nodes are around?"))[0] == "queued"
    assert (await ask(eng, "what is a quasar?"))[0] == "daily_limit"


async def test_a_question_turned_away_for_another_reason_is_not_counted():
    eng, s = make(daily_limit_default=2, queue_max=1)
    assert (await eng.submit(dm("one"))) == "queued"                                    # fills the one-place queue
    assert (await eng.submit(dm("two", sender=B))) == "queue_full"                      # busy: B's question was not taken
    replies(eng)
    assert (await ask(eng, sender=B))[0] == "queued" and (await ask(eng, sender=B))[0] == "queued" and (await ask(eng, sender=B))[0] == "daily_limit"


async def test_a_question_held_for_its_turn_counts_when_it_is_accepted():
    eng, _ = make(Store(), daily_limit_default=2, rate_limit_queries=1, rate_limit_window_s=60, queue_when_limited=True, ack_queued=False)
    assert await eng.submit(dm("one")) == "queued"
    assert await eng.submit(dm("two")) == "delayed"                                     # waits out the cooldown, but is accepted now
    assert await eng.submit(dm("three")) == "daily_limit"
    for task in list(eng._delayed):
        task.cancel()


async def test_a_public_message_or_a_blocked_node_gets_no_notice_and_uses_nothing():
    eng, _ = make(daily_limit_default=1, blocklist=[B])
    assert await eng.submit(Packet(A, "N", "what is a quasar?", False)) == "ignored"
    assert (await ask(eng, sender=B)) == ("ignored", [])
    assert (await ask(eng))[0] == "queued"                                              # A still has its allowance


async def test_the_allowance_survives_a_restart_because_it_is_read_from_the_question_log():
    store = Store()
    eng, _ = make(store, daily_limit_default=2)
    for _ in range(2):
        assert (await ask(eng))[0] == "queued"
        store.log_query(sender_id=A, sender_name="N", question="q", parts=["a"], byte_limit=200, latency_s=1.0, sources=[], debug={})
    fresh, _ = make(store, daily_limit_default=2, wall=time.time)                       # a new engine: it knows nothing but the log
    assert (await fresh.submit(dm("again"))) == "daily_limit"


async def test_the_allowance_starts_over_with_the_next_day_and_the_notice_can_be_sent_again():
    now = [NOON]
    eng, _ = make(daily_limit_default=1, wall=lambda: now[0])
    assert (await ask(eng))[0] == "queued"
    assert (await ask(eng))[1][0].reply.startswith("Daily limit reached")
    assert (await ask(eng))[1] == []
    now[0] = NOON + DAY
    assert (await ask(eng))[0] == "queued"
    assert (await ask(eng))[1][0].reply.startswith("Daily limit reached")               # a new day: told again, once


async def test_a_new_cap_applies_at_once_in_either_direction():
    eng, s = make(daily_limit_default=1)
    assert (await ask(eng))[0] == "queued" and (await ask(eng))[0] == "daily_limit"
    s.daily_limit_default = 3                                                           # the settings object is shared, as in the app
    assert (await ask(eng))[0] == "queued" and (await ask(eng))[0] == "queued" and (await ask(eng))[0] == "daily_limit"
    s.node_daily_limits = {A: 0}
    assert (await ask(eng))[0] == "queued"


# ---- the settings ----------------------------------------------------------------------------------------------------------------------


def test_the_settings_are_checked_and_the_effective_cap_is_worked_out():
    s = Settings(daily_limit_default=5, node_daily_limits={A: 0, B: 9})
    assert (s.daily_cap(A), s.daily_cap(B), s.daily_cap(C)) == (0, 9, 5) and Settings().daily_cap(A) == 0
    for bad in ({"daily_limit_default": -1}, {"daily_limit_default": 1001}, {"node_daily_limits": {"bob": 3}}, {"node_daily_limits": {"!AAAA0001": 3}},
                {"node_daily_limits": {A: -1}}, {"node_daily_limits": {A: 1001}}, {"node_daily_limits": {"!ffff": 1}}):
        with pytest.raises(ValueError):
            Settings(**bad)
    assert Settings.model_validate_json(Settings(node_daily_limits={A: 4}).model_dump_json()).node_daily_limits == {A: 4}


# ---- counting questions ------------------------------------------------------------------------------------------------------------------


def log(store, sender, name, ts):
    store.log_query(sender_id=sender, sender_name=name, question="q", parts=["a"], byte_limit=200, latency_s=1.0, sources=[], debug={})
    store._db.execute("UPDATE queries SET ts=? WHERE id=(SELECT MAX(id) FROM queries)", (ts,))
    store._db.commit()


def test_questions_are_counted_per_sender_and_the_newest_name_is_kept():
    st = Store()
    log(st, A, "OLD", NOON - 2 * DAY)
    log(st, A, "OLD", NOON - 100)
    log(st, A, "NEW", NOON - 10)
    log(st, B, "BOB", NOON - 5)
    midnight = NOON - 12 * 3600
    assert st.question_counts() == {A: 3, B: 1} and st.question_counts(midnight) == {A: 2, B: 1} and st.question_counts(NOON + 1) == {}
    stats = {r["sender_id"]: r for r in st.sender_stats(midnight)}
    assert (stats[A]["total"], stats[A]["today"], stats[A]["name"], stats[A]["last_ts"]) == (3, 2, "NEW", NOON - 10)
    assert stats[B]["today"] == 1 and Store().sender_stats(0) == []
    assert {r["sender_id"]: r["today"] for r in st.sender_stats(NOON + 1)} == {A: 0, B: 0}


# ---- the Limits page's data and the app ---------------------------------------------------------------------------------------------------


def app_client(tmp_path):
    return TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60}))


def put(c, **kw):
    r = c.put("/api/settings", json={**c.get("/api/settings").json(), **kw})
    assert r.status_code == 200, r.text


def test_the_limits_api_lists_everyone_known_with_their_counts_and_limits(tmp_path):
    with app_client(tmp_path) as c:
        c.post("/api/mesh/demo")
        put(c, rate_limit_queries=0, daily_limit_default=3, node_daily_limits={"!5d000002": 10, "!99999999": 0})
        for _ in range(2):
            c.post("/api/simulator/send", json={"sender_id": "!5d000002", "sender_name": "LDGE", "text": "what is a quasar?"})
        for _ in range(100):
            if len(c.get("/api/queries").json()) >= 2:
                break
            time.sleep(0.02)
        put(c, blocklist=["!5d000003", "!88888888"], allowlist=["!5d000001"])                  # after the asking: an allow list would have turned them away
        d = c.get("/api/limits").json()
        by = {n["id"]: n for n in d["nodes"]}
        assert d["default_cap"] == 3 and d["allowlist_active"] is True
        assert d["nodes"][0]["id"] == "!5d000002"                                              # the one that asked today comes first
        lodge = by["!5d000002"]
        assert (lodge["asked_today"], lodge["asked_total"], lodge["custom_cap"], lodge["cap"], lodge["name"]) == (2, 2, 10, 10, "Demo Lodge")
        assert by["!5d000001"]["allowed"] is True and by["!5d000001"]["cap"] == 3 and by["!5d000001"]["custom_cap"] is None
        assert by["!5d000003"]["blocked"] is True and by["!88888888"]["blocked"] is True and by["!88888888"]["name"] == "!88888888"
        assert by["!99999999"]["custom_cap"] == 0 and by["!99999999"]["cap"] == 0
        assert len(d["nodes"]) == len({n["id"] for n in d["nodes"]}) and "!00510000" not in by  # no duplicates, and not this radio


def test_the_app_stops_a_node_at_its_daily_limit_and_says_so(tmp_path):
    with app_client(tmp_path) as c:
        put(c, rate_limit_queries=0, daily_limit_default=1)
        outcomes = [c.post("/api/simulator/send", json={"sender_id": "!5d0000aa", "text": "what is a quasar?"}).json()["outcome"] for _ in range(3)]
        assert outcomes == ["queued", "daily_limit", "daily_limit"]
        for _ in range(100):
            if len(c.get("/api/simulator/outbox").json()) >= 2:
                break
            time.sleep(0.02)
        texts = [m["text"] for m in c.get("/api/simulator/outbox").json()]
        assert sum(t.startswith("Daily limit reached (1 questions)") for t in texts) == 1 and len(c.get("/api/queries").json()) == 1
        assert c.put("/api/settings", json={**c.get("/api/settings").json(), "node_daily_limits": {"nope": 1}}).status_code == 422


async def test_the_engine_reports_what_it_accepted_today_even_before_it_is_logged_and_forgets_it_tomorrow():
    now = [NOON]
    eng, _ = make(daily_limit_default=5, wall=lambda: now[0])
    await ask(eng)
    await ask(eng)
    await ask(eng, sender=B)
    assert eng.quota_today() == {A: 2, B: 1} and eng.store.question_counts() == {}      # accepted, none answered or logged yet
    now[0] = NOON + DAY
    assert eng.quota_today() == {}


def test_a_question_that_failed_still_shows_against_the_nodes_allowance_on_the_limits_page(tmp_path):
    class Down:
        async def generate(self, *a, **k):
            raise RuntimeError("the model is not running")

    with TestClient(create_app(str(tmp_path), llm=Down(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})) as c:
        put(c, rate_limit_queries=0, daily_limit_default=1)
        assert c.post("/api/simulator/send", json={"sender_id": "!5d0000bb", "text": "what is a quasar?"}).json()["outcome"] == "queued"
        for _ in range(100):
            if c.get("/api/simulator/outbox").json():
                break
            time.sleep(0.02)
        assert c.get("/api/queries").json() == []                                       # the answer failed, so nothing was logged...
        assert c.post("/api/simulator/send", json={"sender_id": "!5d0000bb", "text": "again"}).json()["outcome"] == "daily_limit"    # ...but it was used up
        row = next(n for n in c.get("/api/limits").json()["nodes"] if n["id"] == "!5d0000bb")
        assert (row["asked_today"], row["asked_total"], row["cap"]) == (1, 0, 1)
