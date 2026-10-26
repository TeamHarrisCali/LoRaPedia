"""Resumable ZIM downloads and the Kiwix catalog. The only part of LoRaPedia that needs internet,
and only when the user asks for it."""

import asyncio
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

KIWIX_WIKIPEDIA = "https://download.kiwix.org/zim/wikipedia/"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][\w.\-]*\.zim$")

VARIANTS = {  # kiwix naming: wikipedia_en_<scope>_<flavour>_<date>.zim
    "top_mini": "Top ~50k articles, intro paragraphs only, no images. Smallest. Best first install.",
    "top_nopic": "Top ~50k articles, full text, no images.",
    "top_maxi": "Top ~50k articles, full text with images.",
    "all_mini": "All of English Wikipedia, intro paragraphs only. Good coverage, moderate size.",
    "all_nopic": "All of English Wikipedia, full text, no images. Large (tens of GB).",
    "all_maxi": "All of English Wikipedia with images. Very large. Not recommended.",
}


@dataclass
class Job:
    id: str
    url: str
    filename: str
    state: str = "running"  # running | paused | done | error | cancelled
    bytes: int = 0
    total: int = 0
    speed: float = 0.0  # bytes/s
    eta_s: float | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class DownloadError(RuntimeError):
    pass


def filename_from_url(url: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise DownloadError("Only http(s) links are supported.")
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if not _SAFE_NAME.match(name):
        raise DownloadError("That link does not point to a .zim file.")
    return name


class Downloader:
    def __init__(self, dest_dir: Path, on_done: Callable[[Path], Awaitable[None]] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.dest_dir, self.on_done, self._transport = Path(dest_dir), on_done, transport
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._signal: dict[str, str] = {}  # job id -> "pause" | "cancel"

    def start(self, url: str) -> Job:
        name = filename_from_url(url)
        job = Job(id=name, url=url, filename=name)
        existing = self.jobs.get(name)
        if existing and existing.state == "running":
            return existing
        self.jobs[name] = job
        return self._launch(job)

    def resume(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        if job.state in ("paused", "error", "cancelled"):
            job.state, job.error = "running", None
            self._launch(job)
        return job

    def pause(self, job_id: str) -> None:
        if self.jobs[job_id].state == "running":
            self._signal[job_id] = "pause"

    def cancel(self, job_id: str) -> None:
        job = self.jobs[job_id]
        if job.state == "running":
            self._signal[job_id] = "cancel"
        else:
            job.state = "cancelled"
            self._part(job).unlink(missing_ok=True)

    async def wait(self, job_id: str) -> None:
        await self._tasks[job_id]

    def _part(self, job: Job) -> Path:
        return self.dest_dir / f"{job.filename}.part"

    def _launch(self, job: Job) -> Job:
        self._signal.pop(job.id, None)
        self._tasks[job.id] = asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: Job) -> None:
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        part, final = self._part(job), self.dest_dir / job.filename
        offset = part.stat().st_size if part.exists() else 0  # resume where we left off
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=True,
                                         transport=self._transport) as client:
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                async with client.stream("GET", job.url, headers=headers) as resp:
                    if resp.status_code == 416:  # asked past the end: the .part is already complete
                        job.bytes = job.total = offset
                    elif resp.status_code not in (200, 206):
                        raise DownloadError(f"The server answered {resp.status_code}.")
                    else:
                        if resp.status_code == 200:  # server ignored Range: start over
                            offset = 0
                        length = int(resp.headers.get("content-length", 0))
                        job.total = offset + length if length else 0
                        job.bytes = offset
                        await self._stream(resp, job, part, append=offset > 0)
            signal = self._signal.pop(job.id, None)
            if signal == "pause":
                job.state, job.speed = "paused", 0.0
            elif signal == "cancel":
                job.state, job.speed = "cancelled", 0.0
                part.unlink(missing_ok=True)
            else:
                if job.total and job.bytes != job.total:
                    raise DownloadError("The connection dropped early. Press Resume to continue.")
                part.replace(final)
                job.state, job.speed, job.eta_s = "done", 0.0, 0
                if self.on_done:
                    await self.on_done(final)
        except httpx.HTTPError as exc:
            job.state, job.speed = "error", 0.0
            job.error = f"Network problem ({exc.__class__.__name__}). Progress is kept: press Resume to continue."
        except Exception as exc:
            job.state, job.speed, job.error = "error", 0.0, str(exc)

    async def _stream(self, resp: httpx.Response, job: Job, part: Path, append: bool) -> None:
        window: list[tuple[float, int]] = []
        with open(part, "ab" if append else "wb") as f:
            async for chunk in resp.aiter_bytes():  # as received, so a dropped link loses nothing already sent
                f.write(chunk)
                job.bytes += len(chunk)
                now = time.monotonic()
                window = [(t, b) for t, b in window if now - t < 3.0] + [(now, job.bytes)]
                if len(window) > 1 and now > window[0][0]:
                    job.speed = (job.bytes - window[0][1]) / (now - window[0][0])
                    job.eta_s = round((job.total - job.bytes) / job.speed) if job.total and job.speed else None
                if self._signal.get(job.id):
                    break
                await asyncio.sleep(0)


_ROW = re.compile(r'href="(wikipedia_en_([a-z]+_[a-z]+)_(\d{4}-\d{2})\.zim)"[^\n]*?(\d{4}-\d{2}-\d{2}) [\d:]+\s+([\d.]+[KMGT])')


async def fetch_catalog(transport: httpx.AsyncBaseTransport | None = None, url: str = KIWIX_WIKIPEDIA) -> list[dict]:
    """Latest English Wikipedia ZIM of each variant from Kiwix's public listing."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, transport=transport) as client:
            resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise DownloadError("Cannot reach download.kiwix.org. This step needs internet; everything else works "
                            f"offline. You can also import a ZIM file you already have. ({exc.__class__.__name__})") from exc
    latest: dict[str, dict] = {}
    for name, variant, month, _, size in _ROW.findall(resp.text):
        if variant in VARIANTS and (variant not in latest or month > latest[variant]["date"]):
            latest[variant] = {"variant": variant, "filename": name, "date": month, "size": size,
                               "url": url + name, "description": VARIANTS[variant]}
    return [latest[v] for v in VARIANTS if v in latest]
