"""App-side update checking and the request to the updater container. GitHub and the updater are faked (httpx.MockTransport)."""

import json
import os
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lorapedia import __version__, updates as U
from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.store import Store


def release(tag="v9.9.9", body="Notes.", draft=False, pre=False, **extra):
    return {"tag_name": tag, "draft": draft, "prerelease": pre, "body": body, **extra}


def gh(doc=None, status=200, etag='"abc"', headers=None, raw=None, seen=None):
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == U.API_URL  # the one constant address
        if seen is not None:
            seen.append(req)
        h = {"ETag": etag, **(headers or {})}
        if status == 304:
            return httpx.Response(304, headers=h)
        return httpx.Response(status, content=raw if raw is not None else json.dumps(doc if doc is not None else release()).encode(), headers=h)
    return httpx.MockTransport(handler)


# ---- versions and strictness ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag,ok", [("v1.2.3", True), ("1.2.3", False), ("v1.2", False), ("v01.2.3", False), ("v1.2.3-rc.1", False),
                                    ("v1.2.3\n", False), ("v١.2.3", False), ("", False), (None, False), (5, False)])
def test_only_strict_stable_tags(tag, ok):
    assert U.valid_tag(tag) is ok


def test_ordering_and_prereleases():
    assert U.is_newer("v0.2.0", "0.1.0") and U.is_newer("v0.10.0", "0.9.0") and U.is_newer("v1.0.0", "1.0.0-rc.1")
    assert not U.is_newer("v0.1.0", "0.1.0") and not U.is_newer("v0.0.9", "0.1.0") and not U.is_newer("garbage", "0.1.0")
    assert not U.is_newer("v0.2.0", "not-a-version")


def test_release_notes_are_plain_text_and_the_link_is_built_from_the_tag_only():
    r = U.clean_release(release("v1.2.3", body="Hi‮ evil\x00 <script>alert(1)</script>", html_url="https://evil.example/x"))
    assert r["url"] == "https://github.com/TeamHarrisCali/LoRaPedia/releases/tag/v1.2.3"  # html_url ignored
    assert "‮" not in r["notes"] and "\x00" not in r["notes"] and "<script>" in r["notes"]  # kept as inert text, shown with textContent
    assert len(U.clean_release(release(body="x" * 5000))["notes"]) <= U.NOTES_MAX + 1


@pytest.mark.parametrize("doc", [release(draft=True), release(pre=True), release(tag="latest"), release(tag="v1.2"), [], "x", None,
                                 {"tag_name": "v1.2.3"}])  # missing draft/prerelease flags are not trusted either
def test_unacceptable_release_documents_are_ignored(doc):
    assert U.clean_release(doc) is None


# ---- the network call -------------------------------------------------------------------------------------------------


async def test_a_good_answer():
    res = await U.fetch_latest(transport=gh(release("v0.2.0", "Fixed things.")))
    assert (res["outcome"], res["reached"], res["etag"]) == ("ok", True, '"abc"')
    assert res["release"]["tag"] == "v0.2.0" and res["release"]["notes"] == "Fixed things."


async def test_only_the_get_is_sent_with_a_user_agent_and_the_etag():
    seen = []
    await U.fetch_latest(etag='"zzz"', transport=gh(seen=seen))
    (req,) = seen
    assert req.method == "GET" and req.headers["User-Agent"] == f"lorapedia/{__version__}" and req.headers["If-None-Match"] == '"zzz"'
    assert not {"authorization", "cookie"} & set(req.headers.keys())  # no credentials
    await U.fetch_latest(etag="bad\nvalue", transport=gh(seen=seen))
    assert "If-None-Match" not in seen[-1].headers  # a malformed validator is dropped, not sent


@pytest.mark.parametrize("kw,outcome,reached", [
    ({"status": 304}, "not_modified", True), ({"status": 404}, "no_release", True), ({"status": 500}, "error", False),
    ({"status": 302}, "bad_response", True), ({"raw": b"not json"}, "bad_response", True),
    ({"doc": release(draft=True)}, "bad_response", True), ({"raw": b"x" * (U.MAX_RESPONSE_BYTES + 1)}, "too_large", True)])
async def test_outcomes(kw, outcome, reached):
    res = await U.fetch_latest(transport=gh(**kw))
    assert (res["outcome"], res["reached"]) == (outcome, reached)


async def test_rate_limit_is_respected_and_capped():
    now = 1000.0
    res = await U.fetch_latest(now=now, transport=gh(status=403, headers={"Retry-After": "120"}))
    assert res["outcome"] == "rate_limited" and res["retry_at"] == now + 120
    res = await U.fetch_latest(now=now, transport=gh(status=429, headers={"X-RateLimit-Reset": str(int(now) + 99999)}))
    assert res["retry_at"] == now + U.MAX_BLOCK_S  # a hostile header cannot silence the check for days
    res = await U.fetch_latest(now=now, transport=gh(status=403, headers={"Retry-After": "1"}))
    assert res["retry_at"] == now + 60  # nor can it make us hammer


async def test_offline_and_unexpected_errors_never_raise():
    def boom(req):
        raise httpx.ConnectError("no route")

    res = await U.fetch_latest(transport=httpx.MockTransport(boom))
    assert res["outcome"] == "offline" and res["reached"] is False and "online" in res["message"]

    def weird(req):
        raise RuntimeError("anything")

    assert (await U.fetch_latest(transport=httpx.MockTransport(weird)))["outcome"] == "error"


# ---- install kinds ------------------------------------------------------------------------------------------------------


def test_only_a_docker_install_with_the_updater_can_apply(tmp_path):
    token = tmp_path / "token"
    token.write_text("t" * 40)
    env = {"LORAPEDIA_INSTALL": "docker", "LORAPEDIA_UPDATER_URL": "http://updater:8081", "LORAPEDIA_UPDATER_TOKEN_FILE": str(token)}
    assert U.detect_install(env)["can_apply"] is True
    no_token = {**env, "LORAPEDIA_UPDATER_TOKEN_FILE": str(tmp_path / "missing")}
    manual = U.detect_install(no_token)
    assert manual["kind"] == "docker-manual" and not manual["can_apply"] and "docker compose pull" in manual["steps"]
    assert U.detect_install({"LORAPEDIA_INSTALL": "docker"})["kind"] == "docker-manual"
    git = U.detect_install({}, root=tmp_path / "repo")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert U.detect_install({}, root=tmp_path / "repo")["kind"] == "git" and git["kind"] == "other"
    assert not U.detect_install({}, root=tmp_path / "repo")["can_apply"]


# ---- the service --------------------------------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def service(tmp_path, transport=None, updater=None, settings=None, busy=None, can_apply=True, clock=None):
    token = tmp_path / "token"
    token.write_text("s3cret-token-" + "x" * 30)
    env = ({"LORAPEDIA_INSTALL": "docker", "LORAPEDIA_UPDATER_URL": "http://updater:8081", "LORAPEDIA_UPDATER_TOKEN_FILE": str(token)}
           if can_apply else {})
    s = settings or Settings()
    return U.UpdateService(store=Store(tmp_path / "s.db"), settings=lambda: s, data_dir=tmp_path, busy=busy or (lambda: None),
                           transport=transport, updater_transport=updater, environ=env, clock=clock or Clock()), s


async def test_a_check_saves_the_release_and_reports_availability(tmp_path):
    svc, _ = service(tmp_path, gh(release("v9.9.9", "Big news.")))
    result = await svc.check()
    st = await svc.status()
    assert result["ok"] and st["update_available"] and st["latest"]["tag"] == "v9.9.9" and st["latest"]["notes"] == "Big news."
    assert st["can_apply"] is True and st["version"] == __version__ and st["last_error"] is None


async def test_the_same_or_an_older_release_is_not_an_update(tmp_path):
    for tag in ("v" + __version__, "v0.0.1"):
        svc, _ = service(tmp_path, gh(release(tag)))
        await svc.check()
        st = await svc.status()
        assert st["latest"]["tag"] == tag and st["update_available"] is False and st["can_apply"] is False


async def test_an_unchanged_answer_keeps_the_saved_release_and_uses_the_etag(tmp_path):
    clock = Clock()
    seen = []
    svc, _ = service(tmp_path, gh(release("v9.9.9"), seen=seen), clock=clock)
    await svc.check()
    clock.t += 100
    svc._transport = gh(status=304, seen=seen)
    await svc.check()
    assert seen[-1].headers["If-None-Match"] == '"abc"'
    assert (await svc.status())["latest"]["tag"] == "v9.9.9"  # still there after a 304


async def test_due_follows_the_setting_the_cadence_and_the_retry_rules(tmp_path):
    clock = Clock()
    svc, settings = service(tmp_path, gh(), clock=clock)
    assert svc.due()  # never asked
    await svc.check()
    assert not svc.due()  # asked a moment ago
    clock.t += U.CHECK_EVERY_S - 10
    assert not svc.due()
    clock.t += 20
    assert svc.due()  # a day later
    settings.update_check = False
    assert not svc.due()  # switched off: the periodic check never fires
    settings.update_check = True

    # an attempt that never reached GitHub is retried after 3 hours, not 24
    def boom(req):
        raise httpx.ConnectError("offline")

    svc2, _ = service(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, httpx.MockTransport(boom), clock=clock)
    await svc2.check()
    assert not svc2.due()
    clock.t += U.RETRY_UNREACHED_S + 1
    assert svc2.due()


async def test_rate_limit_blocks_checks_until_it_expires(tmp_path):
    clock = Clock()
    svc, _ = service(tmp_path, gh(status=429, headers={"Retry-After": "600"}), clock=clock)
    assert (await svc.check())["outcome"] == "rate_limited"
    assert not svc.due()
    again = await svc.check(manual=True)
    assert again["outcome"] == "rate_limited" and "pause" in again["message"]  # even "Check now" waits
    clock.t += 700
    assert svc.due()  # the pause is over and nothing was fetched: ask again now, not in a day


async def test_check_now_cannot_be_hammered(tmp_path):
    svc, _ = service(tmp_path, gh())
    await svc.check(manual=True)
    assert (await svc.check(manual=True))["outcome"] == "recent"


# ---- backup + the request to the updater -------------------------------------------------------------------------------


def updater_ok(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/v1/apply":
            return httpx.Response(202, json={"state": "checking"})
        return httpx.Response(200, json={"state": "idle", "message": ""})
    return httpx.MockTransport(handler)


async def test_apply_backs_up_then_sends_one_parameterless_authenticated_request(tmp_path):
    seen = []
    svc, _ = service(tmp_path, gh(release("v9.9.9")), updater=updater_ok(seen))
    svc.store.set_json("marker", {"keep": "me"})
    await svc.check()
    out = await svc.apply(who="tester")
    assert out["requested"] and out["tag"] == "v9.9.9"
    (req,) = [r for r in seen if r.url.path == "/v1/apply"]
    assert req.method == "POST" and req.content == b"" and req.url.query == b""  # no version, image or registry can be chosen
    assert req.headers["Authorization"].startswith("Bearer s3cret-token-") and str(req.url).startswith("http://updater:8081/")
    backup = tmp_path / "backups" / out["backup"]
    assert backup.exists() and out["backup"].startswith(f"pre-update-{__version__}-to-9.9.9-")
    assert sqlite3.connect(str(backup)).execute("SELECT v FROM kv WHERE k='marker'").fetchone() is not None  # it holds our data
    last = svc.store.get_json("update_last")
    assert last["to"] == "9.9.9" and last["result"] == "requested" and last["backup"] == out["backup"]


async def test_old_backups_are_pruned_to_the_newest_few(tmp_path):
    svc, _ = service(tmp_path, gh(release("v9.9.9")), updater=updater_ok([]))
    folder = tmp_path / "backups"
    folder.mkdir()
    for i in range(8):
        f = folder / f"pre-update-0.0.{i}-to-0.1.0-20200101-00000{i}.db"
        f.write_bytes(b"x")
        os.utime(f, (1000 + i, 1000 + i))
    svc.backup("9.9.9")
    left = sorted(p.name for p in folder.glob("pre-update-*.db"))
    assert len(left) == U.BACKUPS_KEPT and "pre-update-0.0.0-to-0.1.0-20200101-000000.db" not in left


async def test_a_failed_backup_means_nothing_is_asked(tmp_path):
    seen = []
    svc, _ = service(tmp_path, gh(release("v9.9.9")), updater=updater_ok(seen))
    await svc.check()
    (tmp_path / "backups").write_text("a file where the folder should be")  # makes the backup impossible
    with pytest.raises(U.UpdateError, match="backup could not be made"):
        await svc.apply()
    assert not [r for r in seen if r.url.path == "/v1/apply"]


@pytest.mark.parametrize("status,body,fragment", [(401, {}, "bad token"), (409, {"error": "An update is already running."}, "already running"),
                                                  (500, {}, "HTTP 500")])
async def test_updater_refusals_become_plain_errors(tmp_path, status, body, fragment):
    svc, _ = service(tmp_path, gh(release("v9.9.9")), updater=httpx.MockTransport(lambda r: httpx.Response(status, json=body)))
    await svc.check()
    with pytest.raises(U.UpdateError, match=fragment):
        await svc.apply()


async def test_an_unreachable_updater_is_reported(tmp_path):
    def boom(req):
        raise httpx.ConnectError("refused")

    svc, _ = service(tmp_path, gh(release("v9.9.9")), updater=httpx.MockTransport(boom))
    await svc.check()
    with pytest.raises(U.UpdateError, match="could not be reached"):
        await svc.apply()
    st = await svc.status()
    assert st["updater"]["state"] == "unreachable"


async def test_apply_refuses_without_an_available_release_or_outside_docker(tmp_path):
    svc, _ = service(tmp_path, gh(release("v" + __version__)), updater=updater_ok([]))
    await svc.check()
    with pytest.raises(U.UpdateError, match="No newer release"):
        await svc.apply()
    other, _ = service(tmp_path / "o" if (tmp_path / "o").mkdir() is None else tmp_path, gh(release("v9.9.9")), can_apply=False)
    await other.check()
    with pytest.raises(U.UpdateError, match="cannot update itself|source checkout|not Docker"):
        await other.apply()


async def test_a_restarted_app_on_the_new_version_marks_the_update_done(tmp_path):
    svc, _ = service(tmp_path, gh(), updater=updater_ok([]))
    svc.store.set_json("update_last", {"from": "0.0.1", "to": __version__, "ts": 1, "backup": "b.db", "result": "requested"})
    assert (await svc.status())["last_update"]["result"] == "done"


# ---- automatic updates ---------------------------------------------------------------------------------------------------


async def test_auto_apply_waits_for_a_quiet_node_then_goes(tmp_path):
    clock = Clock()
    seen = []
    state = {"busy": "the Wikipedia index is being built"}
    svc, settings = service(tmp_path, gh(release("v9.9.9")), updater=updater_ok(seen), busy=lambda: state["busy"], clock=clock)
    await svc.check()
    assert (await svc.maybe_auto_apply()).startswith("waiting: the Wikipedia index")
    state["busy"] = None
    svc.touch()
    assert "a question was answered a moment ago" in await svc.maybe_auto_apply()
    clock.t += U.IDLE_BEFORE_AUTO_APPLY_S + 1
    assert await svc.maybe_auto_apply() == "applied"
    assert len([r for r in seen if r.url.path == "/v1/apply"]) == 1


async def test_auto_apply_respects_the_setting_and_never_loops(tmp_path):
    clock = Clock()
    seen = []
    svc, settings = service(tmp_path, gh(release("v9.9.9")), updater=updater_ok(seen), clock=clock)
    await svc.check()
    clock.t += 10_000
    settings.auto_update = False
    assert await svc.maybe_auto_apply() == "auto_update off" and not seen
    settings.auto_update = True
    assert await svc.maybe_auto_apply() == "applied"
    assert await svc.maybe_auto_apply() == "already asked recently"  # e.g. it rolled back: do not hammer the updater
    clock.t += 7 * 3600
    assert await svc.maybe_auto_apply() == "applied"  # ...but it may try again much later


async def test_auto_apply_does_nothing_when_there_is_nothing_to_do_or_no_updater(tmp_path):
    svc, _ = service(tmp_path, gh(release("v" + __version__)), updater=updater_ok([]))
    await svc.check()
    assert await svc.maybe_auto_apply() == "nothing to do"
    other, _ = service(tmp_path / "o" if (tmp_path / "o").mkdir() is None else tmp_path, gh(release("v9.9.9")), can_apply=False)
    await other.check()
    assert await other.maybe_auto_apply() == "nothing to do"  # notify-only installs are never auto-updated


# ---- store helpers + API integration ----------------------------------------------------------------------------------------


def test_store_backup_is_consistent_and_last_query_ts(tmp_path):
    store = Store(tmp_path / "a.db")
    assert store.last_query_ts() is None
    store.log_query(sender_id="!a", sender_name="A", question="q", parts=["a"], byte_limit=200, latency_s=1.0, sources=[], debug={})
    assert store.last_query_ts() is not None
    store.backup_to(tmp_path / "b.db")
    assert sqlite3.connect(str(tmp_path / "b.db")).execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 1


def api_client(tmp_path, github=None, updater=None, environ=None):
    opts = {"port_lister": lambda: [], "poll_s": 60, "github_transport": github, "updater_transport": updater, "environ": environ or {}}
    return TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts))


def test_api_check_status_and_apply(tmp_path):
    token = tmp_path / "tok"
    token.write_text("t" * 40)
    env = {"LORAPEDIA_INSTALL": "docker", "LORAPEDIA_UPDATER_URL": "http://updater:8081", "LORAPEDIA_UPDATER_TOKEN_FILE": str(token)}
    seen = []
    with api_client(tmp_path / "d", gh(release("v9.9.9", "Hello")), updater_ok(seen), env) as c:
        st = c.get("/api/updates/status").json()
        assert st["latest"] is None and st["can_apply"] is False and st["install"]["kind"] == "docker"
        r = c.post("/api/updates/check").json()
        assert r["result"]["outcome"] == "ok" and r["update_available"] is True and r["can_apply"] is True
        assert c.post("/api/updates/apply").json()["requested"] is True
        assert [x for x in seen if x.url.path == "/v1/apply"]
        assert c.get("/api/updates/status").json()["last_update"]["result"] == "requested"


def test_api_apply_without_an_update_is_a_plain_400(tmp_path):
    with api_client(tmp_path, gh(release("v" + __version__))) as c:
        c.post("/api/updates/check")
        r = c.post("/api/updates/apply")  # no environment: not a Docker install, so it cannot apply anything
        assert r.status_code == 400 and "updated by hand" in r.json()["detail"]


def test_api_apply_in_docker_with_nothing_newer_says_so(tmp_path):
    token = tmp_path / "tok"
    token.write_text("t" * 40)
    env = {"LORAPEDIA_INSTALL": "docker", "LORAPEDIA_UPDATER_URL": "http://updater:8081", "LORAPEDIA_UPDATER_TOKEN_FILE": str(token)}
    with api_client(tmp_path / "d", gh(release("v" + __version__)), updater_ok([]), env) as c:
        c.post("/api/updates/check")
        r = c.post("/api/updates/apply")
        assert r.status_code == 400 and "No newer release" in r.json()["detail"]


def test_api_settings_expose_the_update_switches(tmp_path):
    with api_client(tmp_path) as c:
        s = c.get("/api/settings").json()
        assert s["update_check"] is True and s["auto_update"] is True
        assert s["wikipedia_update_check"] is True and s["wikipedia_auto_update"] is True
        s["update_check"] = False
        assert c.put("/api/settings", json=s).json()["update_check"] is False
