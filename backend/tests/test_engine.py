import asyncio

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.engine import NOT_FOUND, Engine
from lorapedia.fitter import byte_len
from lorapedia.llm.base import MockLLM
from lorapedia.radio.base import Packet
from lorapedia.radio.simulator import SimulatorRadio
from lorapedia.retrieval.base import Chunk, DemoRetriever
from lorapedia.store import Store


class LongLLM:
    """Always returns a long answer, to exercise compression, splitting and truncation."""

    def __init__(self, text, compressed=None):
        self.text, self.compressed, self.calls = text, compressed, []

    async def generate(self, system, user, *, temperature, max_tokens):
        self.calls.append(user)
        return self.compressed if user.startswith("REWRITE") and self.compressed else self.text


def make(settings=None, llm=None, retriever=None):
    s = settings or Settings(send_delay_s=0)
    radio, sleeps, t = SimulatorRadio(), [], [0.0]

    async def sleep(d):
        sleeps.append(d)

    eng = Engine(settings=lambda: s, radio=radio, llm=llm or MockLLM(), retriever=retriever or DemoRetriever(),
                 store=Store(), clock=lambda: t[0], sleep=sleep)
    eng.sleeps, eng.t = sleeps, t
    return eng


def pkt(text, dm=True, sender="!a1b2", name="BOB"):
    return Packet(sender, name, text, dm)


async def drain(eng):
    while not eng.queue.empty():
        await eng.process(eng.queue.get_nowait())


async def test_dm_question_gets_single_reply_within_limit():
    eng = make()
    assert await eng.submit(pkt("what is a quasar?")) == "queued"
    await drain(eng)
    (sent,) = eng.radio.sent
    assert sent.to == "!a1b2" and byte_len(sent.text) <= 200
    assert "quasar" in sent.text.lower() and "[wp:Quasar]" in sent.text


async def test_sources_are_unique_even_when_several_chunks_share_an_article():
    corpus = [Chunk("Quasar", "quasar first chunk about luminous nuclei"), Chunk("Quasar", "quasar second chunk about redshift"),
              Chunk("Black hole", "a black hole powers a quasar")]
    eng = make(retriever=DemoRetriever(corpus))
    await eng.submit(pkt("quasar"))
    await drain(eng)
    assert eng.store.recent_queries(1)[0]["sources"] == ["Quasar", "Black hole"]


async def test_dm_only_mode_never_posts_publicly_even_if_a_channel_job_reaches_the_worker():
    from lorapedia.engine import Job
    eng = make(Settings(trigger_mode="dm", send_delay_s=0))
    # Bypass submit()'s filtering: e.g. the mode was switched to dm while this job sat in the queue.
    await eng.process(Job(pkt("?what is a quasar", dm=False), question="what is a quasar"))
    await eng.process(Job(pkt("?help", dm=False), reply="Ask me a question."))
    assert eng.radio.sent == []


async def test_anyone_can_dm_in_dm_mode_and_gets_a_direct_reply():
    eng = make(Settings(trigger_mode="dm", send_delay_s=0))
    for sender in ("!aaaa0001", "!bbbb0002"):
        await eng.submit(pkt("what is a quasar?", sender=sender))
    await drain(eng)
    assert [m.to for m in eng.radio.sent] == ["!aaaa0001", "!bbbb0002"]  # direct, never broadcast (None)


async def test_channel_reply_is_prefixed_and_broadcast():
    eng = make(Settings(trigger_mode="channel", send_delay_s=0))
    await eng.submit(pkt("?what is photosynthesis", dm=False))
    await drain(eng)
    (sent,) = eng.radio.sent
    assert sent.to is None and sent.text.startswith("@BOB ") and byte_len(sent.text) <= 200


async def test_ignores_own_blocked_and_unlisted_nodes():
    eng = make(Settings(blocklist=["!bad"]))
    assert await eng.submit(pkt("hi", sender=eng.radio.node_id)) == "ignored"
    assert await eng.submit(pkt("quasar", sender="!bad")) == "ignored"
    eng = make(Settings(allowlist=["!ok"]))
    assert await eng.submit(pkt("quasar", sender="!other")) == "ignored"
    assert await eng.submit(pkt("quasar", sender="!ok")) == "queued"


async def test_reject_mode_still_replies_busy_with_retry_time():
    eng = make(Settings(queue_when_limited=False))
    assert await eng.submit(pkt("quasar")) == "queued"
    assert await eng.submit(pkt("quasar again")) == "rate_limited"
    await drain(eng)
    assert eng.radio.sent[-1].text.startswith("Busy, try again in ")


async def test_a_too_soon_question_is_queued_and_answered_after_the_cooldown_not_rejected():
    eng = make(Settings(send_delay_s=0))
    assert await eng.submit(pkt("what is a quasar?")) == "queued"
    assert await eng.submit(pkt("what is photosynthesis?")) == "delayed"  # inside the 60s window
    await asyncio.sleep(0)  # let the release task run (the test's sleep returns at once)
    await drain(eng)
    texts = [m.text for m in eng.radio.sent]
    assert any("Quasar" in t for t in texts) and any("Photosynthesis" in t for t in texts)  # both answered
    assert any(t.startswith("Queued. Answer in about 60s") for t in texts)  # and the asker was told
    assert not any("Busy" in t for t in texts)
    assert 60 in eng.sleeps  # it really waited out the node's cooldown


async def test_the_cooldown_is_waited_in_order_one_window_apart():
    eng = make(Settings(send_delay_s=0, max_queued_per_node=5))
    outcomes = [await eng.submit(pkt(f"quasar {i}")) for i in range(4)]
    assert outcomes == ["queued", "delayed", "delayed", "delayed"]
    await asyncio.sleep(0)
    assert sorted(d for d in eng.sleeps if d >= 60) == [60, 120, 180]  # turns at +60s, +120s, +180s


async def test_queued_acknowledgment_can_be_turned_off():
    eng = make(Settings(send_delay_s=0, ack_queued=False))
    await eng.submit(pkt("quasar"))
    assert await eng.submit(pkt("photosynthesis")) == "delayed"
    await asyncio.sleep(0)
    await drain(eng)
    assert not any(m.text.startswith("Queued") for m in eng.radio.sent)
    assert any("Photosynthesis" in m.text for m in eng.radio.sent)  # still answered


async def test_one_node_cannot_queue_more_than_its_cap():
    eng = make(Settings(send_delay_s=0, max_queued_per_node=2))
    outcomes = [await eng.submit(pkt("quasar")) for _ in range(5)]
    assert outcomes == ["queued", "delayed", "delayed", "rate_limited", "rate_limited"]
    await asyncio.sleep(0)
    await drain(eng)
    assert any(m.text.startswith("Busy: 2 of your questions are already waiting") for m in eng.radio.sent)


async def test_other_nodes_are_not_held_up_by_someone_elses_cooldown():
    eng = make(Settings(send_delay_s=0))
    await eng.submit(pkt("quasar", sender="!aaaa0001"))
    await eng.submit(pkt("quasar", sender="!aaaa0001"))  # delayed
    assert await eng.submit(pkt("quasar", sender="!bbbb0002")) == "queued"  # a different node goes straight in


async def test_pending_count_includes_held_questions_and_stats_reports_it():
    eng = make(Settings(send_delay_s=0))
    await eng.submit(pkt("quasar"))
    await eng.submit(pkt("quasar"))
    assert eng.pending_count == eng.queue.qsize() + 1  # one held for its cooldown


async def test_the_global_queue_limit_still_applies_to_held_questions():
    eng = make(Settings(send_delay_s=0, queue_max=2, max_queued_per_node=9))
    await eng.submit(pkt("q1", sender="!aaaa0001"))
    await eng.submit(pkt("q2", sender="!aaaa0001"))
    assert await eng.submit(pkt("q3", sender="!bbbb0002")) == "queue_full"


async def test_not_found_when_nothing_retrieved():
    eng = make()
    await eng.submit(pkt("zzzz qqqq"))
    await drain(eng)
    assert eng.radio.sent[0].text == NOT_FOUND


async def test_long_answer_is_split_into_parts_up_to_admin_cap():
    text = "Quasars are bright objects powered by black holes. " * 12  # ~600 bytes
    eng = make(Settings(send_delay_s=7, max_parts=3), LongLLM(text))
    await eng.submit(pkt("quasar"))
    await drain(eng)
    texts = [s.text for s in eng.radio.sent]
    assert len(texts) == 3
    assert [t[:6] for t in texts] == ["(1/3) ", "(2/3) ", "(3/3) "]
    assert all(byte_len(t) <= 200 for t in texts)
    assert eng.sleeps == [7, 7]  # airtime delay between parts, not before the first


async def test_admin_cap_of_two_parts_is_respected():
    eng = make(Settings(send_delay_s=0, max_parts=2), LongLLM("word " * 300))
    await eng.submit(pkt("quasar"))
    await drain(eng)
    assert len(eng.radio.sent) == 2
    assert eng.radio.sent[-1].text.endswith("...")


async def test_multipart_off_gives_one_truncated_message():
    eng = make(Settings(send_delay_s=0, multipart_enabled=False), LongLLM("word " * 300))
    await eng.submit(pkt("quasar"))
    await drain(eng)
    (sent,) = eng.radio.sent
    assert byte_len(sent.text) <= 200 and sent.text.endswith("...")


async def test_llm_asked_to_compress_once_when_over_total_budget():
    llm = LongLLM("word " * 300, compressed="Short answer.")
    eng = make(Settings(send_delay_s=0), llm)
    await eng.submit(pkt("quasar"))
    await drain(eng)
    assert sum(c.startswith("REWRITE") for c in llm.calls) == 1
    assert eng.radio.sent[0].text.startswith("Short answer.")


async def test_prompt_offers_extra_budget_only_when_multipart():
    llm = LongLLM("ok")
    eng = make(Settings(send_delay_s=0), llm)
    await eng.submit(pkt("quasar"))
    await drain(eng)
    assert "Answer in at most 200 bytes. Only if essential, you may use up to" in llm.calls[0]
    llm = LongLLM("ok")
    eng = make(Settings(send_delay_s=0, multipart_enabled=False), llm)
    await eng.submit(pkt("quasar"))
    await drain(eng)
    assert "Only if essential" not in llm.calls[0]


async def test_more_command_releases_held_parts_when_auto_send_off():
    eng = make(Settings(send_delay_s=0, auto_send_parts=False), LongLLM("Quasars shine. " * 40))
    await eng.submit(pkt("quasar"))
    await drain(eng)
    assert len(eng.radio.sent) == 1 and eng.radio.sent[0].text.endswith("?more")
    await eng.submit(pkt("?more"))
    await drain(eng)
    assert eng.radio.sent[-1].text.startswith("(2/")
    assert all(byte_len(s.text) <= 200 for s in eng.radio.sent)


async def test_commands_bypass_rate_limit():
    eng = make()
    await eng.submit(pkt("quasar"))
    assert await eng.submit(pkt("?help")) == "command"
    assert await eng.submit(pkt("?stats")) == "command"


async def test_multibyte_answer_never_exceeds_limit():
    corpus = [Chunk("Cafe", "x")]
    eng = make(Settings(send_delay_s=0), LongLLM("café 日本語 \U0001f600 " * 80), DemoRetriever(corpus))
    await eng.submit(pkt("cafe"))
    await drain(eng)
    assert eng.radio.sent and all(byte_len(s.text) <= 200 for s in eng.radio.sent)


def test_api_roundtrip(tmp_path):
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder())) as client:
        assert client.get("/api/health").json()["radio"] == "simulator"
        s = client.get("/api/settings").json()
        s["max_parts"] = 2
        assert client.put("/api/settings", json=s).json()["max_parts"] == 2
        assert client.put("/api/settings", json={**s, "byte_limit": 999}).status_code == 422
        r = client.post("/api/simulator/send", json={"text": "what is a quasar?"})
        assert r.json() == {"outcome": "queued"}


# ---- AI-memory fallback ------------------------------------------------------------------------


class Scripted:
    """Answers by call order and records every call, so tests can see which prompt was used."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def generate(self, system, user, *, temperature, max_tokens):
        self.calls.append((system, user))
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def make_with_state(state, llm, settings=None, retriever=None):
    from lorapedia.engine import Engine as E
    s = settings or Settings(send_delay_s=0)
    radio = SimulatorRadio()

    async def sleep(d):
        pass

    eng = E(settings=lambda: s, radio=radio, llm=llm, retriever=retriever or DemoRetriever(), store=Store(),
            sleep=sleep, library_state=lambda: state)
    return eng


async def ask(eng, text="zzzz qqqq"):  # nothing in the demo corpus matches this
    await eng.submit(pkt(text))
    await drain(eng)
    return [m.text for m in eng.radio.sent]


async def test_fallback_answers_from_memory_with_an_unforgettable_warning_once_the_library_is_complete():
    from lorapedia.engine import FALLBACK_PREFIX, FALLBACK_SYSTEM
    llm = Scripted("Arachnophobia is an intense fear of spiders.")
    eng = make_with_state("complete", llm)
    (text,) = await ask(eng, "what is arachnophobia")
    assert text == FALLBACK_PREFIX + "Arachnophobia is an intense fear of spiders."
    assert "may be wrong" in text and byte_len(text) <= 200
    assert len(llm.calls) == 1 and llm.calls[0][0] == FALLBACK_SYSTEM  # retrieval found nothing, so only the memory call
    logged = eng.store.recent_queries(1)[0]
    assert logged["debug"]["fallback"] is True and logged["sources"] == []


async def test_fallback_runs_after_the_model_says_not_found_from_irrelevant_context():
    llm = Scripted("not found", "Canberra is the capital of Australia.")
    eng = make_with_state("complete", llm)
    (text,) = await ask(eng, "quasar")  # retrieval finds the Quasar demo chunk, but the model says it does not help
    assert text.startswith("[AI memory - may be wrong] Canberra") and len(llm.calls) == 2


async def test_fallback_is_allowed_once_every_article_is_searchable_even_if_details_are_still_arriving():
    llm = Scripted("It is a thing.")
    (text,) = await ask(make_with_state("covered", llm))
    assert text.startswith("[AI memory - may be wrong] It is a thing.")


async def test_no_fallback_while_the_library_is_still_being_indexed_because_not_found_may_just_mean_not_yet():
    llm = Scripted("this must never be asked")
    eng = make_with_state("partial", llm)
    (text,) = await ask(eng)
    assert text.startswith("Not found yet") and "indexed" in text and llm.calls == []


async def test_no_fallback_with_no_library_at_all():
    llm = Scripted("never")
    (text,) = await ask(make_with_state("none", llm))
    assert text == "Not found in my offline library." and llm.calls == []


async def test_fallback_can_be_turned_off():
    llm = Scripted("never")
    eng = make_with_state("complete", llm, Settings(send_delay_s=0, ai_fallback=False))
    (text,) = await ask(eng)
    assert text == "Not found in my offline library." and llm.calls == []


@pytest.mark.parametrize("decline", ["I don't know.", "I do not know", "I'm not sure about that", "Not found", "I cannot help", ""])
async def test_a_model_that_does_not_know_gets_plain_not_found_not_a_guess(decline):
    eng = make_with_state("complete", Scripted(decline))
    (text,) = await ask(eng)
    assert text == "Not found in my offline library."


async def test_a_library_answer_is_never_marked_as_a_guess():
    llm = Scripted("A quasar is an extremely luminous active galactic nucleus.")
    eng = make_with_state("complete", llm)
    (text,) = await ask(eng, "what is a quasar?")
    assert "[AI memory" not in text and "[wp:Quasar]" in text and len(llm.calls) == 1
    assert eng.store.recent_queries(1)[0]["debug"]["fallback"] is False


async def test_a_rambling_guess_is_cut_to_one_message_and_keeps_its_warning():
    from lorapedia.engine import FALLBACK_PREFIX
    long_guess = "caf\u00e9 \u65e5\u672c\u8a9e facts about the thing. " * 40  # ~1.5 KB, multibyte
    eng = make_with_state("complete", Scripted(long_guess), Settings(send_delay_s=0, max_parts=3))
    texts = await ask(eng)
    assert len(texts) == 1 and byte_len(texts[0]) <= 200  # never several messages of unverified text
    assert texts[0].startswith(FALLBACK_PREFIX) and texts[0].endswith("...")


async def test_the_guess_is_asked_to_fit_the_room_left_after_the_warning():
    llm = Scripted("short")
    eng = make_with_state("complete", llm, Settings(send_delay_s=0, max_parts=3))  # multi-part on: still one message of room
    await ask(eng)
    from lorapedia.engine import FALLBACK_PREFIX
    assert f"at most {200 - byte_len(FALLBACK_PREFIX)} bytes" in llm.calls[0][1]  # one message, not the multi-part budget


async def test_a_failing_model_during_the_fallback_degrades_to_not_found():
    class Boom(Scripted):
        async def generate(self, *a, **k):
            raise RuntimeError("ollama fell over")

    eng = make_with_state("complete", Boom())
    (text,) = await ask(eng)
    assert text == "Not found in my offline library."


async def test_not_found_rows_do_not_list_the_irrelevant_chunks_as_sources():
    eng = make_with_state("complete", Scripted("not found", "I don't know."))
    await ask(eng, "quasar")
    row = eng.store.recent_queries(1)[0]
    assert row["sources"] == [] and row["debug"]["chunks"]  # still inspectable in the Live Feed's debug view
