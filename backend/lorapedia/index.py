"""SQLite knowledge-base index: chunks + FTS5 (BM25) + sqlite-vec vectors, and a resumable indexer."""

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import sqlite_vec

from .embed import Embedder, EmbedError
from .zim import ZimReader, chunk_article

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zims (
    name TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT, entries INTEGER, articles INTEGER, added REAL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, zim TEXT NOT NULL, entry_id INTEGER NOT NULL,
    title TEXT NOT NULL, section TEXT NOT NULL, ord INTEGER NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_zim ON chunks(zim, entry_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, text, content='chunks', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS index_state (
    zim TEXT PRIMARY KEY, last_entry INTEGER NOT NULL DEFAULT -1, articles INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'idle', params TEXT NOT NULL DEFAULT '{}',
    error TEXT, updated REAL,
    phase TEXT NOT NULL DEFAULT 'all',      -- 'all' = complete/standard, 'first' / 'rest' = a two-pass run in progress
    rest_from INTEGER NOT NULL DEFAULT 0    -- two-pass: entries before this already have every chunk
);
"""


class IndexMismatch(RuntimeError):
    pass


class Index:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(index_state)")}
        for name, ddl in (("phase", "TEXT NOT NULL DEFAULT 'all'"), ("rest_from", "INTEGER NOT NULL DEFAULT 0")):
            if name not in cols:  # databases created before two-pass indexing
                self.db.execute(f"ALTER TABLE index_state ADD COLUMN {name} {ddl}")
        self.db.commit()
        self.path = str(path)

    # ---- meta / vector table ------------------------------------------------------------

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None

    def embedder_name(self) -> str | None:
        return self._meta("embedder")

    def ensure_vec(self, embedder_name: str, dim: int) -> None:
        """Create the vector table on first use; refuse to mix incompatible embedders."""
        current = self._meta("embedder")
        if current is None:
            self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{dim}])")
            self.db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                                [("embedder", embedder_name), ("dim", str(dim))])
            self.db.commit()
        elif current != embedder_name or int(self._meta("dim")) != dim:
            raise IndexMismatch(
                f"This index was built with '{current}'. Remove the library entries and re-index to switch "
                f"to '{embedder_name}'.")

    # ---- library ------------------------------------------------------------------------

    def add_zim(self, name: str, path: str, title: str, entries: int, articles: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO zims VALUES (?,?,?,?,?,?)",
                        (name, path, title, entries, articles, time.time()))
        self.db.commit()

    def list_zims(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT z.*, COALESCE(s.status,'not indexed') AS status, COALESCE(s.articles,0) AS indexed_articles,"
            " COALESCE(s.chunks,0) AS indexed_chunks, s.updated AS last_indexed, s.last_entry AS last_entry,"
            " s.error AS error FROM zims z LEFT JOIN index_state s ON s.zim = z.name ORDER BY z.name").fetchall()
        return [dict(r) for r in rows]

    def get_zim(self, name: str) -> dict | None:
        row = self.db.execute("SELECT * FROM zims WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def remove_zim(self, name: str) -> None:
        """Drop a library entry and everything indexed from it (the ZIM file itself is untouched)."""
        self.db.execute("INSERT INTO chunks_fts(chunks_fts, rowid, title, text) "
                        "SELECT 'delete', id, title, text FROM chunks WHERE zim=?", (name,))
        if self._meta("embedder"):
            self.db.execute("DELETE FROM vec_chunks WHERE rowid IN (SELECT id FROM chunks WHERE zim=?)", (name,))
        self.db.execute("DELETE FROM chunks WHERE zim=?", (name,))
        self.db.execute("DELETE FROM index_state WHERE zim=?", (name,))
        self.db.execute("DELETE FROM zims WHERE name=?", (name,))
        if self.db.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is None and self._meta("embedder"):
            # Empty index: forget the embedder so a different one can be chosen next time.
            self.db.execute("DROP TABLE IF EXISTS vec_chunks")
            self.db.execute("DELETE FROM meta WHERE k IN ('embedder','dim')")
        self.db.commit()

    # ---- writing ------------------------------------------------------------------------

    def get_state(self, zim: str) -> dict | None:
        row = self.db.execute("SELECT * FROM index_state WHERE zim=?", (zim,)).fetchone()
        return dict(row) if row else None

    def set_status(self, zim: str, status: str, error: str | None = None, params: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO index_state(zim, status, error, params, updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(zim) DO UPDATE SET status=excluded.status, error=excluded.error, updated=excluded.updated"
            + (", params=excluded.params" if params is not None else ""),
            (zim, status, error, json.dumps(params or {}), time.time()))
        self.db.commit()

    def update_state(self, zim: str, **fields) -> None:
        """Set checkpoint/phase columns directly (names come from our own code, never from users)."""
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE index_state SET {sets}, updated=? WHERE zim=?", (*fields.values(), time.time(), zim))
        self.db.commit()

    def library_state(self) -> str:
        """How far indexing has got, for deciding whether "no match" is certain:

        "none"     nothing indexed;
        "partial"  some article has not been looked at yet, so "not found" may just mean "not yet";
        "covered"  every article has at least its opening indexed (two-pass indexing is on its second pass),
                   so a missing *topic* is certain even though deep details may still be arriving;
        "complete" everything is indexed.
        """
        # Only files that have been indexed (or are being) form the searchable library. A file added but never
        # indexed (e.g. the sample) is listed as "Not indexed" in the UI and must not hold every answer at "partial".
        rows = self.db.execute("SELECT z.name, s.status, s.phase FROM zims z JOIN index_state s ON s.zim=z.name").fetchall()
        if not rows or self.chunk_count() == 0:
            return "none"
        if all(r["status"] == "done" and r["phase"] == "all" for r in rows):
            return "complete"
        if all(r["phase"] == "rest" or (r["phase"] == "all" and r["status"] == "done") for r in rows):
            return "covered"
        return "partial"

    def add_batch(self, zim: str, rows: list[tuple], last_entry: int, articles: int) -> None:
        """Atomically store chunks+vectors for a batch AND advance the checkpoint (safe to resume).

        rows: (entry_id, title, section, ord, text, vector)
        """
        with self.db:
            for entry_id, title, section, ord_, text, vec in rows:
                cur = self.db.execute(
                    "INSERT INTO chunks(zim, entry_id, title, section, ord, text) VALUES (?,?,?,?,?,?)",
                    (zim, entry_id, title, section, ord_, text))
                cid = cur.lastrowid
                self.db.execute("INSERT INTO chunks_fts(rowid, title, text) VALUES (?,?,?)", (cid, title, text))
                self.db.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES (?,?)",
                                (cid, sqlite_vec.serialize_float32(vec)))
            self.db.execute(
                "UPDATE index_state SET last_entry=?, articles=articles+?, chunks=chunks+?, updated=? WHERE zim=?",
                (last_entry, articles, len(rows), time.time(), zim))

    # ---- reading ------------------------------------------------------------------------

    def stats(self) -> dict:
        one = lambda q: self.db.execute(q).fetchone()[0] or 0  # noqa: E731
        size = Path(self.path).stat().st_size if self.path != ":memory:" and Path(self.path).exists() else 0
        return {
            "articles": one("SELECT COALESCE(SUM(articles),0) FROM index_state"),
            "chunks": one("SELECT COUNT(*) FROM chunks"),
            "disk_bytes": size,
            "last_indexed": self.db.execute("SELECT MAX(updated) FROM index_state").fetchone()[0],
            "embedder": self.embedder_name(),
        }

    def chunk_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def fts_search(self, match: str, n: int) -> list[int]:
        rows = self.db.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 8.0, 1.0) LIMIT ?",
            (match, n)).fetchall()
        return [r[0] for r in rows]

    def vec_search(self, vec: list[float], n: int) -> list[tuple[int, float]]:
        """Returns (chunk id, cosine similarity); vectors are normalised so cos = 1 - d^2/2."""
        if not self._meta("embedder"):
            return []
        rows = self.db.execute(
            "SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ?",
            (sqlite_vec.serialize_float32(vec), n)).fetchall()
        return [(r[0], 1 - r[1] ** 2 / 2) for r in rows]

    def get_chunks(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.db.execute(f"SELECT * FROM chunks WHERE id IN ({marks})", ids).fetchall()
        return {r["id"]: r for r in rows}


@dataclass
class Progress:
    state: str = "idle"  # idle | running | paused | done | error
    zim: str = ""
    entries_done: int = 0
    entries_total: int = 0
    articles: int = 0
    chunks: int = 0
    articles_per_s: float = 0.0
    eta_s: float | None = None
    error: str | None = None
    params: dict = field(default_factory=dict)
    mode: str = "all"  # "all" (article by article) or "fast" (two passes)
    phase: str = "all"  # fast mode: "first" (opening chunk of every article) then "rest"
    phase_label: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class Indexer:
    """Streams a ZIM into the index in batches. Pausable, stoppable and resumable via checkpoints."""

    BATCH_ARTICLES = 25
    EMBED_BATCH = 32

    def __init__(self, index: Index, embedder: Embedder) -> None:
        self.index, self.embedder = index, embedder
        self.progress = Progress()
        self._gate = asyncio.Event()
        self._gate.set()
        self._stop = False
        self._task: asyncio.Task | None = None

    def start(self, zim_name: str, *, max_articles: int | None = None, lead_only: bool = False,
              mode: str = "all") -> asyncio.Task:
        """mode "fast": index the opening chunk of every article first, then the rest, so every topic becomes
        answerable early. mode "all": every chunk of each article in turn."""
        if self._task and not self._task.done():
            raise RuntimeError("Indexing is already running.")
        self._stop = False
        self._gate.set()
        self._task = asyncio.create_task(self.run(zim_name, max_articles=max_articles, lead_only=lead_only, mode=mode))
        return self._task

    def pause(self) -> None:
        if self.progress.state == "running":
            self._gate.clear()
            self.progress.state = "paused"

    def resume(self) -> None:
        if self.progress.state == "paused":
            self.progress.state = "running"
            self._gate.set()

    def stop(self) -> None:
        self._stop = True
        self._gate.set()

    async def wait(self) -> None:
        if self._task:
            await self._task

    async def run(self, zim_name: str, *, max_articles: int | None = None, lead_only: bool = False,
                  mode: str = "all") -> None:
        p = self.progress = Progress(state="running", zim=zim_name, mode=mode,
                                     params={"max_articles": max_articles, "lead_only": lead_only, "mode": mode})
        try:
            zim = self.index.get_zim(zim_name)
            if not zim:
                raise EmbedError(f"'{zim_name}' is not in the library.")
            reader = ZimReader(zim["path"])
            dim = len((await self.embedder.embed(["probe"]))[0])
            self.index.ensure_vec(self.embedder.name, dim)
            self.index.set_status(zim_name, "running", params=p.params)
            state = self.index.get_state(zim_name)
            p.articles, p.chunks = state["articles"], state["chunks"]

            # A two-pass run that was interrupted always resumes as two-pass, whatever mode is asked for now.
            if mode == "fast" or state["phase"] in ("first", "rest"):
                p.mode = "fast"
                max_articles = None  # a partial first pass would leave the second pass meaningless
                phase = state["phase"]
                if phase == "all":  # starting fresh: whatever is already indexed is complete and is skipped
                    rest_from = state["last_entry"] + 1
                    self.index.update_state(zim_name, phase="first", rest_from=rest_from)
                    phase = "first"
                else:
                    rest_from = state["rest_from"]
                if phase == "first":
                    if await self._walk(zim_name, reader, state["last_entry"] + 1, "first", lead_only, None,
                                        range_start=rest_from):
                        return
                    self.index.update_state(zim_name, phase="rest", last_entry=rest_from - 1)
                    state = self.index.get_state(zim_name)
                if await self._walk(zim_name, reader, state["last_entry"] + 1, "rest", lead_only, None, range_start=rest_from):
                    return
                self.index.update_state(zim_name, phase="all")
            else:
                if await self._walk(zim_name, reader, state["last_entry"] + 1, "all", lead_only, max_articles):
                    return
            p.state = "done"
            self.index.set_status(zim_name, "done")
        except Exception as exc:  # checkpoint is intact, so "resume" picks up where this stopped
            log.exception("indexing %s failed", zim_name)
            p.state, p.error = "error", str(exc)
            if self.index.get_zim(zim_name):
                self.index.set_status(zim_name, "error", error=str(exc))

    PHASE_LABELS = {"first": "Pass 1 of 2: the opening of every article", "rest": "Pass 2 of 2: the rest of each article",
                    "all": ""}

    async def _walk(self, zim_name: str, reader: ZimReader, start: int, which: str, lead_only: bool,
                    max_articles: int | None, range_start: int | None = None) -> bool:
        """Index entries from `start` to the end of the file. `which` is "all", "first" (opening chunk only)
        or "rest" (every chunk after the first). Returns True if the user stopped it, False if it finished
        (or hit max_articles)."""
        p = self.progress
        base = 0 if which == "all" else (start if range_start is None else range_start)  # progress is shown per phase
        p.phase, p.phase_label = which, self.PHASE_LABELS[which]
        p.entries_total, p.entries_done = reader.entry_count - base, start - base
        it = reader.articles(start)
        t0, done_at_start, articles_at_start = time.monotonic(), start, p.articles
        exhausted = False
        while not exhausted:
            await self._gate.wait()  # blocks here while paused
            if self._stop:
                self.index.set_status(zim_name, "stopped")
                p.state = "idle"
                return True
            room = None if max_articles is None else max_articles - p.articles
            if room is not None and room <= 0:
                break
            batch, exhausted = await asyncio.to_thread(self._read_batch, it, min(self.BATCH_ARTICLES, room or 10**9), lead_only, which)
            if batch:
                rows = await self._embed_rows(batch)
                last = batch[-1][0].entry_id
                new_articles = 0 if which == "rest" else len(batch)  # the second pass adds chunks, not articles
                self.index.add_batch(zim_name, rows, last_entry=last, articles=new_articles)
                p.articles += new_articles
                p.chunks += len(rows)
                p.entries_done = last + 1 - base
            if exhausted:
                self.index.update_state(zim_name, last_entry=reader.entry_count - 1)
                p.entries_done = p.entries_total
            elapsed = max(time.monotonic() - t0, 1e-6)
            # Most ZIM entries are redirects, so articles/s and the ETA (which walks entries) differ.
            p.articles_per_s = round((p.articles - articles_at_start) / elapsed, 2)
            entries_per_s = (p.entries_done + base - done_at_start) / elapsed
            remaining = p.entries_total - p.entries_done
            p.eta_s = round(remaining / entries_per_s) if entries_per_s and remaining > 0 else 0
            await asyncio.sleep(0)  # let the API/worker breathe between batches
        return False

    @staticmethod
    def _read_batch(it, n: int, lead_only: bool, which: str = "all"):
        batch, exhausted = [], False
        while len(batch) < n:
            try:
                article = next(it)
            except StopIteration:
                exhausted = True
                break
            chunks = chunk_article(article.html, lead_only=lead_only)
            if which == "first":
                chunks = chunks[:1]
            elif which == "rest":
                chunks = chunks[1:]
            if chunks:
                batch.append((article, chunks))
        return batch, exhausted

    async def _embed_rows(self, batch) -> list[tuple]:
        flat = [(a, c) for a, chunks in batch for c in chunks]
        texts = [f"{a.title} - {c.section}: {c.text}" if c.section else f"{a.title}: {c.text}" for a, c in flat]
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.EMBED_BATCH):
            vectors += await self.embedder.embed(texts[i:i + self.EMBED_BATCH])
        return [(a.entry_id, a.title, c.section, c.ord, c.text, v) for (a, c), v in zip(flat, vectors)]
