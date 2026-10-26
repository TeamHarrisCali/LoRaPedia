"""FastAPI app: settings, simulator, knowledge base, query log, and a WebSocket feed for the UI."""

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, demo, fitter, system
from .download import Downloader, DownloadError, fetch_catalog
from .embed import EmbedError
from .ollama import PullManager, list_models, native_base
from . import packages

from .config import Settings
from .embed import Embedder, HashEmbedder, OllamaEmbedder
from .engine import Engine
from .index import Index, Indexer
from .llm.base import MockLLM
from .llm.openai_compat import OpenAICompatLLM
from .radio.base import Packet
from .radio.manager import RadioManager
from .radio.meshtastic_radio import RadioError
from .retrieval.hybrid import RoutingRetriever
from .sample import make_sample_zim
from .store import Store
from .updates import UpdateError, UpdateService
from .zim import ZimReader


class SimMessage(BaseModel):
    sender_id: str = "!sim00001"
    sender_name: str = "SIM"
    text: str
    is_dm: bool = True
    channel: int = 0


class ImportZim(BaseModel):
    path: str


class RadioTest(BaseModel):
    to: str  # a node id the radio has heard, e.g. "!a1b2c3d4"
    text: str


class NodeToggle(BaseModel):
    on: bool = True


class PullRequest(BaseModel):
    tag: str


class TryRequest(BaseModel):
    question: str
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class InstallRequest(BaseModel):
    replace: bool = False  # also remove other installed Wikipedia packages once this one is ready


class DownloadRequest(BaseModel):
    url: str


class IndexStart(BaseModel):
    zim: str
    max_articles: int | None = None  # e.g. 100000 for "top 100k articles" (ignored in fast mode)
    lead_only: bool = False
    mode: str = "fast"  # "fast": opening chunk of every article first, then the rest. "all": article by article


_NON_RADIO_OPTIONS = {"http_transport", "github_transport", "updater_transport", "environ"}


def create_app(data_dir: str | None = None, *, llm=None, embedder: Embedder | None = None,
               radio_options: dict | None = None) -> FastAPI:
    """`llm` / `embedder` / `radio_options` override the configured backends (used by tests and demos)."""
    data = Path(data_dir or os.environ.get("LORAPEDIA_DATA", "data"))
    (data / "zim").mkdir(parents=True, exist_ok=True)
    store = Store(data / "lorapedia.db")
    index = Index(data / "index.db")
    state = {"settings": store.load_settings()}
    sockets: set[WebSocket] = set()
    static = Path(os.environ.get("LORAPEDIA_STATIC") or Path(__file__).resolve().parents[2] / "frontend" / "dist")
    ui_dir = static if (static / "index.html").exists() else None

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
        if event.get("type") in ("query", "rx"):
            updates.touch()  # an update waits until the node has been quiet for a while
        for ws in list(sockets):
            try:
                await ws.send_json(event)
            except Exception:
                sockets.discard(ws)

    retriever = RoutingRetriever(index, current_embedder)
    engine: Engine  # created below; the radio manager submits received packets to it
    radio = RadioManager(settings=settings, submit=lambda pkt: engine.submit(pkt), emit=broadcast,
                         **{k: v for k, v in (radio_options or {}).items() if k not in _NON_RADIO_OPTIONS})
    engine = Engine(settings=settings, radio=radio, sim_radio=radio.sim, llm=configured_llm,
                    retriever=retriever, store=store, emit=broadcast, library_state=index.library_state)
    indexer = Indexer(index, current_embedder())

    def register_zim(path: Path) -> dict:
        try:
            reader = ZimReader(str(path))
            title = reader.metadata("Title", path.stem)
            index.add_zim(path.name, str(path.resolve()), title, reader.entry_count, reader.estimate_articles())
        except Exception as exc:
            raise HTTPException(400, f"'{path}' is not a readable ZIM file ({exc.__class__.__name__}).") from exc
        return index.get_zim(path.name)

    for zim_file in sorted((data / "zim").glob("*.zim")):  # auto-register anything dropped in data/zim
        if not index.get_zim(zim_file.name):
            register_zim(zim_file)

    installs: dict[str, dict] = {}  # filename -> {"package": id, "replace": [library names to drop when ready]}

    async def on_download_done(path: Path) -> None:
        register_zim(path)
        if path.name in installs:  # a one-click package: index it straight away
            asyncio.create_task(finish_install(path.name))

    def retire(names: list[str]) -> None:
        """Drop library entries and their index rows. Delete a file only if it is a Wikipedia package that
        LoRaPedia downloaded into its own zim folder; anything else (sample, the user's files) is kept."""
        for old in names:
            zim = index.get_zim(old)
            if not zim:
                continue
            index.remove_zim(old)
            old_path = Path(zim["path"])
            if packages.package_for_filename(old) and old_path.parent == (data / "zim").resolve() and old_path.exists():
                old_path.unlink()

    async def finish_install(name: str) -> None:
        """Index a freshly downloaded package, then retire what it replaces."""
        try:
            while indexer.progress.state in ("running", "paused"):  # one indexing job at a time
                await asyncio.sleep(1)
            indexer.embedder = current_embedder()
            old = installs[name]["replace"]
            # One vector table holds one embedder's vectors, so an index built differently must go first.
            rebuild = index.embedder_name() not in (None, indexer.embedder.name)
            if rebuild:
                retire(old)
            indexer.start(name, max_articles=None, lead_only=False, mode="fast")
            await indexer.wait()
            if indexer.progress.state == "done" and not rebuild:
                retire(old)  # keep the old package serving until the new one is fully ready
        except Exception:
            logging.getLogger(__name__).exception("finishing install of %s failed", name)

    downloader = Downloader(data / "zim", on_done=on_download_done, transport=(radio_options or {}).get("http_transport"))
    pulls = PullManager(transport=(radio_options or {}).get("http_transport"))

    def busy_reason() -> str | None:
        """Why an automatic update must wait (None = the node is quiet)."""
        if engine.pending_count:
            return "questions are waiting to be answered"
        if indexer.progress.state in ("running", "paused"):
            return "the Wikipedia index is being built"
        if any(j.state == "running" for j in downloader.jobs.values()):
            return "a download is running"
        if any(j["state"] == "running" for j in pulls.jobs.values()):
            return "a model is being downloaded"
        return None

    opts = radio_options or {}
    updates = UpdateService(store=store, settings=settings, data_dir=data, busy=busy_reason,
                            transport=opts.get("github_transport"), updater_transport=opts.get("updater_transport"),
                            environ=opts.get("environ"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = asyncio.create_task(engine.run_worker())
        radio.start()
        updates.start()
        yield
        indexer.stop()
        worker.cancel()
        updates.stop()
        await radio.stop()

    app = FastAPI(title="LoRaPedia", lifespan=lifespan)
    app.state.engine, app.state.index, app.state.indexer, app.state.radio, app.state.updates = engine, index, indexer, radio, updates

    # ---- status / settings --------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        return {"radio": radio.active, "queue": engine.pending_count, "knowledge_base": retriever.mode,
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

    @app.post("/api/radio/test-send")
    async def radio_test_send(body: RadioTest):
        """Send one direct message to a known node, to prove the radio can transmit. Never broadcasts."""
        if radio.active != "meshtastic":
            raise HTTPException(400, "No real radio is connected, so there is nothing to transmit with.")
        if body.to not in {n["id"] for n in radio.nodes()} or body.to == radio.node_id:
            raise HTTPException(400, "Pick another node this radio has heard (see the Nodes list on the Radio page).")
        s = settings()
        if fitter.byte_len(body.text) > s.byte_limit:
            raise HTTPException(400, f"That is {fitter.byte_len(body.text)} bytes; the limit is {s.byte_limit}.")
        try:
            await radio.send_text(body.text, body.to, s.channel_index, s.hop_limit)
        except RadioError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"sent": True, "to": body.to, "bytes": fitter.byte_len(body.text)}

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

    @app.post("/api/kb/sample")
    async def kb_sample():
        """One-click tiny ZIM (12 articles) so a new user can try the whole flow before downloading anything."""
        return register_zim(make_sample_zim(data / "zim" / "sample.zim"))

    @app.post("/api/demo/seed")
    async def demo_seed():
        return {"added": await asyncio.to_thread(demo.seed, store)}

    @app.post("/api/kb/index/start")
    async def index_start(body: IndexStart):
        if not index.get_zim(body.zim):
            raise HTTPException(404, "Not in the library.")
        indexer.embedder = current_embedder()
        try:
            indexer.start(body.zim, max_articles=body.max_articles, lead_only=body.lead_only, mode=body.mode)
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

    # ---- system / models ----------------------------------------------------------------

    @app.get("/api/system/hardware")
    async def hardware():
        hw = await asyncio.to_thread(system.detect_hardware, data)
        return {"hardware": hw, "suggestions": system.suggest_models(hw)}

    @app.get("/api/system/info")
    async def system_info():
        return {"version": __version__, "data_dir": str(data.resolve()), "static": ui_dir is not None,
                "disk_free_gb": round(shutil.disk_usage(data).free / 1e9, 1)}

    @app.get("/api/models")
    async def models():
        try:
            return {"installed": await list_models(native_base(settings()), (radio_options or {}).get("http_transport")),
                    "error": None}
        except Exception as exc:
            return {"installed": [], "error": f"Cannot reach Ollama at {native_base(settings())}. Is it running? "
                                              f"({exc.__class__.__name__})"}

    @app.get("/api/settings/defaults")
    async def settings_defaults() -> Settings:
        """For the UI's "reset to default" buttons."""
        return Settings()

    @app.post("/api/models/pull")
    async def models_pull(body: PullRequest):
        return pulls.start(native_base(settings()), body.tag.strip())

    @app.get("/api/models/pulls")
    async def models_pulls():
        return list(pulls.jobs.values())

    @app.post("/api/model/try")
    async def model_try(body: TryRequest):
        """The Model page's "try it" box: full pipeline with unsaved overrides, nothing sent or logged."""
        overrides = {k: v for k, v in (("system_prompt", body.system_prompt), ("temperature", body.temperature),
                                       ("max_tokens", body.max_tokens)) if v is not None}
        s = settings().model_copy(update=overrides)
        started = asyncio.get_running_loop().time()
        try:
            gen = await engine.generate(body.question, s)
        except Exception as exc:  # LLMError and anything else: report it, don't crash the page
            raise HTTPException(502, str(exc) or exc.__class__.__name__) from exc
        return {"parts": gen.parts, "bytes": [fitter.byte_len(p) for p in gen.parts], "byte_limit": s.byte_limit,
                "raw": gen.raw, "compressed": gen.compressed, "fallback": gen.fallback, "latency_s": round(asyncio.get_running_loop().time() - started, 2),
                "chunks": [{"title": c.title, "section": c.section, "score": c.score} for c in gen.chunks]}

    @app.get("/api/wizard")
    async def wizard_get():
        return store.get_json("wizard", {"done": [], "skipped": [], "finished": False, "step": 0})

    @app.put("/api/wizard")
    async def wizard_put(body: dict):
        keep = {k: body[k] for k in ("done", "skipped", "finished", "step") if k in body}
        store.set_json("wizard", {**store.get_json("wizard", {"done": [], "skipped": [], "finished": False, "step": 0}), **keep})
        return store.get_json("wizard")

    # ---- stats / exports ----------------------------------------------------------------

    @app.get("/api/stats/activity")
    async def activity():
        return {"hours": store.activity_24h(), **store.today_stats()}

    @app.get("/api/queries/export.csv")
    async def export_csv():
        import csv
        import io
        import time as _time

        def rows():
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["time", "sender_id", "sender_name", "question", "answer", "bytes", "byte_limit", "parts",
                        "latency_s", "sources"])
            yield buf.getvalue()
            for r in store.export_rows():
                buf.seek(0), buf.truncate()
                w.writerow([_time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(r["ts"])), r["sender_id"],
                            r["sender_name"], r["question"], r["answer"].replace("\n", " | "), r["bytes"],
                            r["byte_limit"], r["parts"], r["latency_s"], r["sources"]])
                yield buf.getvalue()
        return StreamingResponse(rows(), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="lorapedia-log.csv"'})

    @app.get("/api/settings/backup")
    async def settings_backup():
        return Response(settings().model_dump_json(indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="lorapedia-settings.json"'})

    # ---- ZIM catalog + downloads (the only online step, always user-initiated) -----------

    @app.get("/api/kb/catalog")
    async def kb_catalog():
        try:
            return await fetch_catalog((radio_options or {}).get("http_transport"))
        except DownloadError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/kb/downloads")
    async def dl_start(body: DownloadRequest):
        try:
            return downloader.start(body.url.strip()).as_dict()
        except DownloadError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/kb/downloads")
    async def dl_list():
        return [j.as_dict() for j in downloader.jobs.values()]

    @app.post("/api/kb/downloads/{job_id}/{action}")
    async def dl_action(job_id: str, action: str):
        if job_id not in downloader.jobs or action not in ("pause", "resume", "cancel"):
            raise HTTPException(404, "No such download.")
        getattr(downloader, action)(job_id)
        return downloader.jobs[job_id].as_dict()

    # ---- one-click Wikipedia packages (modelled on Project NOMAD's curated list) ---------

    def package_state(pkg: dict) -> dict:
        prefix = f"wikipedia_en_{pkg['variant']}_"
        zim = next((z for z in index.list_zims() if z["name"].startswith(prefix)), None)
        job = next((j for j in downloader.jobs.values() if j.filename.startswith(prefix) and j.state != "done"), None)
        out = {**pkg, "needs_mb": round(pkg["approx_mb"] * packages.DISK_MARGIN), "state": "available", "progress": 0.0, "filename": None, "articles": 0, "detail": None}
        if job:
            pct = job.bytes / job.total if job.total else 0.0
            out.update(filename=job.filename, progress=pct, detail=job.error)
            out["state"] = {"running": "downloading", "paused": "paused", "error": "error"}.get(job.state, "available")
            return out
        if zim:
            ip = indexer.progress
            out.update(filename=zim["name"], articles=zim["indexed_articles"])
            if ip.zim == zim["name"] and ip.state in ("running", "paused"):
                out.update(state="indexing", progress=ip.entries_done / ip.entries_total if ip.entries_total else 0.0)
            elif ip.zim == zim["name"] and ip.state == "error":
                out.update(state="error", detail=ip.error)
            elif zim["name"] in installs and zim["status"] != "done":
                out.update(state="indexing", progress=0.0)  # downloaded, waiting its turn
            elif zim["status"] == "done":
                out["state"] = "ready"
            else:
                out["state"] = "downloaded"  # in the library but not indexed (e.g. imported by hand)
        return out

    @app.get("/api/kb/packages")
    async def kb_packages():
        return [package_state(p) for p in packages.PACKAGES]

    @app.post("/api/kb/packages/{package_id}/install")
    async def kb_package_install(package_id: str, body: InstallRequest):
        """Download, add to the library and index, in one step. Checks the cheap failure causes first."""
        pkg = packages.BY_ID.get(package_id)
        if not pkg:
            raise HTTPException(404, "Unknown package.")
        # An embedding model that is missing would fail only after a long download; find out now.
        try:
            await current_embedder().embed(["probe"])
        except EmbedError as exc:
            raise HTTPException(400, f"{exc} (Or turn on keyword-only search on the Model page.)") from exc
        try:
            info = await packages.resolve(pkg, (radio_options or {}).get("http_transport"))
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        if index.get_zim(info["filename"]):
            raise HTTPException(409, f"{pkg['name']} ({info['version']}) is already installed.")
        free_mb = shutil.disk_usage(data).free / 1e6
        need_mb = info["size_mb"] * packages.DISK_MARGIN
        part = data / "zim" / f"{info['filename']}.part"
        need_mb -= (part.stat().st_size / 1e6) if part.exists() else 0  # a resumed download needs less
        if free_mb < need_mb:
            raise HTTPException(400, f"Not enough disk space: {pkg['name']} needs about {need_mb / 1000:.1f} GB "
                                     f"(including its search index) and {free_mb / 1000:.1f} GB is free.")
        built_with = index.embedder_name()
        rebuild = built_with is not None and built_with != current_embedder().name
        if rebuild and not body.replace:
            raise HTTPException(409, f"Your search index was built with '{built_with}' but you now use "
                                     f"'{current_embedder().name}'. Installing means rebuilding the index from "
                                     "scratch (your files are not deleted). Confirm to rebuild and install.")
        replace = []
        if rebuild:
            replace = [z["name"] for z in index.list_zims()]  # everything must be re-indexed with the new embedder
        elif body.replace:
            replace = [z["name"] for z in index.list_zims() if packages.package_for_filename(z["name"])]
        installs[info["filename"]] = {"package": pkg["id"], "replace": replace}
        try:
            downloader.start(info["url"])
        except DownloadError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**package_state(pkg), "version": info["version"], "size_mb": info["size_mb"], "source": info["source"]}

    # ---- app updates (GitHub releases; applied by the separate updater container) -----------------

    @app.get("/api/updates/status")
    async def updates_status():
        return await updates.status()

    @app.post("/api/updates/check")
    async def updates_check():
        """The "Check now" button: always allowed, even with the periodic check off."""
        result = await updates.check(manual=True)
        return {**await updates.status(), "result": result}

    @app.post("/api/updates/apply")
    async def updates_apply():
        """Takes no parameters: the updater decides what to install from GitHub itself and verifies its signature."""
        try:
            return await updates.apply(who="user")
        except UpdateError as exc:
            raise HTTPException(400, str(exc)) from exc

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

    if ui_dir:  # serve the built UI; hash routing means no SPA fallback is needed
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app

