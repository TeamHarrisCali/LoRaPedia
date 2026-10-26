"""The updater sidecar (updater/updater.py): every path through apply(), with a fake Docker and a fake GitHub."""

import http.client
import importlib.util
import json
import os
import stat
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("lp_updater", Path(__file__).resolve().parents[2] / "updater" / "updater.py")
U = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U)

DIGEST = "a" * 64
REF = f"{U.IMAGE_REPO}@sha256:{DIGEST}"


class FakeDocker:
    """Answers the exact docker/cosign commands the updater issues, and models which image the container is running."""

    def __init__(self, cfg, current="0.1.0", label=None, verify_ok=True, healthy=True, rollback_works=True, project="lorapedia"):
        self.cfg, self.current, self.label, self.verify_ok = cfg, current, label, verify_ok
        self.healthy, self.rollback_works, self.project = healthy, rollback_works, project
        self.calls, self.target, self.up_calls = [], None, 0
        self.running = current

    def _env_image(self):
        for ln in Path(self.cfg.env_file).read_text().splitlines():
            if ln.startswith("LORAPEDIA_IMAGE="):
                return ln.split("=", 1)[1]
        return ""

    def __call__(self, argv, timeout=0):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)  # never a shell line
        self.calls.append(argv)
        a = argv
        if a[0] == "cosign":
            return (0, "ok", "") if self.verify_ok else (1, "", "no matching signatures")
        assert a[0] == "docker"
        if a[1] == "inspect":
            fmt, target = a[3], a[4]
            if "com.docker.compose.project" in fmt:
                return 0, self.project, ""
            assert target == "abc123"
            if "org.opencontainers.image.version" in fmt:
                return 0, self.running, ""
            if fmt == "{{.Image}}":
                return 0, "sha256:" + "b" * 64, ""
            if "Health" in fmt:
                return 0, "healthy" if self.healthy or self.running == self.current else "unhealthy", ""
        if a[1] == "ps":
            return 0, "abc123", ""
        if a[1] == "pull":
            return 0, "", ""
        if a[1:3] == ["image", "inspect"]:
            if "RepoDigests" in a[4]:
                return 0, REF, ""
            return 0, self.label or self.target or "", ""
        if a[1] == "tag" or a[1:3] == ["image", "rm"]:
            return 0, "", ""
        if a[1] == "compose":
            self.up_calls += 1
            if "sha256" in self._env_image():
                self.running = self.target
            else:
                if not self.rollback_works:
                    return 1, "", "daemon is on fire"
                self.running = self.current
            return 0, "", ""
        raise AssertionError(f"unexpected command {argv}")


def github(tag="v0.2.0", status=200, draft=False, pre=False, boom=False):
    def get(url, timeout=15.0):
        assert url == f"https://api.github.com/repos/TeamHarrisCali/LoRaPedia/releases/latest"
        if boom:
            raise OSError("offline")
        return status, json.dumps({"tag_name": tag, "draft": draft, "prerelease": pre}).encode()
    return get


def make(tmp_path, tag="v0.2.0", **kw):
    cfg = U.Config(project_dir=str(tmp_path / "proj"), state_dir=str(tmp_path / "state"), health_timeout_s=30, poll_s=1)
    os.makedirs(cfg.project_dir)
    Path(cfg.env_file).write_text("# my settings\nMY_OTHER=keep me\nLORAPEDIA_IMAGE=ghcr.io/teamharriscali/lorapedia:latest\n")
    clock = [0.0]

    def sleep(s):
        clock[0] += s

    gh_kw = {k: kw.pop(k) for k in ("status", "draft", "pre", "boom") if k in kw}
    fake = FakeDocker(cfg, **kw)
    fake.target = tag[1:]
    up = U.Updater(cfg, run=fake, get=github(tag, **gh_kw), clock=lambda: clock[0], sleep=sleep)
    return up, fake, cfg


# ---- versions and identity ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag,ok", [("v1.2.3", True), ("v0.1.0", True), ("1.2.3", False), ("v1.2", False), ("v01.2.3", False),
                                    ("v1.2.3-rc.1", False), ("v1.2.3\n", False), ("v1.2.3 ", False), ("v١.2.3", False),
                                    ("", False), (None, False), ("../v1.2.3", False)])
def test_only_strict_stable_tags_are_accepted(tag, ok):
    assert U.valid_tag(tag) is ok


def test_is_newer_never_downgrades_and_ignores_non_releases():
    assert U.is_newer("v0.2.0", "0.1.0") and U.is_newer("v1.0.0", "0.9.9") and U.is_newer("v0.10.0", "0.9.0")
    assert not U.is_newer("v0.1.0", "0.1.0") and not U.is_newer("v0.0.9", "0.1.0")
    assert not U.is_newer("v0.2.0", "dev") and not U.is_newer("v0.2.0", "") and not U.is_newer("v0.2.0", "0.2.0-rc.1")


def test_the_signing_identity_is_exact_to_the_release_workflow_and_tag():
    assert U.signing_identity("v0.2.0") == "https://github.com/TeamHarrisCali/LoRaPedia/.github/workflows/release.yml@refs/tags/v0.2.0"


# ---- GitHub -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kw,fragment", [({"draft": True}, "stable"), ({"pre": True}, "stable"), ({"tag": "latest"}, "stable"),
                                         ({"status": 404}, "HTTP 404"), ({"status": 403}, "HTTP 403"), ({"boom": True}, "reached")])
def test_latest_tag_refuses_anything_but_a_stable_release(tmp_path, kw, fragment):
    up, _, _ = make(tmp_path, **kw)
    with pytest.raises(U.UpdateError, match=fragment):
        up.latest_tag()


# ---- the happy path -----------------------------------------------------------------------------------------------


def test_a_verified_newer_release_is_applied_in_the_right_order(tmp_path):
    up, fake, cfg = make(tmp_path)
    up.apply()
    s = up.status()
    assert (s["state"], s["current"], s["target"]) == ("done", "0.2.0", "0.2.0")
    kinds = [c[0] if c[0] == "cosign" else c[1] for c in fake.calls]
    assert kinds.index("pull") < kinds.index("cosign") < kinds.index("tag") < kinds.index("compose")  # verify, then switch
    pull = next(c for c in fake.calls if c[:2] == ["docker", "pull"])
    assert pull[-1] == f"{U.IMAGE_REPO}:0.2.0"
    verify = next(c for c in fake.calls if c[0] == "cosign")
    assert "--certificate-identity" in verify and verify[verify.index("--certificate-identity") + 1] == U.signing_identity("v0.2.0")
    assert verify[verify.index("--certificate-oidc-issuer") + 1] == U.OIDC_ISSUER and verify[-1] == REF  # the digest, not a tag
    env = Path(cfg.env_file).read_text().splitlines()
    assert f"LORAPEDIA_IMAGE={REF}" in env  # what was verified is what runs
    assert "# my settings" in env and "MY_OTHER=keep me" in env  # the user's other lines are untouched
    assert fake.up_calls == 1
    up_cmd = next(c for c in fake.calls if c[1] == "compose")
    assert "--no-deps" in up_cmd and "--no-build" in up_cmd and up_cmd[-1] == "lorapedia" and up_cmd[up_cmd.index("-p") + 1] == "lorapedia"
    assert any(c[:2] == ["docker", "tag"] and c[-1] == U.ROLLBACK_TAG for c in fake.calls)  # the old image is kept


def test_the_signature_is_checked_before_anything_is_switched(tmp_path):
    up, fake, _ = make(tmp_path)
    up.apply()
    kinds = [c[0] if c[0] == "cosign" else c[1] for c in fake.calls]
    assert kinds.index("cosign") < kinds.index("compose")


# ---- refusals -----------------------------------------------------------------------------------------------------


def test_already_current_does_nothing(tmp_path):
    up, fake, _ = make(tmp_path, tag="v0.1.0")
    up.apply()
    assert up.status()["state"] == "idle" and "newest" in up.status()["message"]
    assert fake.up_calls == 0 and not any(c[:2] == ["docker", "pull"] for c in fake.calls)


def test_it_never_downgrades(tmp_path):
    up, fake, _ = make(tmp_path, tag="v0.0.9")
    up.apply()
    assert up.status()["state"] == "idle" and fake.up_calls == 0


def test_a_dev_build_is_not_auto_updated(tmp_path):
    up, fake, _ = make(tmp_path, current="dev")
    with pytest.raises(U.UpdateError, match="not a release"):
        up.apply()
    assert fake.up_calls == 0


def test_a_bad_signature_installs_nothing_removes_the_image_and_is_not_retried(tmp_path):
    up, fake, cfg = make(tmp_path, verify_ok=False)
    before = Path(cfg.env_file).read_text()
    with pytest.raises(U.UpdateError, match="could not be verified"):
        up.apply()
    assert fake.up_calls == 0 and Path(cfg.env_file).read_text() == before  # nothing changed
    assert any(c[:3] == ["docker", "image", "rm"] and c[-1] == REF for c in fake.calls)  # unverified image removed
    assert "0.2.0" in up.status()["failed_versions"]
    calls_before = len(fake.calls)
    with pytest.raises(U.UpdateError, match="tried before"):
        up.apply()
    retry = fake.calls[calls_before:]
    assert not any(c[:2] == ["docker", "pull"] or c[0] == "cosign" for c in retry)  # it stopped before downloading again


def test_an_image_whose_label_disagrees_with_the_tag_is_refused(tmp_path):
    up, fake, _ = make(tmp_path, label="0.9.9")
    with pytest.raises(U.UpdateError, match="reports version"):
        up.apply()
    assert fake.up_calls == 0


# ---- roll back ----------------------------------------------------------------------------------------------------


def test_an_unhealthy_new_version_is_rolled_back_and_remembered(tmp_path):
    up, fake, cfg = make(tmp_path, healthy=False)
    up.apply()
    s = up.status()
    assert s["state"] == "rolled_back" and "Rolled back to 0.1.0" in s["message"]
    assert fake.up_calls == 2  # new, then the old again
    assert f"LORAPEDIA_IMAGE={U.ROLLBACK_TAG}" in Path(cfg.env_file).read_text().splitlines()
    assert fake.running == "0.1.0" and "0.2.0" in s["failed_versions"]


def test_a_version_that_never_becomes_healthy_times_out_into_a_rollback(tmp_path):
    class Slow(FakeDocker):
        def __call__(self, argv, timeout=0):
            if argv[:2] == ["docker", "inspect"] and "Health" in argv[3] and self.running != self.current:
                return 0, "starting", ""
            return super().__call__(argv, timeout)

    up, fake, cfg = make(tmp_path)
    slow = Slow(cfg)
    slow.target = "0.2.0"
    up._run = slow
    up.apply()
    assert up.status()["state"] == "rolled_back" and slow.up_calls == 2


def test_if_rolling_back_fails_too_the_message_says_so(tmp_path):
    up, fake, _ = make(tmp_path, healthy=False, rollback_works=False)
    with pytest.raises(U.UpdateError, match="Rolling back also failed"):
        up.apply()


def test_the_env_writer_changes_only_what_it_is_told_and_refuses_unsafe_input(tmp_path):
    up, _, cfg = make(tmp_path)
    up._write_env({"LORAPEDIA_IMAGE": "x", "NEW_KEY": "1"})
    assert Path(cfg.env_file).read_text().splitlines() == ["# my settings", "MY_OTHER=keep me", "LORAPEDIA_IMAGE=x", "NEW_KEY=1"]
    for bad in ({"lower": "x"}, {"KEY": "a\nINJECT=1"}, {"A B": "x"}):
        with pytest.raises(U.UpdateError):
            up._write_env(bad)


def test_the_env_file_keeps_its_owner_and_mode_and_a_new_one_gets_the_folders(tmp_path):
    up, _, cfg = make(tmp_path)
    os.chmod(cfg.env_file, 0o600)
    before = os.stat(cfg.env_file)
    up._write_env({"LORAPEDIA_IMAGE": "x"})
    after = os.stat(cfg.env_file)
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (before.st_uid, before.st_gid, 0o600)
    assert not os.path.exists(cfg.env_file + ".tmp")  # no stray temp file left in the user's folder
    os.remove(cfg.env_file)
    up._write_env({"LORAPEDIA_IMAGE": "y"})  # created fresh: takes the project folder's owner, normal mode
    fresh = os.stat(cfg.env_file)
    assert fresh.st_uid == os.stat(cfg.project_dir).st_uid and stat.S_IMODE(fresh.st_mode) == 0o644
    assert Path(cfg.env_file).read_text() == "LORAPEDIA_IMAGE=y\n"


def test_a_leftover_temp_file_from_an_interrupted_run_cannot_block_updates(tmp_path):
    up, _, cfg = make(tmp_path)
    Path(cfg.env_file + ".tmp").write_text("stale half-written junk")
    up._write_env({"LORAPEDIA_IMAGE": "ok"})
    assert "LORAPEDIA_IMAGE=ok" in Path(cfg.env_file).read_text() and "junk" not in Path(cfg.env_file).read_text()
    assert not os.path.exists(cfg.env_file + ".tmp")


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    up, _, cfg = make(tmp_path)
    before = Path(cfg.env_file).read_text()
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(U.UpdateError, match="disk full"):
        up._write_env({"LORAPEDIA_IMAGE": "x"})
    assert not os.path.exists(cfg.env_file + ".tmp") and Path(cfg.env_file).read_text() == before  # original untouched


def test_a_missing_chown_capability_is_tolerated_but_a_write_failure_is_reported(tmp_path, monkeypatch):
    up, _, cfg = make(tmp_path)
    monkeypatch.setattr(os, "chown", lambda *a: (_ for _ in ()).throw(PermissionError()))
    up._write_env({"LORAPEDIA_IMAGE": "z"})  # still written
    assert "LORAPEDIA_IMAGE=z" in Path(cfg.env_file).read_text()
    monkeypatch.undo()
    os.chmod(cfg.project_dir, 0o500)
    try:
        if os.geteuid() != 0:  # a read-only folder: root would ignore it
            with pytest.raises(U.UpdateError, match="could not be written"):
                up._write_env({"LORAPEDIA_IMAGE": "w"})
    finally:
        os.chmod(cfg.project_dir, 0o700)


def test_run_command_refuses_a_shell_string_and_survives_a_missing_program():
    with pytest.raises(TypeError):
        U.run_command("docker ps; rm -rf /")
    assert U.run_command(["definitely-not-a-program-xyz"])[0] == 127


def test_state_survives_a_restart_and_an_interrupted_update_reads_as_failed(tmp_path):
    up, _, cfg = make(tmp_path)
    up._set(state="applying", message="Switching", failed_versions=["0.1.5"])
    again = U.Updater(cfg)
    s = again.status()
    assert s["state"] == "failed" and "restarted" in s["message"] and s["failed_versions"] == ["0.1.5"]


# ---- the HTTP API -------------------------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    up, fake, cfg = make(tmp_path)
    token = U.load_or_create_token(cfg.state_dir)
    server = ThreadingHTTPServer(("127.0.0.1", 0), U.make_handler(up, token))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, path, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request(method, path, headers={"Authorization": f"Bearer {token}"} if auth else {})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    yield call, up, fake, cfg, token
    server.shutdown()


def test_api_requires_the_token_except_the_health_probe(api):
    call, *_ = api
    assert call("GET", "/healthz", auth=False)[0] == 200
    for method, path in (("GET", "/v1/status"), ("POST", "/v1/apply")):
        assert call(method, path, auth=False)[0] == 401
    assert call("GET", "/v1/status")[0] == 200
    assert call("GET", "/nope")[0] == 404 and call("POST", "/v1/other")[0] == 404


def test_api_apply_takes_no_parameters_and_runs_the_update(api):
    call, up, fake, cfg, _ = api
    code, body = call("POST", "/v1/apply")
    assert code == 202
    for _ in range(100):
        if up.status()["state"] in ("done", "failed", "rolled_back"):
            break
        threading.Event().wait(0.02)
    assert up.status()["state"] == "done" and fake.up_calls == 1


def test_api_refuses_a_second_update_while_one_runs(api):
    call, up, fake, cfg, _ = api
    gate = threading.Event()
    real = fake.__call__

    def blocking(argv, timeout=0):
        if argv[:2] == ["docker", "pull"]:
            gate.wait(5)
        return real(argv, timeout)

    up._run = blocking
    assert call("POST", "/v1/apply")[0] == 202
    code, body = call("POST", "/v1/apply")
    assert code == 409 and "already running" in body["error"]
    gate.set()


def test_token_is_created_once_long_random_and_readable_by_the_app(tmp_path):
    state = str(tmp_path / "s")
    t1 = U.load_or_create_token(state)
    assert len(t1) >= 32 and U.load_or_create_token(state) == t1  # stable across restarts
    mode = stat.S_IMODE(os.stat(os.path.join(state, "token")).st_mode)
    assert mode & 0o004  # world-readable inside the private volume so the app's uid can read it
    assert not mode & 0o022  # but never writable by anyone else
