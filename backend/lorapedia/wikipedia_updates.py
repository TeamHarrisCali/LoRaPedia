"""Keeping installed Wikipedia packages current when the computer is online.

Kiwix publishes a new edition of each Wikipedia file about monthly. This looks at Kiwix's public listing (one small page, about
once a day) and compares the newest edition of each INSTALLED package with the one installed. If the setting allows, it installs the
newer edition through the same safe path as a manual install (disk and embedding-model checks first, download that resumes,
indexing, and the old edition is retired only after the new one is fully ready), but only when the node is quiet.

Rules that keep an automatic update from doing harm:
* Only packages you installed are touched, and only their OLDER EDITION is replaced: never a different package, never your own files.
* It never rebuilds the index on its own (that happens only if you change the embedding model, which is a decision for a person).
* It never starts without enough disk space for the new edition plus its index, and says so instead.
* Nothing is checked or downloaded unless the settings switches are on; "Check now" always works.
"""
import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

import httpx

from . import packages
from .config import Settings
from .download import DownloadError, fetch_catalog
from .store import Store

log = logging.getLogger(__name__)

KEY = "wikipedia_updates"
CHECK_EVERY_S = 24 * 3600
RETRY_UNREACHED_S = 3 * 3600
RETRY_FAILED_S = 24 * 3600     # an update that could not start (disk, embedder...) is tried again after a day
EDITION_RE = re.compile(r"_(\d{4}-\d{2})\.zim$")


def edition(filename: str) -> str | None:
    m = EDITION_RE.search(filename or "")
    return m.group(1) if m else None


class WikipediaUpdater:
    def __init__(
        self,
        *,
        store: Store,
        settings: Callable[[], Settings],
        list_zims: Callable[[], list[dict]],
        install: Callable[..., Awaitable[dict]],
        quiet: Callable[[], str | None],
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self._settings, self._list_zims, self._install, self._quiet = store, settings, list_zims, install, quiet
        self._transport, self._clock = transport, clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # ---- state -------------------------------------------------------------------------------------------------------
    def _state(self) -> dict:
        d = self.store.get_json(KEY, {})
        return d if isinstance(d, dict) else {}

    def _save(self, **changes) -> dict:
        state = {**self._state(), **changes}
        self.store.set_json(KEY, state)
        return state

    def installed(self) -> dict[str, list[dict]]:
        """package id -> its installed library files (name, edition), newest edition first."""
        out: dict[str, list[dict]] = {}
        for z in self._list_zims():
            pkg = packages.package_for_filename(z["name"])
            ed = edition(z["name"])
            if pkg and ed:
                out.setdefault(pkg["id"], []).append({"name": z["name"], "edition": ed})
        for files in out.values():
            files.sort(key=lambda f: f["edition"], reverse=True)
        return out

    def due(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        if not self._settings().wikipedia_update_check or not self.installed():
            return False
        state = self._state()
        attempt = state.get("attempt")
        if not isinstance(attempt, (int, float)) or attempt > now + 60:
            return True
        return now - attempt >= (CHECK_EVERY_S if state.get("reached") else RETRY_UNREACHED_S)

    # ---- the check ---------------------------------------------------------------------------------------------------
    async def check(self) -> dict:
        """Ask Kiwix which edition is newest and record which installed packages are behind. Never raises for a network problem."""
        now = self._clock()
        async with self._lock:
            installed = self.installed()
            try:
                catalog = {c["variant"]: c for c in await fetch_catalog(self._transport)}
            except DownloadError as exc:
                self._save(attempt=now, reached=False, error=str(exc))
                return {"ok": False, "message": str(exc)}
            available = {}
            for pkg_id, files in installed.items():
                pkg = packages.BY_ID[pkg_id]
                entry = catalog.get(pkg["variant"])
                if entry and entry["date"] > files[0]["edition"]:
                    available[pkg_id] = {"installed": files[0]["edition"], "latest": entry["date"],
                                         "size_mb": packages.size_to_mb(entry["size"]) or pkg["approx_mb"],
                                         "replaces": [f["name"] for f in files]}
            self._save(attempt=now, checked=now, reached=True, error=None, available=available)
            return {"ok": True, "message": "Checked.", "available": len(available)}

    # ---- applying ----------------------------------------------------------------------------------------------------
    async def apply(self, package_id: str, who: str = "user") -> dict:
        """Install the newer edition of one package and retire its older edition when ready. Raises whatever install raises."""
        info = self._state().get("available", {}).get(package_id)
        if not info:
            raise LookupError("No newer edition of that package is known. Use Check now first.")
        result = await self._install(package_id, replace=False, replace_names=info["replaces"], allow_rebuild=False)
        last = dict(self._state().get("last", {}))
        last[package_id] = {"result": "started", "to": info["latest"], "ts": self._clock(), "who": who}
        self._save(last=last)
        log.info("wikipedia update of %s to %s started by %s", package_id, info["latest"], who)
        return result

    async def maybe_auto_apply(self) -> str:
        """Start at most one update, if switched on, the node is quiet, and nothing was tried too recently. Returns what happened."""
        if not self._settings().wikipedia_auto_update:
            return "auto_update off"
        state = self._state()
        available, last = state.get("available", {}), state.get("last", {})
        now = self._clock()
        for pkg_id, info in available.items():
            tried = last.get(pkg_id, {})
            if tried.get("to") == info["latest"] and now - tried.get("ts", 0) < RETRY_FAILED_S:
                continue  # started or failed recently: do not hammer
            why = self._quiet()
            if why:
                return f"waiting: {why}"
            try:
                await self.apply(pkg_id, who="automatic")
            except Exception as exc:  # InstallError (disk, embedder, rebuild needed...) or anything unexpected
                message = getattr(exc, "message", None) or str(exc)
                new_last = dict(self._state().get("last", {}))
                new_last[pkg_id] = {"result": "skipped", "to": info["latest"], "ts": now, "message": message}
                self._save(last=new_last)
                log.warning("wikipedia update of %s not started: %s", pkg_id, message)
                return f"not started: {message}"
            return f"started {pkg_id}"
        return "nothing to do"

    # ---- status for the UI ---------------------------------------------------------------------------------------------
    def status(self) -> dict:
        s, state = self._settings(), self._state()
        installed = self.installed()
        available = {k: v for k, v in state.get("available", {}).items()
                     if k in installed and v["latest"] > installed[k][0]["edition"]}  # drop ones installed since
        return {
            "check_enabled": s.wikipedia_update_check, "auto_update": s.wikipedia_auto_update,
            "checked": state.get("checked"), "error": state.get("error"),
            "available": [{"package": k, "name": packages.BY_ID[k]["name"], **v,
                           "needs_mb": round(v["size_mb"] * packages.DISK_MARGIN)} for k, v in available.items()],
            "last": state.get("last", {}),
        }

    # ---- the background loop ------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(120)  # let start-up (and the radio) settle first
        while True:
            try:
                if self.due():
                    await self.check()
                await self.maybe_auto_apply()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("wikipedia update loop error")
            await asyncio.sleep(1800)
