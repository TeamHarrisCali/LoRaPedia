"""Looking for a newer release of LoRaPedia on GitHub, and (in a Docker install) asking the updater container to apply it.

READ THIS FIRST, it is the whole security story of this file:

* The only thing the CHECK ever fetches is one small JSON document of REPO (a constant: no request, setting or environment variable
  changes it): `releases/latest` on the stable channel, or the last few releases on the beta channel (Settings -> Updates, off by
  default), over HTTPS with the certificate verified, redirects refused, a timeout and a size cap. It is parsed strictly: the tag must
  be `vX.Y.Z` (or, on the beta channel only, `vX.Y.Z-beta.N` flagged by GitHub as a pre-release) and nothing else from it is trusted.
  Release notes are shown as plain text only, and the only link ever given to the page is built from the validated tag. Nothing is
  sent but the GET (a User-Agent with the version).
* This process NEVER applies an update itself and never holds the Docker socket. In a Docker install it asks the separate updater
  container (updater/updater.py) over the private compose network with a shared token. The request carries NO version or image,
  only (on the beta channel) the word "beta": the updater decides what to install from GitHub itself, verifies its signature, and
  rolls back on failure. Anything that is not a
  Docker install is only TOLD that a release exists, and how to update by hand.
* Before asking, a verified backup of the settings and log database is made; if that fails, nothing is asked.
* The periodic check is on by default and can be switched off in Settings -> Updates ("Check now" always works).
"""
import asyncio
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable

import httpx

from . import __version__
from .config import Settings
from .store import Store

log = logging.getLogger(__name__)

REPO = "TeamHarrisCali/LoRaPedia"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
BETA_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=5"
CHANNELS = ("stable", "beta")
RELEASE_URL_PREFIX = f"https://github.com/{REPO}/releases/"
USER_AGENT = f"lorapedia/{__version__}"

CHECK_EVERY_S = 24 * 3600        # ask at most once a day when GitHub answered
RETRY_UNREACHED_S = 3 * 3600     # after an attempt that never reached GitHub (offline), at most every three hours
MIN_GAP_S = 5                    # "Check now" cannot be repeated faster than this
MAX_BLOCK_S = 3600               # how long a "rate limited" answer can hold the check back
MAX_RESPONSE_BYTES = 256 * 1024
MAX_LIST_BYTES = 1024 * 1024     # the beta channel reads a short list of releases, each with its notes
NOTES_MAX = 1500
IDLE_BEFORE_AUTO_APPLY_S = 300   # no question answered for this long before an automatic update is started
BACKUPS_KEPT = 5

KEY_STATE = "update_state"       # the cached check
KEY_LAST = "update_last"         # the last update we asked for

NUM = r"(0|[1-9][0-9]{0,8})"
TAG_RE = re.compile(r"\Av" + NUM + r"\." + NUM + r"\." + NUM + r"\Z", re.ASCII)
BETA_TAG_RE = re.compile(r"\Av" + NUM + r"\." + NUM + r"\." + NUM + r"-beta\." + NUM + r"\Z", re.ASCII)
VERSION_RE = re.compile(r"\A" + NUM + r"\." + NUM + r"\." + NUM + r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\Z", re.ASCII)
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")


class UpdateError(ValueError):
    """Something the operator should see, in plain words (a ValueError so the API shows it as a 400)."""


# ---- versions ---------------------------------------------------------------------------------------------------------
def valid_tag(tag) -> bool:
    """A stable release tag, vX.Y.Z."""
    return isinstance(tag, str) and TAG_RE.match(tag) is not None


def valid_beta_tag(tag) -> bool:
    """A beta pre-release tag, vX.Y.Z-beta.N and nothing else."""
    return isinstance(tag, str) and BETA_TAG_RE.match(tag) is not None


def valid_release_tag(tag) -> bool:
    return valid_tag(tag) or valid_beta_tag(tag)


def parse_version(text):
    """Sortable key for X.Y.Z[-pre], or None. A release sorts above its own pre-releases, and beta.10 above beta.9."""
    m = VERSION_RE.match(text) if isinstance(text, str) else None
    if not m:
        return None
    major, minor, patch, pre = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if pre is None:
        return (major, minor, patch, 1, 0, "")
    beta = re.fullmatch(r"beta\." + NUM, pre, re.ASCII)
    return (major, minor, patch, 0, int(beta.group(1)), "") if beta else (major, minor, patch, 0, -1, pre)  # other names sort below any beta


def is_newer(tag, current=None) -> bool:
    """True if release `tag` (vX.Y.Z or vX.Y.Z-beta.N) is strictly higher than `current` (default: this program). Unparsable is not newer."""
    if not valid_release_tag(tag):
        return False
    a, b = parse_version(tag[1:]), parse_version(__version__ if current is None else current)
    return a is not None and b is not None and a > b


def plain_text(value, limit=NOTES_MAX) -> str:
    """Safe plain text: control and bidirectional-override characters removed, at most `limit` characters."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def release_url(tag: str) -> str:
    if not valid_release_tag(tag):
        raise UpdateError("That is not a release tag.")
    return RELEASE_URL_PREFIX + "tag/" + tag


def clean_release(data, channel: str = "stable"):
    """{tag, url, notes} for a published release with a valid tag, else None. The document's own html_url is ignored.

    stable: one release document, not a draft or pre-release, tagged vX.Y.Z.
    beta: the list of recent releases; the highest acceptable one, which is a stable release or a vX.Y.Z-beta.N that GitHub
    flags as a pre-release (a beta tag not flagged, or a stable tag flagged as one, is not trusted)."""
    if channel == "beta":
        best = None
        for entry in (data if isinstance(data, list) else [])[:20]:
            if not isinstance(entry, dict) or entry.get("draft") is not False:
                continue
            tag, pre = entry.get("tag_name"), entry.get("prerelease")
            if not ((valid_tag(tag) and pre is False) or (valid_beta_tag(tag) and pre is True)):
                continue
            if best is None or parse_version(tag[1:]) > parse_version(best["tag_name"][1:]):
                best = entry
        return None if best is None else {"tag": best["tag_name"], "url": release_url(best["tag_name"]), "notes": plain_text(best.get("body"))}
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        return None
    tag = data.get("tag_name")
    if not valid_tag(tag):
        return None
    return {"tag": tag, "url": release_url(tag), "notes": plain_text(data.get("body"))}


# ---- the one network call -----------------------------------------------------------------------------------------------
async def fetch_latest(etag: str | None = None, now: float | None = None,
                       transport: httpx.AsyncBaseTransport | None = None, channel: str = "stable") -> dict:
    """Ask GitHub for the latest release and describe what happened. Never raises. Returns a dict:
    outcome  ok | not_modified | no_release | rate_limited | offline | certificate | bad_response | too_large | error
    reached  True if GitHub answered at all (it decides how soon the periodic check may ask again)."""
    now = time.time() if now is None else now
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if isinstance(etag, str) and 0 < len(etag) < 200 and re.fullmatch(r"[\x21-\x7e]+", etag):
        headers["If-None-Match"] = etag
    out = {"outcome": "error", "reached": False, "message": "", "release": None, "etag": None, "retry_at": None}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False, transport=transport,
                                     trust_env=False) as client:  # trust_env=False: never pick up a ~/.netrc or proxy credentials
            cap = MAX_LIST_BYTES if channel == "beta" else MAX_RESPONSE_BYTES
            async with client.stream("GET", BETA_URL if channel == "beta" else API_URL, headers=headers) as resp:
                body, status, rh = b"", resp.status_code, resp.headers
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > cap:
                        out.update(outcome="too_large", reached=True,
                                   message="GitHub's answer was far bigger than expected, so it was ignored.")
                        return out
    except httpx.ConnectError as exc:
        text = str(exc)
        if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
            out.update(outcome="certificate", message="GitHub's certificate could not be verified, so nothing was fetched.")
        else:
            out.update(outcome="offline", message="GitHub could not be reached. Is this computer online?")
        return out
    except httpx.HTTPError as exc:
        out.update(outcome="offline", message=f"GitHub could not be reached ({exc.__class__.__name__}). Is this computer online?")
        return out
    except Exception as exc:  # the check must never take anything down
        out.update(message=f"The check failed ({exc.__class__.__name__}).")
        return out

    out["reached"] = True
    if status == 304:
        out.update(outcome="not_modified", etag=etag, message="No change since the last check.")
    elif status in (403, 429):
        wait = MAX_BLOCK_S
        retry, reset = rh.get("Retry-After"), rh.get("X-RateLimit-Reset")
        if retry and retry.isdigit():
            wait = int(retry)
        elif reset and reset.isdigit():
            wait = int(reset) - now
        wait = max(60, min(int(wait), MAX_BLOCK_S))
        out.update(outcome="rate_limited", retry_at=now + wait,
                   message=f"GitHub is limiting requests from this address; it will be asked again in about {max(1, round(wait / 60))} minutes.")
    elif status == 404:
        out.update(outcome="no_release", message="GitHub has no published release of this project (yet).")
    elif 300 <= status < 400:
        out.update(outcome="bad_response", message="GitHub answered with a redirect, which this check does not follow.")
    elif status != 200:
        out.update(outcome="error", reached=status < 500, message=f"GitHub answered with an error (HTTP {status}).")
    else:
        try:
            import json
            release = clean_release(json.loads(body.decode("utf-8")), channel)
        except (ValueError, UnicodeDecodeError):
            release = None
        if release is None:
            out.update(outcome="bad_response", message="GitHub's answer was not a release this check understands, so it was ignored.")
        else:
            tag_etag = rh.get("ETag")
            out.update(outcome="ok", release=release, message="Checked.",
                       etag=tag_etag if isinstance(tag_etag, str) and len(tag_etag) < 200 else None)
    return out


def _discard(path: Path) -> None:
    """Remove a half-made file. Error-path cleanup must never raise."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---- what kind of installation this is ------------------------------------------------------------------------------------
MANUAL_DOCKER_STEPS = ["docker compose pull", "docker compose up -d"]
GIT_STEPS = ["git pull", "./run.sh"]


def detect_install(environ=None, token_exists: Callable[[str], bool] = os.path.isfile, root: Path | None = None) -> dict:
    """{kind, can_apply, reason, steps}. Only a Docker install with the updater container can apply updates itself."""
    env = os.environ if environ is None else environ
    if env.get("LORAPEDIA_INSTALL") == "docker":
        url, token_file = env.get("LORAPEDIA_UPDATER_URL", ""), env.get("LORAPEDIA_UPDATER_TOKEN_FILE", "")
        if url and token_file and token_exists(token_file):
            return {"kind": "docker", "can_apply": True, "reason": None, "steps": []}
        return {"kind": "docker-manual", "can_apply": False, "steps": list(MANUAL_DOCKER_STEPS),
                "reason": "This runs in Docker without the updater container, so it cannot update itself. On the computer that runs Docker, in the project folder:"}
    base = root or Path(__file__).resolve().parents[2]
    if (base / ".git").exists():
        return {"kind": "git", "can_apply": False, "steps": list(GIT_STEPS),
                "reason": "This runs from a source checkout, which is updated by hand. In the project folder:"}
    return {"kind": "other", "can_apply": False, "steps": [],
            "reason": "This installation is not Docker or a git checkout, so it cannot update itself. Replace it the way you installed it."}


def manual_steps(install: dict, channel: str, latest: dict | None) -> list[str]:
    """What to type for an install that cannot update itself, adjusted for the beta channel (a beta is never `latest`)."""
    steps = list(install["steps"])
    if channel != "beta":
        return steps
    if install["kind"] == "git":
        return ["git fetch", "git switch beta", "git pull", "./run.sh"]
    if install["kind"] == "docker-manual" and latest:
        return [f"Set LORAPEDIA_IMAGE=ghcr.io/teamharriscali/lorapedia:{latest['tag'][1:]} in the .env file", "docker compose up -d"]
    return steps


def leave_beta_steps(install: dict) -> list[str]:
    """What to type to go from a beta back to stable on an install that cannot do it itself."""
    if install["kind"] == "git":
        return ["git switch main", "git pull", "./run.sh"]
    if install["kind"] == "docker-manual":
        return ["Set LORAPEDIA_IMAGE=ghcr.io/teamharriscali/lorapedia:latest in the .env file, then:", "docker compose up -d"]
    return []


# ---- the service the app owns ---------------------------------------------------------------------------------------------
class UpdateService:
    def __init__(
        self,
        *,
        store: Store,
        settings: Callable[[], Settings],
        data_dir: Path,
        busy: Callable[[], str | None] = lambda: None,
        transport: httpx.AsyncBaseTransport | None = None,
        updater_transport: httpx.AsyncBaseTransport | None = None,
        environ: dict | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self._settings, self.data_dir, self._busy = store, settings, Path(data_dir), busy
        self._transport, self._updater_transport = transport, updater_transport
        self._environ = os.environ if environ is None else environ
        self._clock = clock
        self._check_lock = asyncio.Lock()
        self._apply_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.last_activity = clock()  # bumped by the app whenever it answers a question

    # ---- small things ------------------------------------------------------------------------------------------------
    def touch(self) -> None:
        """The app answered or received a message just now."""
        self.last_activity = self._clock()

    def install(self) -> dict:
        return detect_install(self._environ)

    def _state(self) -> dict:
        data = self.store.get_json(KEY_STATE, {})
        return data if isinstance(data, dict) else {}

    def _save_state(self, **changes) -> dict:
        state = {**self._state(), **changes}
        self.store.set_json(KEY_STATE, state)
        return state

    def channel(self) -> str:
        return self._settings().update_channel

    def latest(self):
        """The release the last check found, if it was a check on the channel in use now (switching channel forgets the old answer)."""
        state = self._state()
        data = state.get("latest")
        if state.get("channel", "stable") != self.channel() or not isinstance(data, dict):
            return None
        tag = data.get("tag")
        if not (valid_tag(tag) or (self.channel() == "beta" and valid_beta_tag(tag))):
            return None
        return {"tag": tag, "url": release_url(tag), "notes": plain_text(data.get("notes")), "beta": valid_beta_tag(tag)}

    def available(self):
        latest = self.latest()
        return latest if latest and is_newer(latest["tag"]) else None

    # ---- the check ---------------------------------------------------------------------------------------------------
    def _blocked_until(self, now: float):
        until = self._state().get("blocked_until")
        return until if isinstance(until, (int, float)) and now < until < now + MAX_BLOCK_S + 60 else None

    def due(self, now: float | None = None) -> bool:
        """The periodic check is on, GitHub has not asked us to wait, and the last attempt is old enough."""
        now = self._clock() if now is None else now
        if not self._settings().update_check or self._blocked_until(now):
            return False
        state = self._state()
        if state.get("channel", "stable") != self.channel():
            return True  # the channel was just changed: ask about the new one now
        if state.get("retry_pending"):
            return True  # a rate-limit pause just ended and nothing was fetched: ask again now, not in a day
        attempt = state.get("attempt")
        if not isinstance(attempt, (int, float)) or attempt > now + 60:  # never asked, or the clock moved back
            return True
        return now - attempt >= (CHECK_EVERY_S if state.get("reached") else RETRY_UNREACHED_S)

    async def check(self, manual: bool = False) -> dict:
        """Ask GitHub now and save the answer. Never raises for a network problem."""
        now = self._clock()
        if self._check_lock.locked():
            return {"ok": False, "outcome": "busy", "message": "A check is already running."}
        async with self._check_lock:
            state = self._state()
            blocked = self._blocked_until(now)
            if blocked:
                return {"ok": False, "outcome": "rate_limited",
                        "message": f"GitHub asked for a pause; the next check can be made in about {max(1, round((blocked - now) / 60))} minutes."}
            attempt = state.get("attempt")
            if manual and isinstance(attempt, (int, float)) and 0 <= now - attempt < MIN_GAP_S:
                return {"ok": True, "outcome": "recent", "message": "Just checked."}
            channel = self.channel()
            same = state.get("channel", "stable") == channel  # an ETag belongs to one address
            res = await fetch_latest(state.get("etag") if same else None, now=now, transport=self._transport, channel=channel)
            good = res["outcome"] in ("ok", "not_modified", "no_release")
            changes = {"attempt": now, "reached": res["reached"], "error": None if good else res["message"],
                       "blocked_until": res["retry_at"] if res["outcome"] == "rate_limited" else None,
                       "retry_pending": res["outcome"] == "rate_limited"}
            if res["outcome"] == "ok":
                changes.update(latest=res["release"], etag=res["etag"], checked=now, channel=channel)
            elif res["outcome"] == "not_modified":
                changes.update(checked=now)
            elif res["outcome"] == "no_release":
                changes.update(latest=None, etag=None, checked=now, channel=channel)
            self._save_state(**changes)
            return {"ok": good, "outcome": res["outcome"], "message": res["message"]}

    # ---- the updater container ---------------------------------------------------------------------------------------
    def _updater_client(self) -> httpx.AsyncClient:
        token = Path(self._environ["LORAPEDIA_UPDATER_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
        return httpx.AsyncClient(base_url=self._environ["LORAPEDIA_UPDATER_URL"], timeout=15.0, transport=self._updater_transport,
                                 headers={"Authorization": f"Bearer {token}"}, trust_env=False)

    async def updater_status(self) -> dict | None:
        if not self.install()["can_apply"]:
            return None
        try:
            async with self._updater_client() as c:
                r = await c.get("/v1/status")
            return r.json() if r.status_code == 200 else {"state": "unreachable", "message": f"The updater answered HTTP {r.status_code}."}
        except Exception as exc:
            return {"state": "unreachable", "message": f"The updater could not be reached ({exc.__class__.__name__})."}

    def backup(self, to_version: str) -> str:
        """A verified copy of the settings/log database, taken before an update. Raises UpdateError if it cannot be made."""
        folder = self.data_dir / "backups"
        name = f"pre-update-{__version__}-to-{to_version}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self._clock()))}.db"
        dest = folder / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.store.backup_to(dest)
            check = sqlite3.connect(str(dest))
            try:
                ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                check.close()
        except Exception as exc:
            _discard(dest)
            raise UpdateError(f"The backup could not be made ({exc.__class__.__name__}), so nothing was changed.") from exc
        if not ok:
            _discard(dest)
            raise UpdateError("The backup did not pass its integrity check, so nothing was changed.")
        for old in sorted(folder.glob("pre-update-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[BACKUPS_KEPT:]:
            old.unlink(missing_ok=True)  # keep the newest few
        return name

    async def apply(self, who: str = "user") -> dict:
        """Back up, then ask the updater to install the newest release. Raises UpdateError with a plain reason."""
        install = self.install()
        if not install["can_apply"]:
            raise UpdateError(install["reason"] or "This installation cannot update itself.")
        available = self.available()
        if available is None:
            raise UpdateError("No newer release is known. Use Check now first.")
        if self._apply_lock.locked():
            raise UpdateError("An update is already being applied.")
        async with self._apply_lock:
            backup = self.backup(available["tag"][1:])
            self.store.set_json(KEY_LAST, {"from": __version__, "to": available["tag"][1:], "ts": self._clock(),
                                           "backup": backup, "result": "requested", "who": who})
            # Stable sends nothing (so an older updater works unchanged); beta sends the one word the updater accepts.
            await self._ask_updater({"channel": "beta"} if self.channel() == "beta" else None)
            log.info("update to %s requested by %s (backup %s)", available["tag"], who, backup)
            return {"requested": True, "tag": available["tag"], "backup": backup}

    async def _ask_updater(self, body: dict | None, old_updater_hint: str | None = None) -> None:
        """POST /v1/apply and turn every way it can go wrong into a plain message."""
        try:
            async with self._updater_client() as c:
                r = await c.post("/v1/apply", json=body) if body is not None else await c.post("/v1/apply")
        except Exception as exc:
            raise UpdateError(f"The updater could not be reached ({exc.__class__.__name__}). Is it running?") from exc
        if r.status_code == 401:
            raise UpdateError("The updater refused the request (bad token). Restart both containers.")
        if r.status_code == 409:
            raise UpdateError((r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else None) or "The updater is busy.")
        if r.status_code == 400 and old_updater_hint:
            raise UpdateError(old_updater_hint)
        if r.status_code != 202:
            raise UpdateError(f"The updater answered HTTP {r.status_code}.")

    def running_beta(self) -> bool:
        return valid_beta_tag("v" + __version__)

    async def leave_beta(self, who: str = "user") -> dict:
        """Return from a beta to the newest STABLE release, which is a lower version number: the one downgrade there is.

        Only a node that is running a beta may do this, and only to the newest stable release (found here, from GitHub, and then
        found again and verified by the updater itself). A backup of the settings and log is made first. Raises UpdateError."""
        install = self.install()
        if not install["can_apply"]:
            raise UpdateError(install["reason"] or "This installation cannot update itself.")
        if not self.running_beta():
            raise UpdateError("This is not a beta, so there is nothing to return from.")
        if self._apply_lock.locked():
            raise UpdateError("An update is already being applied.")
        async with self._apply_lock:
            res = await fetch_latest(None, now=self._clock(), transport=self._transport, channel="stable")
            release = res.get("release")
            if res["outcome"] != "ok" or not release or not valid_tag(release["tag"]):
                raise UpdateError(f"The newest stable release could not be found ({res['message'] or res['outcome']}), so nothing was changed.")
            tag = release["tag"]
            backup = self.backup(tag[1:])
            self.store.set_json(KEY_LAST, {"from": __version__, "to": tag[1:], "ts": self._clock(), "backup": backup,
                                           "result": "requested", "who": who, "downgrade": True})
            await self._ask_updater({"channel": "stable", "leave_beta": True},
                                    "The updater container is too old to return to stable (it only knows how to move forward). Update it "
                                    "first, in the project folder: docker compose pull updater && docker compose up -d updater")
            log.info("return from %s to %s requested by %s (backup %s)", __version__, tag, who, backup)
            return {"requested": True, "tag": tag, "backup": backup, "downgrade": not is_newer(tag)}

    # ---- automatic updates ----------------------------------------------------------------------------------------------
    def idle_reason(self) -> str | None:
        """Why an automatic update must wait, or None if the node is quiet."""
        why = self._busy()
        if why:
            return why
        if self._clock() - self.last_activity < IDLE_BEFORE_AUTO_APPLY_S:
            return "a question was answered a moment ago"
        return None

    async def maybe_auto_apply(self) -> str | None:
        """Start an update if one is available, allowed, and the node is quiet. Returns what happened (for tests and logs)."""
        s = self._settings()
        if not s.auto_update:
            return "auto_update off"
        available = self.available()
        if not available or not self.install()["can_apply"]:
            return "nothing to do"
        last = self.store.get_json(KEY_LAST, {})
        if isinstance(last, dict) and last.get("to") == available["tag"][1:] and last.get("result") == "requested" \
                and self._clock() - last.get("ts", 0) < 6 * 3600:
            return "already asked recently"  # do not hammer the updater (or loop on a version that rolled back)
        why = self.idle_reason()
        if why:
            return f"waiting: {why}"
        try:
            await self.apply(who="automatic")
        except UpdateError as exc:
            log.warning("automatic update not started: %s", exc)
            return f"not started: {exc}"
        return "applied"

    def _updater_outdated(self, updater, last) -> bool:
        """True when a beta was asked for but the updater answered as if only stable releases existed.

        An updater from before the beta channel ignores the word "beta" and, finding nothing newer than the stable release already
        installed, goes back to idle with that release as its target ("Already on the newest release"). A current updater that was
        given the same request targets the beta (or reports a failure). Derived from what the updater says, so it also works for
        updater versions that do not know how to describe themselves."""
        if isinstance(last, dict) and last.get("downgrade") and last.get("result") == "requested" and isinstance(updater, dict):
            # An updater that does not know "leave the beta" goes straight back to idle: a supporting one is busy pulling and verifying.
            return bool(updater.get("state") == "idle" and isinstance(updater.get("updated"), (int, float))
                        and updater["updated"] >= (last.get("ts") or 0) - 5)
        return bool(self.channel() == "beta" and isinstance(last, dict) and last.get("result") == "requested"
                    and valid_beta_tag("v" + str(last.get("to"))) and isinstance(updater, dict) and updater.get("state") == "idle"
                    and updater.get("target") not in (None, last.get("to"))
                    and isinstance(updater.get("updated"), (int, float)) and updater["updated"] >= (last.get("ts") or 0) - 5)

    # ---- status for the UI ----------------------------------------------------------------------------------------------
    async def status(self) -> dict:
        install = self.install()
        state = self._state()
        latest, available = self.latest(), self.available()
        s = self._settings()
        last = self.store.get_json(KEY_LAST, {})
        if isinstance(last, dict) and last.get("result") == "requested":
            if last.get("to") == __version__:
                last["result"] = "done"  # we are the new version
                self.store.set_json(KEY_LAST, last)
        updater = await self.updater_status()
        return {
            "version": __version__, "install": {"kind": install["kind"], "steps": manual_steps(install, s.update_channel, latest)},
            "check_enabled": s.update_check, "auto_update": s.auto_update, "channel": s.update_channel,
            "can_apply": bool(install["can_apply"] and available), "reason": install["reason"],
            "checked": state.get("checked") if isinstance(state.get("checked"), (int, float)) else None,
            "last_error": plain_text(state.get("error"), 300) or None,
            "latest": latest, "update_available": bool(available),
            "last_update": {k: last.get(k) for k in ("from", "to", "ts", "backup", "result", "downgrade")} if isinstance(last, dict) and last else None,
            "updater": updater, "updater_outdated": self._updater_outdated(updater, last),
            "on_beta": self.running_beta(), "can_leave_beta": bool(self.running_beta() and install["can_apply"]),
            "leave_beta_steps": leave_beta_steps(install) if self.running_beta() else [],
            "idle_reason": self.idle_reason() if available else None,
        }

    # ---- the background loop ----------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        """A minute after start-up (so it never competes with it), then every ten minutes see whether anything is due."""
        await asyncio.sleep(60)
        while True:
            try:
                if self.due():
                    await self.check()
                await self.maybe_auto_apply()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("update loop error")
            await asyncio.sleep(600)
