"""LoRaPedia updater: the only container that holds the Docker socket, so the app that processes mesh text never does.

READ THIS FIRST, it is the whole security story:

* It can do exactly one thing: replace the `lorapedia` service with the NEWEST PUBLISHED RELEASE of REPO (a constant: no request,
  flag or environment variable changes it). The caller cannot choose a version, a registry or an image: `POST /v1/apply` takes no
  parameters. A compromised app can therefore at worst trigger an update to a release this repository's own workflow signed.
* The version comes from GitHub's `releases/latest` of REPO, parsed strictly (a stable `vX.Y.Z` tag, not a draft or pre-release), and
  must be strictly newer than what is running: it can never downgrade.
* Before anything runs, the image is pulled and its signature is verified with cosign against the EXACT identity
  `https://github.com/<REPO>/.github/workflows/release.yml@refs/tags/<tag>` issued by GitHub's OIDC provider: only the release
  workflow, run for that exact tag, produces an image that passes. What was verified is what runs: the new image is referenced by
  its digest, never by a tag that could move.
* The new container must become healthy AND report the new version within a deadline, or the old image is put back and the
  failed version is remembered so it is not retried.
* Every command is an argument list (never a shell); the token that protects the API lives in a volume shared only with the app.

Standard library only. The module has no side effects on import; tests inject the command runner and the HTTP getter.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = "TeamHarrisCali/LoRaPedia"           # the one repository this updates from
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
IMAGE_REPO = "ghcr.io/teamharriscali/lorapedia"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
SERVICE = "lorapedia"                        # the compose service this replaces
ROLLBACK_TAG = "lorapedia-rollback:previous"
IMAGE_VAR = "LORAPEDIA_IMAGE"                # the variable docker-compose.yml reads the image from
VERSION_LABEL = "org.opencontainers.image.version"

NUM = r"(0|[1-9][0-9]{0,8})"
TAG_RE = re.compile(r"\Av" + NUM + r"\." + NUM + r"\." + NUM + r"\Z", re.ASCII)
DIGEST_RE = re.compile(r"\A[a-z0-9./_-]+@sha256:[0-9a-f]{64}\Z", re.ASCII)
MAX_RESPONSE = 256 * 1024


def valid_tag(tag) -> bool:
    return isinstance(tag, str) and TAG_RE.match(tag) is not None


def parse(version: str):
    """(major, minor, patch) for a strict X.Y.Z, else None. Pre-release and dev versions are deliberately not updatable."""
    m = re.fullmatch(NUM + r"\." + NUM + r"\." + NUM, version or "", re.ASCII)
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag: str, current: str) -> bool:
    return valid_tag(tag) and parse(tag[1:]) is not None and parse(current) is not None and parse(tag[1:]) > parse(current)


def signing_identity(tag: str) -> str:
    return f"https://github.com/{REPO}/.github/workflows/release.yml@refs/tags/{tag}"


class UpdateError(Exception):
    """A refusal or failure with a plain-language message."""


@dataclass
class Config:
    project_dir: str = "/project"
    state_dir: str = "/state"
    health_timeout_s: float = 180.0
    poll_s: float = 3.0
    command_timeout_s: float = 900.0

    @property
    def env_file(self):
        return os.path.join(self.project_dir, ".env")

    @property
    def compose_file(self):
        return os.path.join(self.project_dir, "docker-compose.yml")


@dataclass
class State:
    state: str = "idle"  # idle | checking | pulling | verifying | applying | waiting | done | rolled_back | failed
    message: str = ""
    current: str | None = None
    target: str | None = None
    updated: float = 0.0
    failed_versions: list = field(default_factory=list)


# ---- the seams: how commands run and how GitHub is asked ---------------------------------------------------------
def run_command(argv, timeout=900.0):
    """(returncode, stdout, stderr). argv is a list of strings, never a shell line; no stdin; a time limit applies."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings")
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]} took longer than {int(timeout)} seconds and was stopped"
    except OSError as exc:
        return 127, "", f"{argv[0]} could not be run ({exc})"
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def http_get(url: str, timeout=15.0) -> tuple[int, bytes]:
    """GET with certificate verification, no redirects, a size cap and no credentials."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "lorapedia-updater", "Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_RESPONSE + 1)[:MAX_RESPONSE]
    except urllib.error.HTTPError as exc:
        return exc.code, b""


class Updater:
    def __init__(self, cfg: Config | None = None, run=run_command, get=http_get, clock=time.time, sleep=time.sleep):
        self.cfg, self._run, self._get, self._clock, self._sleep = cfg or Config(), run, get, clock, sleep
        self._lock = threading.Lock()        # guards the saved state
        self._apply_lock = threading.Lock()  # one update at a time
        self._state = self._load()

    # ---- state ---------------------------------------------------------------------------------------------------
    @property
    def _state_path(self):
        return os.path.join(self.cfg.state_dir, "status.json")

    def _load(self) -> State:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            st = State(**{k: d[k] for k in State.__dataclass_fields__ if k in d})
            if st.state not in ("idle", "done", "rolled_back", "failed"):
                st.state, st.message = "failed", "The updater was restarted in the middle of an update."
            return st
        except (OSError, ValueError, TypeError):
            return State()

    def _set(self, **fields):
        with self._lock:
            for k, v in fields.items():
                setattr(self._state, k, v)
            self._state.updated = self._clock()
            try:
                os.makedirs(self.cfg.state_dir, exist_ok=True)
                tmp = self._state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._state.__dict__, f)
                os.replace(tmp, self._state_path)
            except OSError:
                pass  # status is best-effort; the in-memory copy still answers

    def status(self) -> dict:
        with self._lock:
            return dict(self._state.__dict__)

    def busy(self) -> bool:
        return self.status()["state"] in ("checking", "pulling", "verifying", "applying", "waiting")

    # ---- docker helpers -------------------------------------------------------------------------------------------
    def _docker(self, *args, timeout=None):
        return self._run(["docker", *args], timeout or self.cfg.command_timeout_s)

    def _ok(self, *args, what):
        rc, out, err = self._docker(*args)
        if rc != 0:
            raise UpdateError(f"{what} failed: {(err or out or 'no reason given')[-300:]}")
        return out

    def project(self) -> str:
        """The compose project this updater runs in, read from its own container's labels (hostname = container id)."""
        name = self._ok("inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', socket.gethostname(),
                        what="Reading the compose project")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name or ""):
            raise UpdateError("This updater is not running inside a Docker Compose project.")
        return name

    def app_container(self, project: str) -> str:
        out = self._ok("ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                       "--filter", f"label=com.docker.compose.service={SERVICE}", "--format", "{{.ID}}",
                       what="Finding the LoRaPedia container")
        ids = [i for i in out.split() if re.fullmatch(r"[0-9a-f]{6,64}", i)]
        if not ids:
            raise UpdateError("The LoRaPedia container was not found.")
        return ids[0]

    def running_version(self, container: str) -> str:
        return self._ok("inspect", "--format", '{{index .Config.Labels "' + VERSION_LABEL + '"}}', container,
                        what="Reading the running version")

    # ---- GitHub ---------------------------------------------------------------------------------------------------
    def latest_tag(self) -> str:
        try:
            status, body = self._get(API_URL)
        except Exception as exc:
            raise UpdateError(f"GitHub could not be reached ({type(exc).__name__}).") from exc
        if status != 200:
            raise UpdateError(f"GitHub answered HTTP {status}.")
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise UpdateError("GitHub's answer was not understood.") from exc
        if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False \
                or not valid_tag(data.get("tag_name")):
            raise UpdateError("GitHub's latest release is not a stable release this updater accepts.")
        return data["tag_name"]

    # ---- the update -----------------------------------------------------------------------------------------------
    def start_apply(self) -> dict:
        """Begin an update on a background thread and return at once. Raises UpdateError if one is already running."""
        if not self._apply_lock.acquire(blocking=False):
            raise UpdateError("An update is already running.")
        self._set(state="checking", message="Checking for the newest release", target=None)
        threading.Thread(target=self._apply_thread, daemon=True, name="apply").start()
        return self.status()

    def _apply_thread(self):
        try:
            self.apply()
        except UpdateError as exc:
            self._set(state="failed", message=str(exc))
        except Exception as exc:  # a bug must not leave the app waiting for ever
            self._set(state="failed", message=f"The update stopped unexpectedly ({type(exc).__name__}). Nothing was changed.")
        finally:
            self._apply_lock.release()

    def apply(self):
        project = self.project()
        container = self.app_container(project)
        current = self.running_version(container)
        self._set(current=current)
        if parse(current) is None:
            raise UpdateError(f"The running image is version {current!r}, not a release, so it is not updated automatically.")
        tag = self.latest_tag()
        self._set(target=tag[1:])
        if not is_newer(tag, current):
            self._set(state="idle", message=f"Already on the newest release ({current}).")
            return
        if tag[1:] in self.status()["failed_versions"]:
            raise UpdateError(f"{tag} was tried before and failed, so it is not tried again. A newer release will be.")
        version = tag[1:]
        image = f"{IMAGE_REPO}:{version}"

        self._set(state="pulling", message=f"Downloading {tag}")
        self._ok("pull", "--quiet", image, what=f"Downloading {tag}")
        ref = self._digest_ref(image)

        self._set(state="verifying", message="Verifying the signature")
        try:
            self._verify(tag, ref)
            label = self._ok("image", "inspect", "--format", '{{index .Config.Labels "' + VERSION_LABEL + '"}}', ref,
                             what="Reading the new image's version")
            if label != version:
                raise UpdateError(f"The image reports version {label!r}, not {version}, so it is refused.")
        except UpdateError:
            self._docker("image", "rm", ref)  # never leave an unverified image around
            self._remember_failure(version)
            raise

        self._set(state="applying", message=f"Switching to {tag}")
        previous_image_id = self._ok("inspect", "--format", "{{.Image}}", container, what="Reading the running image")
        self._ok("tag", previous_image_id, ROLLBACK_TAG, what="Keeping the current version for roll-back")
        self._write_env({IMAGE_VAR: ref})
        problem = None
        try:
            self._compose_up(project)
            self._set(state="waiting", message="Waiting for the new version to be healthy")
            problem = self._wait_healthy(project, version)
        except UpdateError as exc:
            problem = str(exc)
        if problem is None:
            self._set(state="done", message=f"Updated {current} -> {version}.", current=version)
            return

        # Roll back: put the previous image back and say why.
        self._set(state="applying", message="The new version did not come up; rolling back")
        self._remember_failure(version)
        self._write_env({IMAGE_VAR: ROLLBACK_TAG})
        try:
            self._compose_up(project)
        except UpdateError as exc:
            raise UpdateError(f"{problem} Rolling back also failed ({exc}). Run `docker compose up -d` by hand.") from exc
        self._set(state="rolled_back", message=f"{tag} did not start correctly ({problem}). Rolled back to {current}.")

    def _remember_failure(self, version: str):
        failed = list(self.status()["failed_versions"])
        if version not in failed:
            failed.append(version)
        self._set(failed_versions=failed[-10:])

    def _digest_ref(self, image: str) -> str:
        out = self._ok("image", "inspect", "--format", "{{range .RepoDigests}}{{println .}}{{end}}", image,
                       what="Reading the image digest")
        for line in out.splitlines():
            if line.startswith(IMAGE_REPO + "@") and DIGEST_RE.match(line):
                return line
        raise UpdateError("The downloaded image has no digest from the expected registry.")

    def _verify(self, tag: str, ref: str):
        rc, out, err = self._run(["cosign", "verify", "--certificate-identity", signing_identity(tag),
                                  "--certificate-oidc-issuer", OIDC_ISSUER, ref], self.cfg.command_timeout_s)
        if rc != 0:
            raise UpdateError(f"The signature on {tag} could not be verified, so it was not installed ({(err or out)[-200:]}).")

    # ---- compose + .env --------------------------------------------------------------------------------------------
    def _compose_up(self, project: str):
        self._ok("compose", "-p", project, "--project-directory", self.cfg.project_dir, "-f", self.cfg.compose_file,
                 "up", "-d", "--no-deps", "--no-build", "--pull", "never", SERVICE, what="Starting the new version")

    def _wait_healthy(self, project: str, version: str) -> str | None:
        """None when the new container is healthy and reports `version`; else a one-line reason."""
        deadline = self._clock() + self.cfg.health_timeout_s
        reason = "it never became healthy"
        while self._clock() < deadline:
            try:
                container = self.app_container(project)
                health = self._ok("inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{end}}", container,
                                  what="Checking health")
                running = self.running_version(container)
            except UpdateError as exc:
                reason = str(exc)
            else:
                if health == "healthy" and running == version:
                    return None
                reason = f"health={health or 'unknown'}, version={running or 'unknown'}"
                if health == "unhealthy":
                    return reason
            self._sleep(self.cfg.poll_s)
        return reason

    def _write_env(self, changes: dict):
        """Update only the variables named; keep every other line (comments included) exactly as the user wrote it.

        The updater is root inside its container but runs with every capability dropped except the two it needs here
        (DAC_OVERRIDE to write in a folder owned by the user, CHOWN to hand the file back to them). The new file takes the
        owner and mode of the old one (or of the project folder), so the user can still edit it afterwards."""
        try:
            with open(self.cfg.env_file, encoding="utf-8") as f:
                lines = f.read().splitlines()
            ref = os.stat(self.cfg.env_file)
        except OSError:
            lines, ref = [], None
        for key, value in changes.items():
            if not re.fullmatch(r"[A-Z_]+", key) or "\n" in value:
                raise UpdateError("Refusing an unsafe .env change.")
            for i, ln in enumerate(lines):
                if ln.startswith(key + "="):
                    lines[i] = f"{key}={value}"
                    break
            else:
                lines.append(f"{key}={value}")
        tmp = self.cfg.env_file + ".tmp"
        try:
            owner = ref or os.stat(self.cfg.project_dir)
            try:
                os.unlink(tmp)  # a leftover from an interrupted run is the user's now: never reopen it, replace it
            except FileNotFoundError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # exclusive: it is certainly ours
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            os.chmod(tmp, (ref.st_mode & 0o777) if ref else 0o644)  # while it is still ours: afterwards we could not
            try:
                os.chown(tmp, owner.st_uid, owner.st_gid)  # hand it to the user last
            except PermissionError:
                pass  # without CAP_CHOWN the file stays ours; still usable, just not owned by the user
            os.replace(tmp, self.cfg.env_file)
        except OSError as exc:
            try:
                os.unlink(tmp)  # never leave a half-made file in the user's folder
            except OSError:
                pass
            raise UpdateError(f"The .env file could not be written ({exc}).") from exc


# ---- HTTP API ----------------------------------------------------------------------------------------------------------
def load_or_create_token(state_dir: str) -> str:
    """The shared secret between the app and this updater, kept in a volume only those two containers mount."""
    path = os.path.join(state_dir, "token")
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    os.makedirs(state_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)  # the app (another uid) must read it
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def make_handler(updater: Updater, token: str):
    expected = hashlib.sha256(token.encode()).digest()

    class Handler(BaseHTTPRequestHandler):
        server_version = "lorapedia-updater"

        def log_message(self, *a):  # keep the container log quiet
            pass

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            given = header[7:] if header.startswith("Bearer ") else ""
            return hmac.compare_digest(hashlib.sha256(given.encode()).digest(), expected)

        def _send(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            if self.path == "/v1/status":
                return self._send(200, updater.status())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            if self.path != "/v1/apply":
                return self._send(404, {"error": "not found"})
            try:
                self._send(202, updater.start_apply())
            except UpdateError as exc:
                self._send(409, {"error": str(exc)})

    return Handler


def main():
    cfg = Config()
    updater = Updater(cfg)
    token = load_or_create_token(cfg.state_dir)
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("UPDATER_PORT", "8081"))), make_handler(updater, token))
    print("lorapedia-updater ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
