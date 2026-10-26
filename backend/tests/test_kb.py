"""Knowledge-base tests against a sample ZIM generated on the fly with libzim."""

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.embed import EmbedError, HashEmbedder
from lorapedia.index import Index, IndexMismatch, Indexer
from lorapedia.llm.base import MockLLM
from lorapedia.retrieval.hybrid import HybridRetriever, RoutingRetriever
from lorapedia.sample import ARTICLES, make_sample_zim, render_html
from lorapedia.zim import ZimReader, chunk_article, extract_sections


@pytest.fixture(scope="module")
def zim_path(tmp_path_factory):
    return make_sample_zim(tmp_path_factory.mktemp("zim") / "sample.zim")


def fresh(zim_path, embedder=None):
    index = Index()
    reader = ZimReader(str(zim_path))
    index.add_zim("sample.zim", str(zim_path), "Sample", reader.entry_count, reader.article_count)
    return index, Indexer(index, embedder or HashEmbedder())


# ---- extraction / chunking ----------------------------------------------------------------


def test_extractor_drops_noise_and_title():
    html = render_html("Quasar", ["Lead text here."], [("Properties", ["Body text."])])
    sections = extract_sections(html)
    assert sections == [("", ["Lead text here."]), ("Properties", ["Body text."]),
                        ("See also", ["Related page that must not be indexed"]),
                        ("References", ["Citation text that must not be indexed"])]
    flat = " ".join(" ".join(p) for _, p in sections)
    assert "Infobox" not in flat and "[1]" not in flat and "disambiguation" not in flat and "Quasar" not in flat


def test_chunker_skips_references_and_see_also():
    chunks = chunk_article(render_html("Quasar", ["Lead."], [("Properties", ["Body."])]))
    assert [(c.ord, c.section) for c in chunks] == [(0, ""), (1, "Properties")]
    assert all("must not be indexed" not in c.text for c in chunks)


def test_lead_only():
    chunks = chunk_article(render_html("Quasar", ["Lead."], [("Properties", ["Body."])]), lead_only=True)
    assert [c.section for c in chunks] == [""]


def test_long_paragraph_is_split_at_sentences_within_limit():
    long_para = " ".join(f"Sentence number {i} is here." for i in range(120))
    chunks = chunk_article(f"<html><body><p>{long_para}</p></body></html>")
    assert len(chunks) > 1 and all(len(c.text) <= 800 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)


def test_zim_reader_yields_only_html_articles(zim_path):
    titles = {a.title for a in ZimReader(str(zim_path)).articles()}
    assert titles == {a[0] for a in ARTICLES}


# ---- indexing -----------------------------------------------------------------------------


async def test_index_whole_zim(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim")
    p = indexer.progress
    assert p.state == "done" and p.articles == len(ARTICLES) and p.chunks > p.articles
    assert p.entries_done == p.entries_total
    stats = index.stats()
    assert stats["articles"] == len(ARTICLES) and stats["chunks"] == p.chunks and stats["embedder"] == "hash-384"


async def test_subset_then_resume_expands_without_duplicates(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim", max_articles=4)
    assert indexer.progress.articles == 4
    first_chunks = index.chunk_count()
    await indexer.run("sample.zim")  # "expand later": continues after the checkpoint
    assert indexer.progress.articles == len(ARTICLES)
    titles = [r[0] for r in index.db.execute("SELECT title FROM chunks WHERE ord=0")]
    assert len(titles) == len(set(titles)) == len(ARTICLES)
    assert index.chunk_count() > first_chunks


async def test_lead_only_indexes_fewer_chunks(zim_path):
    full, ix_full = fresh(zim_path)
    await ix_full.run("sample.zim")
    lead, ix_lead = fresh(zim_path)
    await ix_lead.run("sample.zim", lead_only=True)
    assert lead.chunk_count() == len(ARTICLES) < full.chunk_count()


async def test_pause_and_resume(zim_path):
    index, indexer = fresh(zim_path)
    indexer.BATCH_ARTICLES = 2
    indexer.start("sample.zim")
    await asyncio_sleep()
    indexer.pause()
    assert indexer.progress.state == "paused"
    frozen = index.chunk_count()
    await asyncio_sleep(0.05)
    assert index.chunk_count() == frozen  # nothing advances while paused
    indexer.resume()
    await indexer.wait()
    assert indexer.progress.state == "done" and indexer.progress.articles == len(ARTICLES)


async def test_failure_keeps_checkpoint_and_resume_finishes(zim_path):
    class Flaky(HashEmbedder):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def embed(self, texts, *, query=False):
            self.calls += 1
            if self.calls == 3:  # probe + first batch succeed, second batch fails
                raise EmbedError("Ollama went away")
            return await super().embed(texts, query=query)

    index, indexer = fresh(zim_path, Flaky())
    indexer.BATCH_ARTICLES, indexer.EMBED_BATCH = 4, 1000
    await indexer.run("sample.zim")
    assert indexer.progress.state == "error" and "Ollama went away" in indexer.progress.error
    assert 0 < indexer.progress.articles < len(ARTICLES)
    assert index.get_state("sample.zim")["status"] == "error"
    await indexer.run("sample.zim")  # embedder healthy now
    assert indexer.progress.state == "done" and indexer.progress.articles == len(ARTICLES)
    assert index.stats()["articles"] == len(ARTICLES)


async def test_index_refuses_a_different_embedder(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim", max_articles=2)
    other = Indexer(index, HashEmbedder(dim=128))
    await other.run("sample.zim")
    assert other.progress.state == "error" and "hash-384" in other.progress.error
    with pytest.raises(IndexMismatch):
        index.ensure_vec("hash-128", 128)


async def test_remove_zim_clears_everything(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim")
    index.remove_zim("sample.zim")
    assert index.chunk_count() == 0 and index.list_zims() == [] and index.embedder_name() is None
    assert index.db.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'quasar'").fetchone()[0] == 0


# ---- retrieval ----------------------------------------------------------------------------


@pytest.fixture
async def retriever(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim")
    return HybridRetriever(index, indexer.embedder)


@pytest.mark.parametrize("query,expected_title", [
    ("what is a quasar?", "Quasar"),
    ("how long should I boil water to make it safe", "Water purification"),
    ("highest mountain in the world", "Mount Everest"),
    ("who first climbed everest", "Mount Everest"),
    ("how big is a meshtastic message", "Meshtastic"),
    ("what does insulin do", "Insulin"),
    ("why are there two tides a day", "Tide"),
    ("cafe", "Café"),  # diacritics folded
    ("quasars", "Quasar"),  # plural -> stem
])
async def test_retrieval_finds_the_right_article_first(retriever, query, expected_title):
    results = await retriever.search(query, 4)
    assert results and results[0].title == expected_title, [r.title for r in results]


async def test_retrieval_returns_the_matching_section(retriever):
    (top, *_) = await retriever.search("who made the first confirmed ascent of everest", 3)
    assert top.section == "First ascent" and "Hillary" in top.text


async def test_retrieval_nothing_relevant_returns_empty(retriever):
    assert await retriever.search("zzzz qqqq", 4) == []
    assert await retriever.search("???", 4) == []


async def test_retrieval_diversity_and_detail(retriever):
    results = await retriever.search("quasar black hole", 6)
    assert max(sum(r.title == t for r in results) for t in {r.title for r in results}) <= 2
    assert results[0].detail["fts_rank"] is not None


async def test_query_with_fts_syntax_chars_is_safe(retriever):
    await retriever.search('"quasar" OR (NEAR* -', 4)


async def test_keyword_only_fallback_when_embedder_fails(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim")

    class Down(HashEmbedder):
        async def embed(self, texts, *, query=False):
            raise EmbedError("down")

    results = await HybridRetriever(index, Down()).search("what is a quasar", 3)
    assert results[0].title == "Quasar" and results[0].detail["vec_rank"] is None


async def test_routing_uses_demo_corpus_until_indexed(zim_path):
    index, indexer = fresh(zim_path)
    router = RoutingRetriever(index, lambda: indexer.embedder)
    assert router.mode == "demo"
    assert (await router.search("what is a quasar", 1))[0].title == "Quasar"
    await indexer.run("sample.zim")
    assert router.mode == "index"
    assert (await router.search("highest mountain", 1))[0].title == "Mount Everest"


# ---- API ----------------------------------------------------------------------------------


def test_api_knowledge_base_flow(tmp_path, zim_path):
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder())) as client:
        assert client.get("/api/health").json()["knowledge_base"] == "demo"
        assert client.post("/api/kb/library/import", json={"path": str(tmp_path / "nope.zim")}).status_code == 404
        bad = tmp_path / "bad.zim"
        bad.write_text("not a zim")
        assert client.post("/api/kb/library/import", json={"path": str(bad)}).status_code == 400

        lib = client.post("/api/kb/library/import", json={"path": str(zim_path)}).json()
        assert lib["name"] == "sample.zim" and lib["articles"] == len(ARTICLES)
        assert client.post("/api/kb/index/start", json={"zim": "nope.zim"}).status_code == 404

        client.post("/api/kb/index/start", json={"zim": "sample.zim", "lead_only": True})
        for _ in range(200):
            if client.get("/api/kb/index/status").json()["state"] == "done":
                break
            import time
            time.sleep(0.02)
        assert client.get("/api/kb/index/status").json()["state"] == "done"
        stats = client.get("/api/kb/stats").json()
        assert stats["articles"] == len(ARTICLES) and stats["disk_bytes"] > 0

        found = client.get("/api/kb/search", params={"q": "what is photosynthesis"}).json()
        assert found["mode"] == "index" and found["results"][0]["title"] == "Photosynthesis"
        assert client.get("/api/kb/library").json()[0]["status"] == "done"

        r = client.post("/api/simulator/send", json={"text": "what is photosynthesis"})
        assert r.json()["outcome"] == "queued"
        assert client.delete("/api/kb/library/sample.zim").json() == {"removed": "sample.zim"}
        assert client.get("/api/health").json()["knowledge_base"] == "demo"


async def asyncio_sleep(t: float = 0.0):
    import asyncio
    await asyncio.sleep(t)
