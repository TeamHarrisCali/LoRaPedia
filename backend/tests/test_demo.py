from fastapi.testclient import TestClient

from lorapedia import demo
from lorapedia.api import create_app
from lorapedia.embed import HashEmbedder
from lorapedia.fitter import byte_len
from lorapedia.llm.base import MockLLM
from lorapedia.store import Store


def test_demo_answers_fit_the_limit():
    assert all(byte_len(a) <= 200 for _, _, _, a, _, _ in demo.DEMO)


def test_seed_spreads_queries_over_24h():
    store = Store()
    assert demo.seed(store, 36) == 36
    hours = store.activity_24h()
    assert sum(hours) == 36 and sum(1 for h in hours if h) >= 8  # a visible shape, not one spike


def test_api_sample_zim_demo_and_defaults(tmp_path):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts)) as c:
        lib = c.post("/api/kb/sample").json()
        assert lib["name"] == "sample.zim" and lib["articles"] == 12
        assert c.post("/api/kb/sample").status_code == 200  # idempotent: safe to click twice
        assert c.post("/api/demo/seed").json()["added"] == 36
        assert len(c.get("/api/queries", params={"limit": 100}).json()) == 36
        d = c.get("/api/settings/defaults").json()
        assert d["byte_limit"] == 200 and "ONLY the provided context" in d["system_prompt"]


def test_stored_queries_have_the_same_shape_as_live_events():
    store = Store()
    store.log_query(sender_id="!a", sender_name="A", question="q", parts=["(1/2) one", "(2/2) two"], byte_limit=200,
                    latency_s=1.0, sources=["X"], debug={})
    (row,) = store.recent_queries(1)
    assert row["parts"] == ["(1/2) one", "(2/2) two"] and row["part_count"] == 2 and row["bytes"] == len("(1/2) one")


def test_recent_queries_are_newest_first_by_time_not_by_insert_order():
    store = Store()
    demo.seed(store, 12)  # back-dated rows: ids ascend but timestamps do not
    stamps = [r["ts"] for r in store.recent_queries(12)]
    assert stamps == sorted(stamps, reverse=True)


def test_ollama_url_can_be_set_from_the_environment(monkeypatch):
    import importlib

    from lorapedia import config
    monkeypatch.setenv("LORAPEDIA_OLLAMA_URL", "http://ollama:11434/")
    try:
        importlib.reload(config)
        s = config.Settings()
        assert s.llm_base_url == "http://ollama:11434/v1" and s.embed_base_url == "http://ollama:11434"
    finally:
        monkeypatch.delenv("LORAPEDIA_OLLAMA_URL")
        importlib.reload(config)
