"""Backups of the settings and question-log database (lorapedia.db), made and restored from the web interface.

What a backup holds: your settings, the question log, and the wizard/update bookkeeping. It does NOT hold the Wikipedia files or their
search index (gigabytes; the index is rebuilt from the files, the files are downloaded again), so it is small and quick, and it is
safe to take while the node is running (SQLite's online backup). Every file is checked before it is kept or restored, and restoring
first saves what is being replaced, so a restore can itself be undone.

The folder is `backups/` inside the data folder: it protects against a bad change or a bad update, not against losing the disk or the
Docker volume. Download one to keep a copy somewhere else.
"""
import asyncio
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable

from .config import Settings
from .store import Store

log = logging.getLogger(__name__)

# kind, an optional label (the pre-update backup names the two versions), then the time it was taken
NAME_RE = re.compile(r"^(manual|auto|pre-update|before-restore|uploaded)(?:-[0-9A-Za-z.]+)*-\d{8}-\d{6}\.db$")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
AUTO_EVERY_S = 24 * 3600
CHECK_EVERY_S = 3600


class BackupError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _stamp(ts: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))


def taken_at(path: Path) -> float:
    """When a backup was taken, from the time in its name: copying a file or a volume resets modification times, the name does not."""
    m = re.search(r"-(\d{8}-\d{6})\.db$", path.name)
    try:
        return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S")) if m else path.stat().st_mtime
    except (ValueError, OverflowError):
        return path.stat().st_mtime


def kind_of(name: str) -> str:
    m = NAME_RE.match(name)
    return m.group(1) if m else ""


def verify(path: Path) -> None:
    """Raise BackupError unless `path` is an intact LoRaPedia database whose settings this version can load."""
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupError("That is not a LoRaPedia backup.") from exc
    try:
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("That backup is damaged (it failed its integrity check).")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"kv", "queries"} <= tables:
                raise BackupError("That is not a LoRaPedia backup (it has no settings or question log).")
            row = db.execute("SELECT v FROM kv WHERE k='settings'").fetchone()
        except sqlite3.Error as exc:
            raise BackupError("That is not a LoRaPedia backup.") from exc
        if row:
            try:
                Settings.model_validate_json(row[0])
            except ValueError as exc:
                raise BackupError("That backup's settings cannot be read by this version of LoRaPedia.") from exc
    finally:
        db.close()


class BackupService:
    def __init__(self, store: Store, data_dir: Path, settings: Callable[[], Settings], clock: Callable[[], float] = time.time) -> None:
        self.store, self.data_dir, self._settings, self._clock = store, Path(data_dir), settings, clock
        self._task: asyncio.Task | None = None

    @property
    def folder(self) -> Path:
        return self.data_dir / "backups"

    # ---- making and listing ------------------------------------------------------------------------------------------
    def create(self, kind: str = "manual") -> dict:
        """Take a verified backup now. Raises BackupError (and leaves nothing behind) if it cannot be made."""
        name = f"{kind}-{_stamp(self._clock())}.db"
        dest = self.folder / name
        n = 1
        while dest.exists():  # two in the same second
            n += 1
            name = f"{kind}-{n}-{_stamp(self._clock())}.db"
            dest = self.folder / name
        tmp = self.folder / (name + ".tmp")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.store.backup_to(tmp)
            verify(tmp)
            tmp.replace(dest)
        except BackupError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise BackupError(f"The backup could not be made ({exc.__class__.__name__}).") from exc
        return self._describe(dest)

    def _describe(self, path: Path) -> dict:
        st = path.stat()
        return {"name": path.name, "kind": kind_of(path.name), "size": st.st_size, "ts": taken_at(path)}

    def list(self) -> list[dict]:
        if not self.folder.is_dir():
            return []
        paths = [p for p in self.folder.iterdir() if p.is_file() and NAME_RE.match(p.name)]
        paths.sort(key=lambda p: (-taken_at(p), -p.stat().st_mtime_ns))  # newest first; the file's own time breaks a tie within a second
        return [self._describe(p) for p in paths]

    def file(self, name: str) -> Path:
        """The path of a backup by name. Only names this module makes are accepted, so no path can escape the folder."""
        path = self.folder / name
        if not NAME_RE.match(name) or not path.is_file():
            raise LookupError("There is no backup with that name.")
        return path

    def delete(self, name: str) -> None:
        self.file(name).unlink()

    def prune_auto(self, keep: int) -> None:
        autos = [i for i in self.list() if i["kind"] == "auto"]
        for old in autos[keep:]:
            (self.folder / old["name"]).unlink(missing_ok=True)

    # ---- restoring and importing -------------------------------------------------------------------------------------
    def restore(self, name: str) -> dict:
        """Replace the live settings and question log with a backup. What was replaced is saved first (kind "before-restore")."""
        path = self.file(name)
        verify(path)
        saved = self.create("before-restore")  # if this fails nothing has been touched
        try:
            self.store.restore_from(path)
        except Exception as exc:
            log.exception("restore of %s failed", name)
            raise BackupError(f"The restore failed ({exc.__class__.__name__}). Your previous data is saved as {saved['name']}.") from exc
        return {"restored": name, "saved_previous_as": saved["name"]}

    def add_upload(self, data: bytes) -> dict:
        """Keep a backup file the user uploaded (for example from another machine), after checking it."""
        if not data:
            raise BackupError("That file is empty.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise BackupError(f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, so it is not a LoRaPedia backup.")
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"uploaded-{_stamp(self._clock())}.db"
        dest, tmp = self.folder / name, self.folder / (name + ".tmp")
        try:
            tmp.write_bytes(data)
            verify(tmp)
            tmp.replace(dest)
        except BackupError:
            tmp.unlink(missing_ok=True)
            raise
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise BackupError(f"The file could not be saved ({exc.__class__.__name__}).") from exc
        return self._describe(dest)

    # ---- automatic daily backup --------------------------------------------------------------------------------------
    def auto_due(self) -> bool:
        s = self._settings()
        if not s.auto_backup:
            return False
        newest = next((i for i in self.list() if i["kind"] == "auto"), None)
        return newest is None or self._clock() - newest["ts"] >= AUTO_EVERY_S

    def status(self) -> dict:
        s = self._settings()
        items = self.list()
        return {"items": items, "auto_backup": s.auto_backup, "backup_keep": s.backup_keep, "folder": "backups",
                "total_size": sum(i["size"] for i in items)}

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(60)  # let start-up settle first
        while True:
            try:
                if self.auto_due():
                    await asyncio.to_thread(self.create, "auto")
                    self.prune_auto(self._settings().backup_keep)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("automatic backup failed")
            await asyncio.sleep(CHECK_EVERY_S)
