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


async def test_rate_limit_replies_busy_with_retry_time():
    eng = make()
    assert await eng.submit(pkt("quasar")) == "queued"
    assert await eng.submit(pkt("quasar again")) == "rate_limited"
    await drain(eng)
    assert eng.radio.sent[-1].text.startswith("Busy, try again in ")


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
