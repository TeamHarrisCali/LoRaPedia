"""Hybrid retrieval: BM25 (FTS5) + vector KNN, fused with RRF, then lightly reranked."""

import logging
import re

from ..embed import Embedder
from ..index import Index
from .base import _STOP, Chunk, DemoRetriever, Retriever

log = logging.getLogger(__name__)
_WORD = re.compile(r"[a-z0-9]+")

CANDIDATES = 20  # per signal, before fusion
RRF_K = 60
MIN_VEC_COS = 0.35  # a vector-only hit below this similarity is noise, not evidence
MAX_PER_ARTICLE = 2  # keep the final context diverse


def _stem(word: str) -> str:
    """Crude plural folding so 'quasars' matches 'quasar' during rerank (FTS5 uses porter)."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def query_terms(query: str) -> list[str]:
    words = _WORD.findall(query.lower())
    terms = [w for w in words if w not in _STOP and len(w) > 1]
    return terms or words


def fts_match(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in terms)


class HybridRetriever:
    def __init__(self, index: Index, embedder: Embedder) -> None:
        self.index, self.embedder = index, embedder

    async def search(self, query: str, k: int) -> list[Chunk]:
        terms = query_terms(query)
        if not terms:
            return []
        fts_ids = self.index.fts_search(fts_match(terms), CANDIDATES)

        vec_hits: list[tuple[int, float]] = []
        if self.index.embedder_name() == self.embedder.name:
            try:
                (qvec,) = await self.embedder.embed([query], query=True)
                vec_hits = self.index.vec_search(qvec, CANDIDATES)
            except Exception as exc:  # embedder down: degrade to keyword-only rather than fail
                log.warning("vector search skipped: %s", exc)
        elif self.index.embedder_name():
            log.warning("index built with %s but %s is configured; vector search disabled",
                        self.index.embedder_name(), self.embedder.name)

        fts_rank = {cid: r for r, cid in enumerate(fts_ids, 1)}
        vec_rank = {cid: r for r, (cid, _) in enumerate(vec_hits, 1)}
        vec_cos = dict(vec_hits)
        # Vector-only candidates must clear a similarity floor; keyword hits always qualify.
        pool = set(fts_rank) | {c for c, cos in vec_cos.items() if cos >= MIN_VEC_COS}
        rows = self.index.get_chunks(list(pool))

        qstems = {_stem(t) for t in terms}
        scored: list[tuple[float, int, dict]] = []
        for cid in pool:
            row = rows.get(cid)
            if row is None:
                continue
            rrf = sum(1 / (RRF_K + r[cid]) for r in (fts_rank, vec_rank) if cid in r)
            title_words = {_stem(w) for w in _WORD.findall(row["title"].lower())}
            text_words = {_stem(w) for w in _WORD.findall(row["text"].lower())} | title_words
            title_hit = len(qstems & title_words) / len(qstems)
            coverage = len(qstems & text_words) / len(qstems)
            # Rerank: fused rank (scaled to ~0..2) + title match + query-term coverage + lead bonus.
            score = rrf * RRF_K + 1.0 * title_hit + 0.5 * coverage + (0.15 if row["ord"] == 0 else 0.0)
            scored.append((score, cid, {"fts_rank": fts_rank.get(cid), "vec_rank": vec_rank.get(cid),
                                        "vec_cos": round(vec_cos[cid], 3) if cid in vec_cos else None,
                                        "rrf": round(rrf, 4), "title_hit": round(title_hit, 2),
                                        "coverage": round(coverage, 2)}))
        scored.sort(key=lambda t: -t[0])

        out: list[Chunk] = []
        per_article: dict[str, int] = {}
        for score, cid, detail in scored:
            row = rows[cid]
            if per_article.get(row["title"], 0) >= MAX_PER_ARTICLE:
                continue
            per_article[row["title"]] = per_article.get(row["title"], 0) + 1
            out.append(Chunk(row["title"], row["text"], round(score, 3), row["section"], detail))
            if len(out) >= k:
                break
        return out


class RoutingRetriever:
    """Hybrid search over the real index once it has content; the built-in demo corpus before that."""

    def __init__(self, index: Index, embedder_getter, demo: Retriever | None = None) -> None:
        self.index, self._embedder, self.demo = index, embedder_getter, demo or DemoRetriever()

    @property
    def mode(self) -> str:
        return "index" if self.index.chunk_count() else "demo"

    async def search(self, query: str, k: int) -> list[Chunk]:
        if not self.index.chunk_count():
            return await self.demo.search(query, k)
        return await HybridRetriever(self.index, self._embedder()).search(query, k)
