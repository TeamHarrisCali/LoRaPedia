"""Street-map pictures (tiles) from OpenStreetMap, fetched only when the operator turns them on and kept on this computer.

LoRaPedia works without the internet, so the Map page draws a plain grid by default. With "Street map" on (Settings -> Mesh and radio),
the page asks THIS server for the tiles it needs; the server downloads each one once from tile.openstreetmap.org and keeps it, so the
places you have looked at keep working offline. The browser never talks to OpenStreetMap itself.

Courteous by design (https://operations.osmfoundation.org/policies/tiles/): tiles are fetched only for what is being looked at (there is
no bulk or ahead-of-time download), at most two at a time, with a User-Agent that names this project, kept for a month before they are
asked for again, and a failed tile is not retried for a minute. What OpenStreetMap can see: this computer's address and which tiles
(places) were asked for. Nothing about the mesh or any node is ever sent. The address is fixed: it is not a setting, so no one can make
the server fetch from somewhere else through it.
"""
import asyncio
import logging
import os
import time
from pathlib import Path

import httpx

from . import __version__

log = logging.getLogger(__name__)

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "© OpenStreetMap contributors"
USER_AGENT = f"LoRaPedia/{__version__} (+https://github.com/TeamHarrisCali/LoRaPedia)"
MAX_ZOOM = 19
MAX_TILE_BYTES = 512 * 1024      # a real tile is 5-60 KB; anything far larger is not one
CACHE_LIMIT_BYTES = 500 * 1024 * 1024
REFRESH_AFTER_S = 30 * 86400     # older tiles are asked for again (and the old one is used if that fails)
RETRY_AFTER_S = 60               # a tile that failed is not asked for again for this long
PNG = b"\x89PNG\r\n\x1a\n"


class TileError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class TileCache:
    def __init__(self, root: Path, *, url: str = TILE_URL, transport: httpx.AsyncBaseTransport | None = None,
                 limit_bytes: int = CACHE_LIMIT_BYTES, clock=time.time):
        self.root = Path(root)
        self.url = url
        self.limit = limit_bytes
        self._transport = transport
        self._clock = clock
        self._slots = asyncio.Semaphore(2)               # OpenStreetMap asks clients to use at most two connections
        self._locks: dict[tuple[int, int, int], asyncio.Lock] = {}
        self._failed: dict[tuple[int, int, int], float] = {}
        self._writes = 0

    # ---- what is kept -------------------------------------------------------------------------

    def _path(self, z: int, x: int, y: int) -> Path:
        return self.root / str(z) / str(x) / f"{y}.png"      # built from integers only: nothing a caller sends can leave the folder

    def _files(self):
        if not self.root.exists():
            return
        for dirpath, _dirs, names in os.walk(self.root):
            for n in names:
                if n.endswith(".png"):
                    yield Path(dirpath) / n

    def stats(self) -> dict:
        count = total = 0
        for p in self._files():
            try:
                total += p.stat().st_size
                count += 1
            except OSError:
                pass
        return {"tiles": count, "bytes": total, "limit_bytes": self.limit}

    def clear(self) -> int:
        n = 0
        for p in list(self._files()):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        self._failed.clear()
        return n

    def _prune(self) -> None:
        """Over the limit: delete the least recently written tiles until it is back to 90%."""
        files = []
        for p in self._files():
            try:
                st = p.stat()
                files.append((st.st_mtime, st.st_size, p))
            except OSError:
                pass
        total = sum(s for _, s, _ in files)
        if total <= self.limit:
            return
        for _, size, p in sorted(files):
            if total <= self.limit * 0.9:
                break
            try:
                p.unlink()
                total -= size
            except OSError:
                pass

    # ---- one tile -----------------------------------------------------------------------------

    @staticmethod
    def check(z: int, x: int, y: int) -> None:
        if not 0 <= z <= MAX_ZOOM or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise TileError(404, "No such tile")

    async def get(self, z: int, x: int, y: int) -> bytes:
        self.check(z, x, y)
        key = (z, x, y)
        path = self._path(z, x, y)
        stale: bytes | None = None
        cached = self._read(path)
        if cached is not None:
            data, age = cached
            if age < REFRESH_AFTER_S:
                return data
            stale = data
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                cached = self._read(path)                  # another request for the same tile may have just stored it
                if cached is not None and cached[1] < REFRESH_AFTER_S:
                    return cached[0]
                if self._clock() - self._failed.get(key, -RETRY_AFTER_S) < RETRY_AFTER_S:
                    if stale is not None:
                        return stale
                    raise TileError(502, "The tile could not be fetched a moment ago")
                try:
                    data = await self._fetch(z, x, y)
                except TileError:
                    self._failed[key] = self._clock()
                    if stale is not None:
                        return stale
                    raise
                self._failed.pop(key, None)
                self._store(path, data)
                return data
        finally:
            if not lock.locked():
                self._locks.pop(key, None)

    def _read(self, path: Path) -> tuple[bytes, float] | None:
        try:
            st = path.stat()
            return path.read_bytes(), self._clock() - st.st_mtime
        except OSError:
            return None

    def _store(self, path: Path, data: bytes) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not keep a map tile: %s", exc)       # still shown this time, fetched again next time
            return
        self._writes += 1
        if self._writes % 100 == 0:
            self._prune()

    async def _fetch(self, z: int, x: int, y: int) -> bytes:
        async with self._slots:
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=10, follow_redirects=False,
                                             headers={"User-Agent": USER_AGENT}) as client:
                    r = await client.get(self.url.format(z=z, x=x, y=y))
            except httpx.HTTPError as exc:
                raise TileError(502, f"Could not reach the map server ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise TileError(502, f"The map server answered {r.status_code}")
        body = r.content
        if not body.startswith(PNG) or len(body) > MAX_TILE_BYTES:
            raise TileError(502, "The map server sent something that is not a map tile")
        return body
