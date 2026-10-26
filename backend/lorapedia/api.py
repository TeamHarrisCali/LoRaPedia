"""FastAPI app: settings, simulator, knowledge base, query log, and a WebSocket feed for the UI."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .config import Settings
from .embed import Embedder, HashEmbedder, OllamaEmbedder
from .engine import Engine
from .index import Index, Indexer
from .llm.base import MockLLM
from .llm.openai_compat import OpenAICompatLLM
from .radio.base import Packet
from .radio.manager import RadioManager
from .retrieval.hybrid import RoutingRetriever
from .store import Store
from .zim import ZimReader


class SimMessage(BaseModel):
    sender_id: str = "!sim00001"
    sender_name: str = "SIM"
    text: str
    is_dm: bool = True
    channel: int = 0


class ImportZim(BaseModel):
    path: str


class NodeToggle(BaseModel):
    on: bool = True


class IndexStart(BaseModel):
    zim: str
    max_articles: int | None = None  # e.g. 100000 for "top 100k articles"
    lead_only: bool = False


def create_app(data_dir: str | None = None, *, llm=None, embedder: Embedder | None = None,
               radio_options: dict | None = None) -> FastAPI:
    """`llm` / `embedder` / `radio_options` override the configured backends (used by tests and demos)."""
    data = Path(data_dir or os.environ.get("LORAPEDIA_DATA", "data"))
    (data / "zim").mkdir(parents=True, exist_ok=True)
    store = Store(data / "lorapedia.db")
    index = Index(data / "index.db")
    state = {"settings": store.load_settings()}
    sockets: set[WebSocket] = set()

    def settings() -> Settings:
        return state["settings"]

    def current_embedder() -> Embedder:
        if embedder:
            return embedder
        s = settings()
        return HashEmbedder() if s.embed_backend == "hash" else OllamaEmbedder(s.embed_base_url, s.embed_model)

    class _ConfiguredLLM:
        """Picks the backend per call so a UI settings change applies without a restart."""

        def __init__(self) -> None:
            self.real, self.mock = OpenAICompatLLM(settings), MockLLM()

        async def generate(self, system, user, *, temperature, max_tokens):
            backend = llm or (self.mock if settings().llm_backend == "mock" else self.real)
            return await backend.generate(system, user, temperature=temperature, max_tokens=max_tokens)

    configured_llm = _ConfiguredLLM()

    async def broadcast(event: dict) -> None:
        for ws in list(sockets):
            try:
                await ws.send_json(event)
            except Exception:
                sockets.discard(ws)

    retriever = RoutingRetriever(index, current_embedder)
    engine: Engine  # created below; the radio manager submits received packets to it
    radio = RadioManager(settings=settings, submit=lambda pkt: engine.submit(pkt), emit=broadcast,
                         **(radio_options or {}))
    engine = Engine(settings=settings, radio=radio, sim_radio=radio.sim, llm=configured_llm,
                    retriever=retriever, store=store, emit=broadcast)
    indexer = Indexer(index, current_embedder())

    def register_zim(path: Path) -> dict:
        try:
            reader = ZimReader(str(path))
            title = reader.metadata("Title", path.stem)
            index.add_zim(path.name, str(path.resolve()), title, reader.entry_count, reader.article_count)
        except Exception as exc:
            raise HTTPException(400, f"'{path}' is not a readable ZIM file ({exc.__class__.__name__}).") from exc
        return index.get_zim(path.name)

    for zim_file in sorted((data / "zim").glob("*.zim")):  # auto-register anything dropped in data/zim
        if not index.get_zim(zim_file.name):
            register_zim(zim_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = asyncio.create_task(engine.run_worker())
        radio.start()
        yield
        indexer.stop()
        worker.cancel()
        await radio.stop()

    app = FastAPI(title="LoRaPedia", lifespan=lifespan)
    app.state.engine, app.state.index, app.state.indexer, app.state.radio = engine, index, indexer, radio

    # ---- status / settings --------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        return {"radio": radio.active, "queue": engine.queue.qsize(), "knowledge_base": retriever.mode,
                "indexing": indexer.progress.state, **store.today_stats()}

    @app.get("/api/llm/status")
    async def llm_status():
        if llm or settings().llm_backend == "mock":
            return {"ok": True, "detail": "mock"}
        return await configured_llm.real.status()

    @app.get("/api/settings")
    async def get_settings() -> Settings:
        return settings()

    @app.put("/api/settings")
    async def put_settings(new: Settings) -> Settings:
        state["settings"] = new
        store.save_settings(new)
        radio.wake()  # connection settings may have changed
        return new

    # ---- radio --------------------------------------------------------------------------

    @app.get("/api/radio/status")
    async def radio_status():
        return radio.status()

    @app.post("/api/radio/retry")
    async def radio_retry():
        """The toast's [Retry] button: reconnect now and report what happened."""
        return await radio.retry()

    @app.post("/api/radio/use-simulator")
    async def radio_use_simulator():
        """The toast's [Use simulator] button."""
        put = settings().model_copy(update={"radio_method": "simulator", "radio_address": ""})
        state["settings"] = put
        store.save_settings(put)
        return await radio.retry()

    @app.get("/api/radio/ports")
    async def radio_ports():
        return {"serial": radio.ports()}

    @app.get("/api/radio/ble-scan")
    async def radio_ble_scan():
        try:
            from bleak import BleakScanner
            found = await BleakScanner.discover(timeout=5.0)
        except Exception as exc:
            raise HTTPException(503, f"Bluetooth scan failed ({exc.__class__.__name__}). Is Bluetooth on?") from exc
        return [{"name": d.name, "address": d.address} for d in found if d.name]

    @app.get("/api/radio/channels")
    async def radio_channels():
        return radio.channels()

    @app.get("/api/radio/nodes")
    async def radio_nodes():
        s = settings()
        return [{**n, "blocked": n["id"] in s.blocklist, "allowed": n["id"] in s.allowlist} for n in radio.nodes()]

    @app.post("/api/radio/nodes/{node_id}/{which}")
    async def radio_node_list(node_id: str, which: str, body: NodeToggle):
        """Add/remove a node from the blocklist or allowlist (node ids look like !a1b2c3d4)."""
        if which not in ("block", "allow"):
            raise HTTPException(404, "Use 'block' or 'allow'.")
        field = "blocklist" if which == "block" else "allowlist"
        current = [n for n in getattr(settings(), field) if n != node_id]
        new = settings().model_copy(update={field: current + [node_id] if body.on else current})
        state["settings"] = new
        store.save_settings(new)
        return {field: getattr(new, field)}

    # ---- simulator / log ----------------------------------------------------------------

    @app.post("/api/simulator/send")
    async def simulate(msg: SimMessage):
        return {"outcome": await engine.submit(Packet(**msg.model_dump(), simulated=True))}

    @app.get("/api/simulator/outbox")
    async def outbox():
        return [vars(s) for s in radio.sim.sent[-50:]]

    @app.get("/api/queries")
    async def queries(limit: int = 50):
        return store.recent_queries(limit)

    # ---- knowledge base -----------------------------------------------------------------

    @app.get("/api/kb/library")
    async def library():
        return index.list_zims()

    @app.post("/api/kb/library/import")
    async def import_zim(body: ImportZim):
        path = Path(body.path).expanduser()
        if not path.is_file():
            raise HTTPException(404, f"No file at {path}. Check the path and try again.")
        return register_zim(path)

    @app.delete("/api/kb/library/{name}")
    async def remove_zim(name: str):
        if not index.get_zim(name):
            raise HTTPException(404, "Not in the library.")
        if indexer.progress.state in ("running", "paused") and indexer.progress.zim == name:
            raise HTTPException(409, "Stop indexing this file before removing it.")
        index.remove_zim(name)
        return {"removed": name}

    @app.post("/api/kb/index/start")
    async def index_start(body: IndexStart):
        if not index.get_zim(body.zim):
            raise HTTPException(404, "Not in the library.")
        indexer.embedder = current_embedder()
        try:
            indexer.start(body.zim, max_articles=body.max_articles, lead_only=body.lead_only)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return indexer.progress.as_dict()

    @app.post("/api/kb/index/pause")
    async def index_pause():
        indexer.pause()
        return indexer.progress.as_dict()

    @app.post("/api/kb/index/resume")
    async def index_resume():
        indexer.resume()
        return indexer.progress.as_dict()

    @app.post("/api/kb/index/stop")
    async def index_stop():
        indexer.stop()
        return indexer.progress.as_dict()

    @app.get("/api/kb/index/status")
    async def index_status():
        return indexer.progress.as_dict()

    @app.get("/api/kb/stats")
    async def kb_stats():
        return index.stats()

    @app.get("/api/kb/search")
    async def kb_search(q: str, k: int = 8):
        """Search playground: ranked chunks with per-signal scores."""
        chunks = await retriever.search(q, max(1, min(k, 20)))
        return {"mode": retriever.mode,
                "results": [{"title": c.title, "section": c.section, "text": c.text, "score": c.score,
                             "detail": c.detail} for c in chunks]}

    # ---- live feed ----------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws_feed(ws: WebSocket):
        await ws.accept()
        sockets.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            sockets.discard(ws)

    return app

