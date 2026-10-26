"""Keeping installed Wikipedia packages current."""

import shutil
import time
from collections import namedtuple
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lorapedia import wikipedia_updates as W
from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.sample import make_sample_zim
from lorapedia.store import Store

LISTING = """<pre><a href="wikipedia_en_top_mini_2026-06.zim">wikipedia_en_top_mini_2026-06.zim</a>   2026-06-03 10:00  300M
<a href="wikipedia_en_top_mini_2026-09.zim">wikipedia_en_top_mini_2026-09.zim</a>   2026-09-03 10:00  283M
<a href="wikipedia_en_top_nopic_2026-06.zim">wikipedia_en_top_nopic_2026-06.zim</a>   2026-06-03 10:00  2.2G
<a href="wikipedia_en_all_mini_2026-09.zim">wikipedia_en_all_mini_2026-09.zim</a>   2026-09-03 10:00  13G
</pre>"""


def kiwix(payload=b"", listing=LISTING):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/wikipedia/"):
            return httpx.Response(200, text=listing)
        if req.url.path.endswith(".zim"):
            start = int(req.headers["range"].split("=")[1].split("-")[0]) if "range" in req.headers else 0
            body = payload[start:]
            return httpx.Response(206 if start else 200, content=body, headers={"content-length": str(len(body))})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def offline():
    def boom(req):
        raise httpx.ConnectError("offline")
    return httpx.MockTransport(boom)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def installed(*names):
    return lambda: [{"name": n} for n in names]


def updater(tmp_path, zims=("wikipedia_en_top_mini_2026-06.zim",), transport=None, settings=None, quiet=None, install=None, clock=None):
    calls = []

    async def fake_install(pkg, **kw):
        calls.append((pkg, kw))
        if install:
            return await install(pkg, **kw)
        return {"id": pkg, "state": "downloading"}

    s = settings or Settings()
    w = W.WikipediaUpdater(store=Store(tmp_path / "w.db"), settings=lambda: s, list_zims=installed(*zims), install=fake_install,
                           quiet=quiet or (lambda: None), transport=transport or kiwix(), clock=clock or Clock())
    return w, calls, s


# ---- reading editions -------------------------------------------------------------------------------------------------


def test_edition_parsing():
    assert W.edition("wikipedia_en_top_mini_2026-09.zim") == "2026-09"
    assert W.edition("sample.zim") is None and W.edition("x_2026-9.zim") is None and W.edition("") is None


def test_only_installed_packages_with_an_edition_count(tmp_path):
    w, _, _ = updater(tmp_path, zims=("sample.zim", "wikipedia_en_top_mini_2026-06.zim", "wikipedia_en_top_mini_2026-09.zim",
                                      "wikipedia_fr_top_mini_2026-09.zim", "mine_2026-01.zim"))
    inst = w.installed()
    assert list(inst) == ["quick"] and [f["edition"] for f in inst["quick"]] == ["2026-09", "2026-06"]  # newest first


# ---- checking ---------------------------------------------------------------------------------------------------------


async def test_check_finds_a_newer_edition_of_an_installed_package_only(tmp_path):
    w, _, _ = updater(tmp_path, zims=("wikipedia_en_top_mini_2026-06.zim", "wikipedia_en_top_nopic_2026-06.zim"))
    res = await w.check()
    st = w.status()
    assert res["ok"] and res["available"] == 1
    (u,) = st["available"]
    assert (u["package"], u["installed"], u["latest"], u["size_mb"]) == ("quick", "2026-06", "2026-09", 283)
    assert u["replaces"] == ["wikipedia_en_top_mini_2026-06.zim"] and u["needs_mb"] == round(283 * 4.0)
    # all_mini has a 2026-09 edition too, but it is not installed, and nopic is already current: neither is offered
    assert {x["package"] for x in st["available"]} == {"quick"}


async def test_nothing_is_offered_when_up_to_date(tmp_path):
    w, _, _ = updater(tmp_path, zims=("wikipedia_en_top_mini_2026-09.zim",))
    assert (await w.check())["available"] == 0 and w.status()["available"] == []


async def test_offline_is_reported_not_raised_and_retried_in_hours_not_days(tmp_path):
    clock = Clock()
    w, _, _ = updater(tmp_path, transport=offline(), clock=clock)
    res = await w.check()
    assert res["ok"] is False and "internet" in res["message"] and w.status()["error"]
    assert not w.due()
    clock.t += W.RETRY_UNREACHED_S + 1
    assert w.due()


async def test_the_check_cadence_and_switches(tmp_path):
    clock = Clock()
    w, _, s = updater(tmp_path, clock=clock)
    assert w.due()
    await w.check()
    assert not w.due()
    clock.t += W.CHECK_EVERY_S + 1
    assert w.due()
    s.wikipedia_update_check = False
    assert not w.due()  # off: the periodic check never fires
    none, _, _ = updater(tmp_path / "n" if (tmp_path / "n").mkdir() is None else tmp_path, zims=("sample.zim",), clock=clock)
    assert not none.due()  # nothing installed that could be updated: Kiwix is not even asked


async def test_a_package_installed_since_the_check_disappears_from_the_list(tmp_path):
    zims = ["wikipedia_en_top_mini_2026-06.zim"]
    w, _, _ = updater(tmp_path)
    w._list_zims = lambda: [{"name": n} for n in zims]
    await w.check()
    assert len(w.status()["available"]) == 1
    zims[:] = ["wikipedia_en_top_mini_2026-09.zim"]  # the update finished
    assert w.status()["available"] == []


# ---- applying -----------------------------------------------------------------------------------------------------------


async def test_apply_replaces_only_the_older_edition_of_the_same_package_and_never_rebuilds(tmp_path):
    w, calls, _ = updater(tmp_path, zims=("wikipedia_en_top_mini_2026-06.zim", "wikipedia_en_top_nopic_2026-06.zim"))
    await w.check()
    await w.apply("quick")
    (pkg, kw), = calls
    assert pkg == "quick" and kw == {"replace": False, "replace_names": ["wikipedia_en_top_mini_2026-06.zim"], "allow_rebuild": False}
    assert w.status()["last"]["quick"]["result"] == "started"


async def test_apply_without_a_known_update_says_to_check_first(tmp_path):
    w, calls, _ = updater(tmp_path)
    with pytest.raises(LookupError, match="Check now"):
        await w.apply("quick")
    assert calls == []


async def test_auto_apply_respects_the_switch_and_waits_for_a_quiet_node(tmp_path):
    state = {"quiet": "questions are waiting"}
    w, calls, s = updater(tmp_path, quiet=lambda: state["quiet"])
    await w.check()
    s.wikipedia_auto_update = False
    assert await w.maybe_auto_apply() == "auto_update off" and calls == []
    s.wikipedia_auto_update = True
    assert (await w.maybe_auto_apply()).startswith("waiting: questions are waiting") and calls == []
    state["quiet"] = None
    assert await w.maybe_auto_apply() == "started quick" and len(calls) == 1
    assert await w.maybe_auto_apply() == "nothing to do" and len(calls) == 1  # already started: not started twice


async def test_a_failed_start_is_recorded_with_its_reason_and_retried_only_after_a_day(tmp_path):
    clock = Clock()

    class Refuse(Exception):
        message = "Not enough disk space: Quick Reference needs about 1.1 GB (including its search index) and 0.1 GB is free."

    async def refuse(pkg, **kw):
        raise Refuse(Refuse.message)

    w, calls, _ = updater(tmp_path, install=refuse, clock=clock)
    await w.check()
    out = await w.maybe_auto_apply()
    assert out.startswith("not started: Not enough disk space")
    last = w.status()["last"]["quick"]
    assert last["result"] == "skipped" and "disk space" in last["message"]  # the UI can tell the user why
    assert await w.maybe_auto_apply() == "nothing to do" and len(calls) == 1  # not hammered
    clock.t += W.RETRY_FAILED_S + 1
    assert (await w.maybe_auto_apply()).startswith("not started") and len(calls) == 2  # tried again a day later


async def test_only_one_package_is_started_per_pass(tmp_path):
    w, calls, _ = updater(tmp_path, zims=("wikipedia_en_top_mini_2026-06.zim", "wikipedia_en_all_mini_2026-06.zim"))
    await w.check()
    assert len(w.status()["available"]) == 2
    await w.maybe_auto_apply()
    assert len(calls) == 1  # indexing is heavy: one at a time


# ---- integration: the real install path ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_bytes(tmp_path_factory):
    return make_sample_zim(tmp_path_factory.mktemp("z") / "s.zim").read_bytes()


def app_with(tmp_path, payload, old_names, embedder=None):
    data = tmp_path / "data"
    (data / "zim").mkdir(parents=True)
    src = make_sample_zim(tmp_path / "src.zim")
    for n in old_names:
        shutil.copy(src, data / "zim" / n)  # auto-registered at startup, as files dropped in the zim folder are
    opts = {"port_lister": lambda: [], "poll_s": 60, "http_transport": kiwix(payload)}
    return TestClient(create_app(str(data), llm=MockLLM(), embedder=embedder or HashEmbedder(), radio_options=opts)), data


def wait(c, pkg, state, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        p = next(p for p in c.get("/api/kb/packages").json() if p["id"] == pkg)
        if p["state"] == state:
            return p
        time.sleep(0.03)
    raise AssertionError(f"{pkg} never reached {state}: {p['state']} {p['detail']}")


def test_end_to_end_the_new_edition_replaces_the_old_and_other_packages_are_untouched(tmp_path, sample_bytes):
    old, other = "wikipedia_en_top_mini_2026-06.zim", "wikipedia_en_top_nopic_2026-06.zim"
    client, data = app_with(tmp_path, sample_bytes, [old, other])
    with client as c:
        r = c.post("/api/kb/updates/check").json()
        assert r["result"]["ok"] and [u["package"] for u in r["available"]] == ["quick"]
        quick = next(p for p in c.get("/api/kb/packages").json() if p["id"] == "quick")
        assert quick["installed_edition"] == "2026-06" and quick["update"]["latest"] == "2026-09"

        assert c.post("/api/kb/packages/quick/update").status_code == 200
        wait(c, "quick", "ready")
        for _ in range(100):  # the old edition is retired just after the new one finishes indexing
            names = sorted(z["name"] for z in c.get("/api/kb/library").json())
            if old not in names:
                break
            time.sleep(0.05)
        assert names == sorted([other, "wikipedia_en_top_mini_2026-09.zim"])  # the new edition, plus the untouched other package
        assert not (data / "zim" / old).exists() and (data / "zim" / other).exists()  # and its file is untouched
        assert c.get("/api/kb/updates").json()["available"] == []


def test_api_update_endpoints_refuse_politely(tmp_path, sample_bytes):
    client, _ = app_with(tmp_path, sample_bytes, ["wikipedia_en_top_mini_2026-06.zim"])
    with client as c:
        r = c.post("/api/kb/packages/quick/update")
        assert r.status_code == 400 and "Check now" in r.json()["detail"]
        assert c.get("/api/kb/updates").json()["check_enabled"] is True
        assert c.post("/api/kb/packages/nope/update").status_code == 400


def test_an_index_built_with_another_embedder_is_never_rebuilt_by_an_update(tmp_path, sample_bytes):
    old = "wikipedia_en_top_mini_2026-06.zim"
    client, data = app_with(tmp_path, sample_bytes, [old])
    with client as c:  # index the old edition with the default embedder first
        c.post("/api/kb/index/start", json={"zim": old})
        for _ in range(200):
            if c.get("/api/kb/index/status").json()["state"] == "done":
                break
            time.sleep(0.03)
    class Other(HashEmbedder):
        def __init__(self):
            super().__init__(dim=128)

    opts = {"port_lister": lambda: [], "poll_s": 60, "http_transport": kiwix(sample_bytes)}
    with TestClient(create_app(str(data), llm=MockLLM(), embedder=Other(), radio_options=opts)) as c:  # user switched models
        c.post("/api/kb/updates/check")
        r = c.post("/api/kb/packages/quick/update")
        assert r.status_code == 409 and "by hand" in r.json()["detail"]  # a person decides about a rebuild
        assert [z["name"] for z in c.get("/api/kb/library").json()] == [old]  # nothing was removed
        assert c.get("/api/kb/downloads").json() == []


def test_not_enough_disk_stops_an_update_before_downloading(tmp_path, sample_bytes, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    client, _ = app_with(tmp_path, sample_bytes, ["wikipedia_en_top_mini_2026-06.zim"])
    with client as c:
        c.post("/api/kb/updates/check")
        monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 10**12 - 50 * 10**6, 50 * 10**6))
        r = c.post("/api/kb/packages/quick/update")
        assert r.status_code == 400 and "Not enough disk space" in r.json()["detail"]
        assert c.get("/api/kb/downloads").json() == []
