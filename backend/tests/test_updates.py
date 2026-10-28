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


# ---- the beta channel ------------------------------------------------------------------------------------------------------


def ghb(releases, seen=None, etag='"beta1"'):
    """GitHub's list of recent releases, which only the beta channel asks for."""
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == U.BETA_URL  # a second constant address, never built from input
        if seen is not None:
            seen.append(req)
        return httpx.Response(200, content=json.dumps(releases).encode(), headers={"ETag": etag})
    return httpx.MockTransport(handler)


def beta_settings(**kw):
    return Settings(update_channel="beta", **kw)


@pytest.mark.parametrize("tag,ok", [("v0.2.0-beta.1", True), ("v1.10.3-beta.12", True), ("v0.2.0-beta", False), ("v0.2.0-beta.01", False),
                                    ("v0.2.0-rc.1", False), ("v0.2.0-beta.1\n", False), ("v0.2.0", False), ("0.2.0-beta.1", False),
                                    ("v0.2.0-BETA.1", False), (None, False), (5, False)])
def test_only_exact_beta_tags_are_beta_tags(tag, ok):
    assert U.valid_beta_tag(tag) is ok
    assert U.valid_tag(tag) is (tag == "v0.2.0")  # stable-only checks never accept a beta


def test_betas_sort_below_their_release_and_numerically():
    assert U.is_newer("v0.2.0-beta.1", "0.1.5") and U.is_newer("v0.2.0-beta.10", "0.2.0-beta.9") and U.is_newer("v0.2.0", "0.2.0-beta.7")
    assert not U.is_newer("v0.2.0-beta.7", "0.2.0") and not U.is_newer("v0.2.0-beta.1", "0.2.0-beta.1")
    assert not U.is_newer("v0.2.0-rc.1", "0.1.5") and U.is_newer("v0.2.0-beta.1", "0.2.0-rc.1")  # other names sort below any beta
    assert U.parse_version("0.2.0-beta.9") < U.parse_version("0.2.0-beta.10") < U.parse_version("0.2.0")


def rel(tag, pre=None, draft=False, body="Notes."):
    return {"tag_name": tag, "prerelease": ("-" in tag) if pre is None else pre, "draft": draft, "body": body}


def test_the_beta_channel_picks_the_highest_acceptable_release_and_builds_its_own_link():
    r = U.clean_release([rel("v0.3.0-rc.1"), rel("v0.2.0-beta.2"), rel("v0.2.0-beta.10"), rel("v0.1.9"), rel("v9.9.9", draft=True)], "beta")
    assert r["tag"] == "v0.2.0-beta.10" and r["url"] == "https://github.com/TeamHarrisCali/LoRaPedia/releases/tag/v0.2.0-beta.10"
    assert U.clean_release([rel("v0.2.0"), rel("v0.2.0-beta.4")], "beta")["tag"] == "v0.2.0"  # the final release outranks its betas


@pytest.mark.parametrize("doc", [[rel("v0.2.0-beta.1", pre=False)], [rel("v0.2.0", pre=True)], [rel("v0.2.0-beta.1", draft=True)],
                                 [rel("nightly")], [rel("v0.2.0-rc.1")], [], {"tag_name": "v0.2.0-beta.1"}, "x", None, [None, 5]])
def test_the_beta_channel_ignores_what_it_does_not_recognise(doc):
    assert U.clean_release(doc, "beta") is None


def test_the_stable_channel_still_refuses_betas_and_lists():
    assert U.clean_release(rel("v0.2.0-beta.1")) is None and U.clean_release([rel("v0.2.0")]) is None


async def test_a_beta_node_is_offered_a_newer_beta(tmp_path):
    svc, _ = service(tmp_path, ghb([rel("v9.0.0-beta.2", body="Try the new thing."), rel("v0.1.0")]), settings=beta_settings())
    assert (await svc.check())["ok"]
    st = await svc.status()
    assert st["channel"] == "beta" and st["update_available"] and st["latest"]["tag"] == "v9.0.0-beta.2" and st["latest"]["beta"] is True


async def test_a_stable_node_never_asks_for_or_sees_betas(tmp_path):
    svc, _ = service(tmp_path, gh(release("v9.9.9")))  # the stable address only: asking for the list would fail the helper's assertion
    await svc.check()
    st = await svc.status()
    assert st["channel"] == "stable" and st["latest"]["tag"] == "v9.9.9" and st["latest"]["beta"] is False


async def test_switching_channel_forgets_the_old_answer_and_asks_again_at_once(tmp_path):
    s = Settings()
    svc, _ = service(tmp_path, gh(release("v9.9.9")), settings=s)
    await svc.check()
    assert not svc.due() and svc.latest()["tag"] == "v9.9.9"
    s.update_channel = "beta"
    assert svc.latest() is None and svc.due()  # nothing carried over from the stable answer, and the next pass asks right away
    svc._transport = ghb([rel("v9.9.10-beta.1")])
    await svc.check()
    assert svc.latest()["tag"] == "v9.9.10-beta.1" and not svc.due()
    s.update_channel = "stable"
    assert svc.latest() is None and svc.due()  # and back: a beta is never shown on the stable channel


async def test_an_etag_is_not_reused_for_a_different_channel(tmp_path):
    seen = []
    s = Settings()
    svc, _ = service(tmp_path, gh(release("v9.9.9"), etag='"stable-tag"'), settings=s)
    await svc.check()
    s.update_channel = "beta"
    svc._transport = ghb([rel("v9.9.10-beta.1")], seen=seen)
    await svc.check()
    assert "If-None-Match" not in seen[0].headers  # the stable ETag belongs to another address


async def test_applying_a_beta_sends_the_word_beta_and_nothing_else(tmp_path):
    seen = []
    svc, _ = service(tmp_path, ghb([rel("v9.9.9-beta.1")]), updater=updater_ok(seen), settings=beta_settings())
    await svc.check()
    out = await svc.apply()
    assert out["tag"] == "v9.9.9-beta.1" and out["backup"].startswith(f"pre-update-{__version__}-to-9.9.9-beta.1-")
    (req,) = [r for r in seen if r.url.path == "/v1/apply"]
    assert json.loads(req.content) == {"channel": "beta"} and req.url.query == b""


async def test_applying_a_stable_release_sends_no_body_as_before(tmp_path):
    seen = []
    svc, _ = service(tmp_path, gh(release("v9.9.9")), updater=updater_ok(seen))
    await svc.check()
    await svc.apply()
    (req,) = [r for r in seen if r.url.path == "/v1/apply"]
    assert req.content == b""  # an older updater that knows nothing of channels keeps working


async def test_the_beta_list_may_be_larger_than_a_single_release_but_not_unbounded(tmp_path):
    big = [rel("v9.9.9-beta.1", body="x" * 10000)] * 30 + [rel("v0.1.0")]
    assert U.MAX_RESPONSE_BYTES < len(json.dumps(big)) < U.MAX_LIST_BYTES  # too big for one release's cap, fine for the list
    svc, _ = service(tmp_path, ghb(big), settings=beta_settings())
    assert (await svc.check())["ok"]
    (tmp_path / "two").mkdir()
    svc2, _ = service(tmp_path / "two", ghb([rel("v9.9.9-beta.1", body="x" * (U.MAX_LIST_BYTES + 10))]), settings=beta_settings())
    assert (await svc2.check())["outcome"] == "too_large"


def test_manual_steps_for_the_beta_channel():
    git = {"kind": "git", "steps": ["git pull", "./run.sh"]}
    assert U.manual_steps(git, "stable", None) == ["git pull", "./run.sh"]
    assert U.manual_steps(git, "beta", None) == ["git fetch", "git switch beta", "git pull", "./run.sh"]
    docker = {"kind": "docker-manual", "steps": ["docker compose pull", "docker compose up -d"]}
    steps = U.manual_steps(docker, "beta", {"tag": "v0.2.0-beta.3"})
    assert "ghcr.io/teamharriscali/lorapedia:0.2.0-beta.3" in steps[0] and steps[1] == "docker compose up -d"
    assert U.manual_steps(docker, "beta", None) == docker["steps"]


def test_the_channel_defaults_to_stable_and_an_older_saved_settings_file_still_loads():
    assert Settings().update_channel == "stable"
    assert Settings.model_validate_json('{"byte_limit": 150}').update_channel == "stable"
    with pytest.raises(ValueError):
        Settings(update_channel="nightly")


def updater_says(status):
    """An updater that accepts the request and then reports `status` (the updater's own /v1/status document)."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/apply":
            return httpx.Response(202, json={"state": "checking"})
        return httpx.Response(200, json=status)
    return httpx.MockTransport(handler)


async def outdated_after_requesting(tmp_path, status, settings=None, release_doc=None):
    clock = Clock()
    s = settings or beta_settings()
    svc, _ = service(tmp_path, ghb([rel("v9.9.9-beta.1")]) if s.update_channel == "beta" else gh(release("v9.9.9")),
                     updater=updater_says({**status, "updated": clock.t + 1}), settings=s, clock=clock)
    await svc.check()
    await svc.apply()
    return (await svc.status())["updater_outdated"]


OLD = {"state": "idle", "message": "Already on the newest release (0.1.6).", "current": "0.1.6", "target": "0.1.6"}


async def test_an_updater_that_answers_a_beta_request_as_if_only_stable_existed_is_called_out(tmp_path):
    """What an updater from before the beta channel does: it ignores "beta" and reports being up to date. The page must say so
    instead of showing a harmless-looking "Already on the newest release"."""
    assert await outdated_after_requesting(tmp_path, OLD) is True


@pytest.mark.parametrize("status", [
    {"state": "idle", "message": "Already on the newest release", "current": "9.9.9-beta.1", "target": "9.9.9-beta.1"},  # it did consider the beta
    {"state": "pulling", "message": "Downloading", "target": "9.9.9-beta.1"}, {"state": "failed", "message": "x", "target": "0.1.6"},
    {"state": "checking", "message": "", "target": None}, {"state": "idle", "message": "", "target": None}])
async def test_a_current_updater_working_on_or_failing_the_beta_is_not_called_outdated(tmp_path, status):
    assert await outdated_after_requesting(tmp_path, status) is False


async def test_the_stable_channel_never_blames_the_updater(tmp_path):
    assert await outdated_after_requesting(tmp_path, OLD, settings=Settings()) is False


async def test_an_idle_report_from_before_the_request_is_not_taken_for_an_answer_to_it(tmp_path):
    clock = Clock()
    svc, _ = service(tmp_path, ghb([rel("v9.9.9-beta.1")]), updater=updater_says({**OLD, "updated": clock.t - 3600}), settings=beta_settings(), clock=clock)
    await svc.check()
    await svc.apply()
    assert (await svc.status())["updater_outdated"] is False


async def test_no_request_no_blame(tmp_path):
    svc, _ = service(tmp_path, ghb([rel("v9.9.9-beta.1")]), updater=updater_says({**OLD, "updated": 5_000_000.0}), settings=beta_settings())
    await svc.check()
    assert (await svc.status())["updater_outdated"] is False


# ---- returning from a beta to stable ---------------------------------------------------------------------------------------


BETA_VERSION = "0.2.0-beta.3"


async def test_a_beta_returns_to_the_newest_stable_after_a_backup_and_with_the_one_request_the_updater_accepts(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    seen = []
    svc, _ = service(tmp_path, gh(release("v0.1.10")), updater=updater_ok(seen), settings=beta_settings())
    svc.store.set_json("marker", {"keep": "me"})
    out = await svc.leave_beta()
    assert out["tag"] == "v0.1.10" and out["downgrade"] is True and out["backup"].startswith("pre-update-0.2.0-beta.3-to-0.1.10-")
    (req,) = [r for r in seen if r.url.path == "/v1/apply"]
    assert json.loads(req.content) == {"channel": "stable", "leave_beta": True} and req.url.query == b""  # no version, no image
    assert sqlite3.connect(str(tmp_path / "backups" / out["backup"])).execute("SELECT v FROM kv WHERE k='marker'").fetchone() is not None
    last = svc.store.get_json("update_last")
    assert last["downgrade"] is True and last["to"] == "0.1.10" and last["from"] == BETA_VERSION and last["result"] == "requested"


async def test_only_a_beta_may_use_return_to_stable(tmp_path):
    svc, _ = service(tmp_path, gh(release("v0.1.10")), updater=updater_ok([]))  # the running version here is a stable release
    with pytest.raises(U.UpdateError, match="not a beta"):
        await svc.leave_beta()
    assert not (tmp_path / "backups").exists()


async def test_return_to_stable_needs_the_updater_container(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    svc, _ = service(tmp_path, gh(release("v0.1.10")), can_apply=False)
    with pytest.raises(U.UpdateError, match="update itself|updated by hand|cannot"):
        await svc.leave_beta()


@pytest.mark.parametrize("transport", [gh(status=404), gh(status=403, headers={"Retry-After": "120"}), gh(raw=b"not json"),
                                       gh(release("v0.2.0-beta.4", pre=True))])
async def test_if_the_newest_stable_release_cannot_be_found_nothing_is_changed(tmp_path, monkeypatch, transport):
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    seen = []
    svc, _ = service(tmp_path, transport, updater=updater_ok(seen))
    with pytest.raises(U.UpdateError, match="could not be found"):
        await svc.leave_beta()
    assert not seen and not (tmp_path / "backups").exists()  # not even a backup or a request


async def test_an_updater_that_refuses_the_request_is_named_as_too_old(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    refuse = httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "bad request"}))
    svc, _ = service(tmp_path, gh(release("v0.1.10")), updater=refuse)
    with pytest.raises(U.UpdateError, match="docker compose pull updater"):
        await svc.leave_beta()


async def test_an_updater_that_ignores_the_request_is_called_out_by_the_status(tmp_path, monkeypatch):
    """The oldest updaters ignore the body and answer as if nothing newer existed: idle, straight away."""
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    clock = Clock()
    ignoring = updater_says({"state": "idle", "message": "Already on the newest release", "target": "0.1.10", "updated": clock.t + 1})
    svc, _ = service(tmp_path, gh(release("v0.1.10")), updater=ignoring, clock=clock)
    await svc.leave_beta()
    assert (await svc.status())["updater_outdated"] is True
    busy = updater_says({"state": "pulling", "message": "Downloading", "target": "0.1.10", "updated": clock.t + 1})
    (tmp_path / "two").mkdir()
    svc2, _ = service(tmp_path / "two", gh(release("v0.1.10")), updater=busy, clock=Clock())
    await svc2.leave_beta()
    assert (await svc2.status())["updater_outdated"] is False


async def test_the_status_says_when_a_beta_can_return_and_what_to_type_when_it_cannot_do_it_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    svc, _ = service(tmp_path, gh(release("v0.1.10")))
    st = await svc.status()
    assert st["on_beta"] is True and st["can_leave_beta"] is True and st["leave_beta_steps"] == []
    (tmp_path / "m").mkdir()
    manual, _ = service(tmp_path / "m", gh(release("v0.1.10")), can_apply=False)
    st = await manual.status()
    assert st["on_beta"] and not st["can_leave_beta"]
    monkeypatch.undo()
    stable, _ = service(tmp_path / "m", gh(release("v0.1.10")))
    assert (await stable.status())["on_beta"] is False and (await stable.status())["leave_beta_steps"] == []


def test_the_manual_steps_for_leaving_the_beta():
    assert U.leave_beta_steps({"kind": "git"}) == ["git switch main", "git pull", "./run.sh"]
    steps = U.leave_beta_steps({"kind": "docker-manual"})
    assert "ghcr.io/teamharriscali/lorapedia:latest" in steps[0] and steps[1] == "docker compose up -d"
    assert U.leave_beta_steps({"kind": "other"}) == []


def docker_env(tmp_path):
    token = tmp_path / "tok"
    token.write_text("t" * 40)
    return {"LORAPEDIA_INSTALL": "docker", "LORAPEDIA_UPDATER_URL": "http://updater:8081", "LORAPEDIA_UPDATER_TOKEN_FILE": str(token)}


def test_api_return_to_stable_sets_the_channel_to_stable_and_asks_the_updater(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    seen = []
    with api_client(tmp_path, gh(release("v0.1.10")), updater_ok(seen), docker_env(tmp_path)) as c:
        c.put("/api/settings", json={**c.get("/api/settings").json(), "update_channel": "beta"})
        r = c.post("/api/updates/leave-beta")
        assert r.status_code == 200 and r.json()["tag"] == "v0.1.10" and r.json()["downgrade"] is True
        assert c.get("/api/settings").json()["update_channel"] == "stable"
        assert [json.loads(q.content) for q in seen if q.url.path == "/v1/apply"] == [{"channel": "stable", "leave_beta": True}]


def test_api_the_channel_goes_back_to_stable_even_when_the_return_itself_fails(tmp_path, monkeypatch):
    """So that, whatever goes wrong, no further beta is offered afterwards."""
    monkeypatch.setattr(U, "__version__", BETA_VERSION)
    with api_client(tmp_path, gh(status=404), updater_ok([]), docker_env(tmp_path)) as c:
        c.put("/api/settings", json={**c.get("/api/settings").json(), "update_channel": "beta"})
        r = c.post("/api/updates/leave-beta")
        assert r.status_code == 400 and "could not be found" in r.json()["detail"]
        assert c.get("/api/settings").json()["update_channel"] == "stable"


def test_api_a_stable_node_cannot_use_it(tmp_path):
    with api_client(tmp_path, gh(release("v0.1.10")), updater_ok([]), docker_env(tmp_path)) as c:
        r = c.post("/api/updates/leave-beta")
        assert r.status_code == 400 and "not a beta" in r.json()["detail"]
