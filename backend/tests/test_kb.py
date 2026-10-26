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
    # Pause takes effect at the next batch boundary: a batch already in flight is allowed to finish first.
    await asyncio_sleep(0.2)
    frozen = index.chunk_count()
    await asyncio_sleep(0.2)
    assert index.chunk_count() == frozen  # once settled, nothing advances while paused
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


def _zim_with_redirects(path, articles: int, redirects: int):
    from libzim.writer import Creator, Hint, Item, StringProvider

    class A(Item):
        def __init__(self, n):
            super().__init__()
            self.n = n

        def get_path(self): return f"A{self.n}"
        def get_title(self): return f"Article {self.n}"
        def get_mimetype(self): return "text/html"
        def get_contentprovider(self): return StringProvider(f"<html><body><p>Article number {self.n}.</p></body></html>")
        def get_hints(self): return {Hint.FRONT_ARTICLE: True}

    with Creator(str(path)).config_indexing(False, "eng") as c:
        c.set_mainpath("A0")
        for n in range(articles):
            c.add_item(A(n))
        for r in range(redirects):
            c.add_redirection(f"R{r}", f"Redirect {r}", f"A{r % articles}", {Hint.FRONT_ARTICLE: True})  # as in Kiwix files, where redirects are listed as articles
        for k, v in (("Title", "t"), ("Language", "eng"), ("Creator", "x"), ("Publisher", "x"), ("Description", "d"),
                     ("Name", "n"), ("Date", "2026-01-01")):
            c.add_metadata(k, v)
    return path


def test_estimate_articles_ignores_redirects_exactly_for_small_files(tmp_path):
    reader = ZimReader(str(_zim_with_redirects(tmp_path / "small.zim", articles=20, redirects=180)))
    assert reader.estimate_articles() == 20
    assert reader.article_count > 100  # libzim's own figure counts the redirects: the reason this exists


def test_estimate_articles_samples_large_files(tmp_path):
    reader = ZimReader(str(_zim_with_redirects(tmp_path / "big.zim", articles=300, redirects=2700)))
    assert 240 <= reader.estimate_articles() <= 360  # true value 300, from a 1500-entry sample of 3000


def test_library_reports_real_article_count_not_the_redirect_inflated_one(tmp_path):
    from fastapi.testclient import TestClient
    zim = _zim_with_redirects(tmp_path / "w.zim", articles=15, redirects=135)
    opts = {"port_lister": lambda: [], "poll_s": 60}
    with TestClient(create_app(str(tmp_path / "d"), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts)) as c:
        assert c.post("/api/kb/library/import", json={"path": str(zim)}).json()["articles"] == 15


async def test_progress_reports_articles_per_second_separately_from_entries(tmp_path):
    index, indexer = fresh(_zim_with_redirects(tmp_path / "p.zim", articles=10, redirects=90))
    await indexer.run("sample.zim")
    p = indexer.progress
    assert p.state == "done" and p.articles == 10 and p.entries_total >= 100 and p.eta_s == 0
    assert p.articles_per_s > 0


async def test_a_single_common_word_match_is_not_evidence(zim_path):
    """"how big is Antarctica": only "big" matches anything indexed, so nothing relevant should come back."""
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim")
    emb = indexer.embedder
    rows = [(900 + i, f"Football team {i}", "", 0, "A big win for the team in a big match.", (await emb.embed(["a big win football"]))[0])
            for i in range(3)]
    index.add_batch("sample.zim", rows, last_entry=999, articles=3)
    r = HybridRetriever(index, emb)
    assert await r.search("how big is antarctica", 4) == []
    # ...but a title match, or matching every term, still counts:
    assert (await r.search("big football team", 4))[0].title.startswith("Football team")
    assert (await r.search("big match", 4))[0].title.startswith("Football team")  # both terms present in the text
    assert (await r.search("quasar", 4))[0].title == "Quasar"  # single-term queries are unaffected


# ---- two-pass ("fast") indexing ---------------------------------------------------------------


def chunk_keys(index):
    return sorted((r["title"], r["ord"]) for r in index.db.execute("SELECT title, ord FROM chunks"))


class Spy(HashEmbedder):
    """Snapshots the index the first time the second pass starts embedding."""

    def __init__(self, index):
        super().__init__()
        self.index, self.snapshot = index, None

    async def embed(self, texts, *, query=False):
        state = self.index.get_state("sample.zim")
        if self.snapshot is None and state and state["phase"] == "rest":
            self.snapshot = {"first": self.index.db.execute("SELECT COUNT(DISTINCT title) FROM chunks WHERE ord=0").fetchone()[0],
                             "deeper": self.index.db.execute("SELECT COUNT(*) FROM chunks WHERE ord>0").fetchone()[0]}
        return await super().embed(texts, query=query)


async def test_fast_mode_makes_every_article_searchable_before_any_detail_exists(zim_path):
    index, indexer = fresh(zim_path)
    spy = Spy(index)
    indexer.embedder = spy
    await indexer.run("sample.zim", mode="fast")
    # When the second pass began: all 12 articles already had their opening chunk, and nothing deeper yet.
    assert spy.snapshot == {"first": len(ARTICLES), "deeper": 0}
    assert indexer.progress.state == "done" and index.library_state() == "complete"
    assert index.get_state("sample.zim")["phase"] == "all"


async def test_fast_mode_ends_with_exactly_the_same_index_as_standard_mode(zim_path):
    a, ia = fresh(zim_path)
    await ia.run("sample.zim", mode="all")
    b, ib = fresh(zim_path)
    await ib.run("sample.zim", mode="fast")
    assert chunk_keys(a) == chunk_keys(b)
    assert ib.progress.articles == len(ARTICLES) == ia.progress.articles  # pass 2 adds chunks, not articles
    assert b.stats()["chunks"] == a.stats()["chunks"]


async def test_fast_mode_skips_what_a_standard_run_already_completed(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim", max_articles=4, mode="all")  # e.g. a partial alphabetical run
    done_before = index.chunk_count()
    await indexer.run("sample.zim", mode="fast")
    full, ifull = fresh(zim_path)
    await ifull.run("sample.zim", mode="all")
    keys = chunk_keys(index)
    assert keys == chunk_keys(full) and len(keys) == len(set(keys))  # complete, no duplicates
    assert index.chunk_count() > done_before and indexer.progress.articles == len(ARTICLES)


async def test_an_interrupted_fast_run_resumes_as_fast_even_when_started_as_standard(zim_path):
    class Flaky(HashEmbedder):
        calls = 0

        async def embed(self, texts, *, query=False):
            Flaky.calls += 1
            if Flaky.calls == 4:
                raise EmbedError("went away")
            return await super().embed(texts, query=query)

    index, indexer = fresh(zim_path, Flaky())
    indexer.BATCH_ARTICLES = 3
    await indexer.run("sample.zim", mode="fast")
    assert indexer.progress.state == "error" and index.library_state() == "partial"
    assert index.get_state("sample.zim")["phase"] in ("first", "rest")
    healthy = Indexer(index, HashEmbedder())
    await healthy.run("sample.zim", mode="all")  # asked for "all", but a fast run is unfinished
    full, ifull = fresh(zim_path)
    await ifull.run("sample.zim", mode="all")
    assert chunk_keys(index) == chunk_keys(full)
    assert healthy.progress.mode == "fast" and index.library_state() == "complete"


async def test_progress_describes_the_phase(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim", mode="fast")
    p = indexer.progress
    assert p.mode == "fast" and p.phase == "rest" and "Pass 2 of 2" in p.phase_label
    assert p.entries_done == p.entries_total  # per-phase progress ends at 100%


def test_library_state(zim_path):
    index = Index()
    assert index.library_state() == "none"
    reader = ZimReader(str(zim_path))
    index.add_zim("sample.zim", str(zim_path), "S", reader.entry_count, 12)
    assert index.library_state() == "none"  # in the library, nothing indexed


async def test_library_state_is_partial_until_everything_is_done(zim_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim", max_articles=3)
    assert index.library_state() == "complete"  # a deliberate subset run finished: nothing more is coming
    index2, indexer2 = fresh(zim_path)
    index2.set_status("sample.zim", "running")
    index2.update_state("sample.zim", phase="first")
    index2.ensure_vec("hash-384", 384)
    index2.add_batch("sample.zim", [(1, "T", "", 0, "t", [0.1] * 384)], last_entry=1, articles=1)
    assert index2.library_state() == "partial"  # pass 1 still running: not every article has been seen
    index2.update_state("sample.zim", phase="rest")
    assert index2.library_state() == "covered"  # pass 1 done: every topic is findable, details still arriving
    index2.update_state("sample.zim", phase="all")
    index2.set_status("sample.zim", "done")
    assert index2.library_state() == "complete"


def test_old_databases_are_migrated(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "old.db")
    old.executescript("CREATE TABLE index_state (zim TEXT PRIMARY KEY, last_entry INTEGER NOT NULL DEFAULT -1, "
                      "articles INTEGER NOT NULL DEFAULT 0, chunks INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'idle', "
                      "params TEXT NOT NULL DEFAULT '{}', error TEXT, updated REAL); "
                      "INSERT INTO index_state(zim, last_entry, articles, chunks, status) VALUES ('x.zim', 500, 40, 160, 'running');")
    old.commit(); old.close()
    ix = Index(tmp_path / "old.db")
    state = ix.get_state("x.zim")
    assert (state["phase"], state["rest_from"], state["last_entry"], state["articles"]) == ("all", 0, 500, 40)  # data kept


def test_api_fast_mode_reports_phase(tmp_path, zim_path):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    with TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts)) as c:
        c.post("/api/kb/library/import", json={"path": str(zim_path)})
        c.post("/api/kb/index/start", json={"zim": "sample.zim"})  # fast is the default
        for _ in range(200):
            st = c.get("/api/kb/index/status").json()
            if st["state"] == "done":
                break
            time.sleep(0.02)
        assert st["state"] == "done" and st["mode"] == "fast" and st["articles"] == len(ARTICLES)


async def test_resuming_does_not_make_progress_look_like_it_restarted(zim_path):
    class Flaky(HashEmbedder):
        calls = 0

        async def embed(self, texts, *, query=False):
            Flaky.calls += 1
            if Flaky.calls == 4:  # probe, two batches, then fail partway through pass 1
                raise EmbedError("went away")
            return await super().embed(texts, query=query)

    index, indexer = fresh(zim_path, Flaky())
    indexer.BATCH_ARTICLES = 3
    await indexer.run("sample.zim", mode="fast")
    assert indexer.progress.state == "error"
    done_before_failure = indexer.progress.entries_done
    assert done_before_failure > 0

    class Spy2(HashEmbedder):
        def __init__(self, indexer):
            super().__init__()
            self.indexer, self.seen = indexer, None

        async def embed(self, texts, *, query=False):
            if self.seen is None and len(texts) > 1:
                self.seen = (self.indexer.progress.entries_done, self.indexer.progress.entries_total)
            return await super().embed(texts, query=query)

    healthy = Indexer(index, HashEmbedder())
    healthy.embedder = Spy2(healthy)
    healthy.BATCH_ARTICLES = 3
    await healthy.run("sample.zim", mode="fast")
    done, total = healthy.embedder.seen
    assert done >= done_before_failure and total == indexer.progress.entries_total  # carried on, not back to zero


async def test_a_library_file_that_was_never_indexed_does_not_block_the_fallback(zim_path, tmp_path):
    index, indexer = fresh(zim_path)
    await indexer.run("sample.zim", mode="fast")
    assert index.library_state() == "complete"
    extra = make_sample_zim(tmp_path / "other.zim")
    index.add_zim("other.zim", str(extra), "Other", 12, 12)  # in the library, never indexed
    assert index.library_state() == "complete"  # unindexed files are not part of the searchable library
