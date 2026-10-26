"""One-click Wikipedia packages: download, register, index, replace."""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from lorapedia import packages
from lorapedia.api import create_app
from lorapedia.embed import EmbedError, HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.sample import make_sample_zim

LISTING = """<pre><a href="../">../</a>
<a href="wikipedia_en_top_mini_2026-06.zim">wikipedia_en_top_mini_2026-06.zim</a>   2026-06-03 10:00  331M
<a href="wikipedia_en_top_mini_2026-09.zim">wikipedia_en_top_mini_2026-09.zim</a>   2026-09-03 10:00  283M
<a href="wikipedia_en_top_nopic_2026-09.zim">wikipedia_en_top_nopic_2026-09.zim</a>   2026-09-03 10:00  2.6G
</pre>"""


def fake_kiwix(payload: bytes, listing=LISTING):
    """Kiwix stand-in: the directory listing, plus any .zim file path serving `payload` (with Range)."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/wikipedia/"):
            return httpx.Response(200, text=listing)
        if req.url.path.endswith(".zim"):
            start = int(req.headers["range"].split("=")[1].split("-")[0]) if "range" in req.headers else 0
            body = payload[start:]
            return httpx.Response(206 if start else 200, content=body, headers={"content-length": str(len(body))})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture(scope="module")
def zim_bytes(tmp_path_factory):
    return make_sample_zim(tmp_path_factory.mktemp("z") / "s.zim").read_bytes()


def client(tmp_path, transport, embedder=None):
    opts = {"port_lister": lambda: [], "poll_s": 60, "http_transport": transport}
    return TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=embedder or HashEmbedder(), radio_options=opts))


def wait_for(c, pkg_id, state, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        p = next(p for p in c.get("/api/kb/packages").json() if p["id"] == pkg_id)
        if p["state"] == state:
            return p
        time.sleep(0.03)
    raise AssertionError(f"{pkg_id} never reached {state}: last {p['state']} {p['detail']}")


def test_size_to_mb():
    assert [packages.size_to_mb(x) for x in ("283M", "2.6G", "1T", "512K", "weird")] == [283, 2662, 1048576, 0, 0]


def test_package_for_filename():
    assert packages.package_for_filename("wikipedia_en_top_mini_2026-09.zim")["id"] == "quick"
    assert packages.package_for_filename("wikipedia_fr_top_mini_2026-09.zim") is None
    assert packages.package_for_filename("sample.zim") is None


async def test_resolve_prefers_the_newest_kiwix_file_over_the_pinned_one():
    info = await packages.resolve(packages.BY_ID["quick"], fake_kiwix(b""))
    assert (info["filename"], info["version"], info["size_mb"], info["source"]) == (
        "wikipedia_en_top_mini_2026-09.zim", "2026-09", 283, "kiwix")


async def test_resolve_falls_back_to_the_pinned_file_when_offline():
    def boom(r):
        raise httpx.ConnectError("offline")

    info = await packages.resolve(packages.BY_ID["quick"], httpx.MockTransport(boom))
    assert info["source"] == "pinned" and info["filename"] == f"wikipedia_en_top_mini_{packages.PINNED_VERSION}.zim"
    assert info["size_mb"] == 331


def test_no_package_offers_images():
    assert all("maxi" not in p["variant"] for p in packages.PACKAGES)
    assert sum(p["recommended"] for p in packages.PACKAGES) == 1


def test_list_before_install(tmp_path, zim_bytes):
    with client(tmp_path, fake_kiwix(zim_bytes)) as c:
        listed = c.get("/api/kb/packages").json()
        assert [p["id"] for p in listed] == ["quick", "popular", "complete-compact", "complete"]
        assert all(p["state"] == "available" for p in listed)


def test_install_downloads_registers_and_indexes_in_one_step(tmp_path, zim_bytes):
    with client(tmp_path, fake_kiwix(zim_bytes)) as c:
        r = c.post("/api/kb/packages/quick/install", json={}).json()
        assert r["version"] == "2026-09" and r["source"] == "kiwix"
        ready = wait_for(c, "quick", "ready")
        assert ready["articles"] == 12 and ready["filename"] == "wikipedia_en_top_mini_2026-09.zim"
        assert c.get("/api/kb/stats").json()["articles"] == 12
        found = c.get("/api/kb/search", params={"q": "highest mountain"}).json()
        assert found["mode"] == "index" and found["results"][0]["title"] == "Mount Everest"
        assert c.post("/api/kb/packages/quick/install", json={}).status_code == 409  # already installed


def test_replace_retires_the_old_package_only_after_the_new_one_is_ready(tmp_path, zim_bytes):
    with client(tmp_path, fake_kiwix(zim_bytes)) as c:
        c.post("/api/kb/packages/quick/install", json={})
        wait_for(c, "quick", "ready")
        old = tmp_path / "zim" / "wikipedia_en_top_mini_2026-09.zim"
        assert old.exists()
        c.post("/api/kb/packages/popular/install", json={"replace": True})
        wait_for(c, "popular", "ready")
        names = [z["name"] for z in c.get("/api/kb/library").json()]
        assert names == ["wikipedia_en_top_nopic_2026-09.zim"]  # old package gone from the library
        assert not old.exists()  # and its file deleted to free disk
        assert c.get("/api/kb/stats").json()["articles"] == 12  # not double counted


def test_without_replace_both_packages_stay(tmp_path, zim_bytes):
    with client(tmp_path, fake_kiwix(zim_bytes)) as c:
        c.post("/api/kb/packages/quick/install", json={})
        wait_for(c, "quick", "ready")
        c.post("/api/kb/packages/popular/install", json={})
        wait_for(c, "popular", "ready")
        assert len(c.get("/api/kb/library").json()) == 2


def test_replace_never_deletes_files_it_did_not_download(tmp_path, zim_bytes):
    elsewhere = make_sample_zim(tmp_path / "mine" / "wikipedia_en_top_mini_2020-01.zim")  # user's own file, outside data/zim
    with client(tmp_path / "data", fake_kiwix(zim_bytes)) as c:
        assert c.post("/api/kb/library/import", json={"path": str(elsewhere)}).status_code == 200
        c.post("/api/kb/packages/popular/install", json={"replace": True})
        wait_for(c, "popular", "ready")
        assert [z["name"] for z in c.get("/api/kb/library").json()] == ["wikipedia_en_top_nopic_2026-09.zim"]
        assert elsewhere.exists()  # removed from the library, but the user's file is untouched


def test_refuses_up_front_when_the_embedding_model_is_missing(tmp_path, zim_bytes):
    class NoModel(HashEmbedder):
        async def embed(self, texts, *, query=False):
            raise EmbedError("Embedding model 'nomic-embed-text' is not installed.")

    with client(tmp_path, fake_kiwix(zim_bytes), NoModel()) as c:
        r = c.post("/api/kb/packages/quick/install", json={})
        assert r.status_code == 400 and "nomic-embed-text" in r.json()["detail"] and "keyword-only" in r.json()["detail"]
        assert c.get("/api/kb/downloads").json() == []  # nothing was downloaded


def test_refuses_when_there_is_not_enough_disk(tmp_path, zim_bytes, monkeypatch):
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 10**12 - 100 * 10**6, 100 * 10**6))
    with client(tmp_path, fake_kiwix(zim_bytes)) as c:
        r = c.post("/api/kb/packages/quick/install", json={})
        assert r.status_code == 400 and "Not enough disk space" in r.json()["detail"] and "0.1 GB is free" in r.json()["detail"]
        assert c.get("/api/kb/downloads").json() == []


def test_unknown_package_and_download_errors(tmp_path, zim_bytes):
    with client(tmp_path, fake_kiwix(zim_bytes)) as c:
        assert c.post("/api/kb/packages/nope/install", json={}).status_code == 404
    with client(tmp_path / "b", httpx.MockTransport(lambda r: httpx.Response(200, text=LISTING) if r.url.path.endswith("/")
                                                    else httpx.Response(404))) as c:
        c.post("/api/kb/packages/quick/install", json={})
        p = wait_for(c, "quick", "error")
        assert "404" in p["detail"]


def test_install_over_an_index_built_with_another_embedder_asks_then_rebuilds(tmp_path, zim_bytes):
    class Other(HashEmbedder):
        def __init__(self):
            super().__init__(dim=128)  # a different embedder from the one the sample was indexed with

    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(),
                     radio_options={"port_lister": lambda: [], "poll_s": 60, "http_transport": fake_kiwix(zim_bytes)})
    with TestClient(app) as c:
        c.post("/api/kb/sample")
        c.post("/api/kb/index/start", json={"zim": "sample.zim"})
        for _ in range(100):
            if c.get("/api/kb/index/status").json()["state"] == "done":
                break
            time.sleep(0.03)
        assert c.get("/api/kb/stats").json()["embedder"] == "hash-384"

    # Restart with a different embedder: the user switched embedding model.
    app2 = create_app(str(tmp_path), llm=MockLLM(), embedder=Other(),
                      radio_options={"port_lister": lambda: [], "poll_s": 60, "http_transport": fake_kiwix(zim_bytes)})
    with TestClient(app2) as c:
        asked = c.post("/api/kb/packages/quick/install", json={})
        assert asked.status_code == 409 and "rebuild" in asked.json()["detail"] and "hash-384" in asked.json()["detail"]
        assert c.get("/api/kb/downloads").json() == []  # nothing downloaded until confirmed
        assert c.post("/api/kb/packages/quick/install", json={"replace": True}).status_code == 200
        wait_for(c, "quick", "ready")
        assert [z["name"] for z in c.get("/api/kb/library").json()] == ["wikipedia_en_top_mini_2026-09.zim"]
        assert c.get("/api/kb/stats").json()["embedder"] == "hash-128"
        assert (tmp_path / "zim" / "sample.zim").exists()  # the sample file itself is kept
