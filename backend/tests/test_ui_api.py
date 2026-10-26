"""Endpoints that back the UI: hardware, models, downloads, wizard, stats, exports, static files."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.download import Downloader, DownloadError, fetch_catalog, filename_from_url
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.ollama import PullManager, native_base
from lorapedia.sample import make_sample_zim
from lorapedia.store import Store
from lorapedia.system import suggest_models

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB of recognisable bytes


def hw(ram, gpu=None, board=None):
    return {"ram_gb": ram, "gpu": gpu, "board": board, "cores": 4}


# ---- hardware suggestions -----------------------------------------------------------------


def recommended(s):
    return next(m["tag"] for m in s["llm"] if m["recommended"])


def test_suggestions_scale_with_hardware():
    assert recommended(suggest_models(hw(4, board="Raspberry Pi"))) == "llama3.2:1b"
    assert recommended(suggest_models(hw(8))) == "llama3.2:3b"
    assert recommended(suggest_models(hw(16))) == "llama3.2:3b"
    assert recommended(suggest_models(hw(16, gpu={"name": "RTX", "vram_gb": 8}))) == "llama3.1:8b"
    # Lots of RAM but a small GPU: 8B would run mostly on CPU, so stay with 3B.
    assert recommended(suggest_models(hw(38, gpu={"name": "RTX 3050 Ti", "vram_gb": 4}))) == "llama3.2:3b"
    assert recommended(suggest_models(hw(64))) == "llama3.2:3b"


def test_suggestions_flag_models_that_do_not_fit_and_explain_cpu_only():
    s = suggest_models(hw(4))
    assert [m["fits"] for m in s["llm"]] == [True, False, False]
    assert "No GPU" in s["note"]
    assert sum(m["recommended"] for m in s["embed"]) == 1


# ---- downloader ---------------------------------------------------------------------------


def server(payload=PAYLOAD, honour_range=True, fail_after=None, status=200):
    """A fake Kiwix: supports Range, can drop the connection part-way."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if status != 200:
            return httpx.Response(status)
        start = 0
        rng = request.headers.get("range")
        if rng and honour_range:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(payload):
                return httpx.Response(416)
        body = payload[start:]

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                step = 64 * 1024
                for i in range(0, len(body), step):
                    if fail_after is not None and i >= fail_after:
                        raise httpx.ReadError("connection reset")
                    yield body[i:i + step]

        code = 206 if start else 200
        return httpx.Response(code, headers={"content-length": str(len(body))}, stream=Stream())

    transport = httpx.MockTransport(handler)
    transport.calls = calls
    return transport


async def test_download_completes_and_calls_back(tmp_path):
    done = []

    async def on_done(path):
        done.append(path)

    dl = Downloader(tmp_path, on_done, transport=server())
    job = dl.start("https://example.org/zim/wikipedia_en_top_mini_2026-01.zim")
    await dl.wait(job.id)
    assert job.state == "done" and job.bytes == job.total == len(PAYLOAD)
    assert (tmp_path / job.filename).read_bytes() == PAYLOAD
    assert not (tmp_path / f"{job.filename}.part").exists() and done == [tmp_path / job.filename]


async def test_dropped_connection_keeps_progress_and_resume_uses_range(tmp_path):
    flaky = server(fail_after=300_000)
    dl = Downloader(tmp_path, transport=flaky)
    job = dl.start("https://example.org/a.zim")
    await dl.wait(job.id)
    assert job.state == "error" and "Resume" in job.error
    kept = (tmp_path / "a.zim.part").stat().st_size
    assert 0 < kept < len(PAYLOAD)

    good = server()
    dl._transport = good
    dl.resume(job.id)
    await dl.wait(job.id)
    assert job.state == "done" and (tmp_path / "a.zim").read_bytes() == PAYLOAD
    assert good.calls == [f"bytes={kept}-"]  # asked only for the rest


async def test_server_without_range_support_restarts_cleanly(tmp_path):
    (tmp_path / "a.zim.part").write_bytes(b"stale partial data")
    dl = Downloader(tmp_path, transport=server(honour_range=False))
    job = dl.start("https://example.org/a.zim")
    await dl.wait(job.id)
    assert (tmp_path / "a.zim").read_bytes() == PAYLOAD


async def test_pause_then_resume(tmp_path):
    big = bytes(8 * 1024 * 1024)
    dl = Downloader(tmp_path, transport=server(big))
    job = dl.start("https://example.org/a.zim")
    await asyncio.sleep(0)
    dl.pause(job.id)
    await dl.wait(job.id)
    assert job.state == "paused" and (tmp_path / "a.zim.part").exists()
    dl.resume(job.id)
    await dl.wait(job.id)
    assert job.state == "done" and (tmp_path / "a.zim").stat().st_size == len(big)


async def test_cancel_removes_partial_file(tmp_path):
    dl = Downloader(tmp_path, transport=server(bytes(8 * 1024 * 1024)))
    job = dl.start("https://example.org/a.zim")
    await asyncio.sleep(0)
    dl.cancel(job.id)
    await dl.wait(job.id)
    assert job.state == "cancelled" and not list(tmp_path.glob("*"))


async def test_http_error_is_reported(tmp_path):
    dl = Downloader(tmp_path, transport=server(status=404))
    job = dl.start("https://example.org/a.zim")
    await dl.wait(job.id)
    assert job.state == "error" and "404" in job.error


@pytest.mark.parametrize("url", ["ftp://x/a.zim", "https://x/a.exe", "https://x/", "https://x/../../etc/passwd",
                                 "https://x/.hidden.zim", "file:///etc/passwd"])
def test_unsafe_or_wrong_urls_are_rejected(url):
    with pytest.raises(DownloadError):
        filename_from_url(url)


def test_filename_ignores_query_string():
    assert filename_from_url("https://x/zim/a_b-1.zim?token=1") == "a_b-1.zim"


LISTING = """<pre><a href="../">../</a>
<a href="wikipedia_en_top_mini_2025-12.zim">wikipedia_en_top_mini_2025-12.zim</a>   2025-12-02 10:11  310M
<a href="wikipedia_en_top_mini_2026-02.zim">wikipedia_en_top_mini_2026-02.zim</a>   2026-02-03 10:11  320M
<a href="wikipedia_en_all_nopic_2026-01.zim">wikipedia_en_all_nopic_2026-01.zim</a>   2026-01-05 09:00   48G
<a href="wikipedia_fr_all_mini_2026-01.zim">wikipedia_fr_all_mini_2026-01.zim</a>   2026-01-05 09:00  5.0G
<a href="wikipedia_en_all_maxi_2026-01.zim.torrent">x</a> 2026-01-05 09:00 1K
</pre>"""


async def test_catalog_picks_latest_english_variant():
    t = httpx.MockTransport(lambda r: httpx.Response(200, text=LISTING))
    cat = await fetch_catalog(t, url="https://k.example/")
    assert [(c["variant"], c["date"], c["size"]) for c in cat] == [("top_mini", "2026-02", "320M"),
                                                                   ("all_nopic", "2026-01", "48G")]
    assert cat[0]["url"] == "https://k.example/wikipedia_en_top_mini_2026-02.zim"


async def test_catalog_offline_error_is_actionable():
    def boom(r):
        raise httpx.ConnectError("no route")

    with pytest.raises(DownloadError, match="needs internet"):
        await fetch_catalog(httpx.MockTransport(boom))


# ---- ollama -------------------------------------------------------------------------------


def test_native_base_strips_v1():
    assert native_base(Settings(llm_base_url="http://h:11434/v1/")) == "http://h:11434"


async def test_pull_reports_aggregate_progress_and_completes():
    lines = [{"status": "pulling manifest"},
             {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 40},
             {"status": "pulling bbb", "digest": "bbb", "total": 300, "completed": 0},
             {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 100},
             {"status": "success"}]
    body = "\n".join(json.dumps(x) for x in lines)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text=body)

    pm = PullManager(httpx.MockTransport(handler))
    pm.start("http://o", "llama3.2:3b")
    await pm.wait("llama3.2:3b")
    job = pm.jobs["llama3.2:3b"]
    assert job["state"] == "done" and job["total"] == 400 and job["completed"] == 100
    assert seen["body"]["model"] == "llama3.2:3b"


async def test_pull_error_message_is_surfaced():
    pm = PullManager(httpx.MockTransport(lambda r: httpx.Response(200, text='{"error": "pull model manifest: file does not exist"}')))
    pm.start("http://o", "nope")
    await pm.wait("nope")
    assert pm.jobs["nope"]["state"] == "error" and "does not exist" in pm.jobs["nope"]["error"]


async def test_pull_when_ollama_is_down():
    def boom(r):
        raise httpx.ConnectError("refused")

    pm = PullManager(httpx.MockTransport(boom))
    pm.start("http://o", "x")
    await pm.wait("x")
    assert "Is it running" in pm.jobs["x"]["error"]


# ---- store helpers ------------------------------------------------------------------------


def test_activity_buckets_and_csv_rows():
    store = Store()
    now = time.time()
    for ago_h in (0.1, 0.2, 5.5, 23.5, 30):
        store.log_query(sender_id="!a", sender_name="A", question="q", parts=["a"], byte_limit=200, latency_s=1.0,
                        sources=["X", "Y"], debug={})
        store._db.execute("UPDATE queries SET ts=? WHERE id=(SELECT MAX(id) FROM queries)", (now - ago_h * 3600,))
    hours = store.activity_24h(now)
    assert len(hours) == 24 and hours[-1] == 2 and hours[0] == 1 and sum(hours) == 4  # the 30h-old one is excluded
    assert next(store.export_rows())["sources"] == "X; Y"


def test_get_set_json_roundtrip():
    store = Store()
    assert store.get_json("w", {"a": 1}) == {"a": 1}
    store.set_json("w", {"b": 2})
    assert store.get_json("w") == {"b": 2}


# ---- API ----------------------------------------------------------------------------------


def make_client(tmp_path, transport=None):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    if transport:
        opts["http_transport"] = transport
    return TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts))


def test_api_hardware_and_info(tmp_path):
    with make_client(tmp_path) as c:
        body = c.get("/api/system/hardware").json()
        assert body["hardware"]["cores"] >= 1 and body["hardware"]["ram_gb"] > 0
        assert any(m["recommended"] for m in body["suggestions"]["llm"])
        info = c.get("/api/system/info").json()
        assert info["data_dir"] == str(tmp_path.resolve()) and info["version"]


def test_api_models_list_and_pull(tmp_path):
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:3b", "size": 2_000_000_000,
                                                         "details": {"parameter_size": "3.2B"}}]})
        return httpx.Response(200, text='{"status":"success"}')

    with make_client(tmp_path, httpx.MockTransport(handler)) as c:
        assert c.get("/api/models").json() == {"installed": [{"name": "llama3.2:3b", "size_gb": 2.0, "params": "3.2B"}],
                                               "error": None}
        assert c.post("/api/models/pull", json={"tag": "nomic-embed-text"}).json()["tag"] == "nomic-embed-text"
        for _ in range(100):
            if c.get("/api/models/pulls").json()[0]["state"] == "done":
                break
            time.sleep(0.02)
        assert c.get("/api/models/pulls").json()[0]["state"] == "done"


def test_api_models_when_ollama_down(tmp_path):
    def boom(r):
        raise httpx.ConnectError("refused")

    with make_client(tmp_path, httpx.MockTransport(boom)) as c:
        body = c.get("/api/models").json()
        assert body["installed"] == [] and "Is it running" in body["error"]


def test_api_model_try_applies_unsaved_overrides_without_logging(tmp_path):
    with make_client(tmp_path) as c:
        r = c.post("/api/model/try", json={"question": "what is a quasar?", "temperature": 0.9}).json()
        assert r["parts"] and r["bytes"][0] == len(r["parts"][0].encode()) <= r["byte_limit"]
        assert r["chunks"][0]["title"] == "Quasar"
        assert c.get("/api/queries").json() == []  # a trial is not a query
        assert c.get("/api/settings").json()["temperature"] == 0.2  # and nothing was saved


def test_api_wizard_state_persists_and_merges(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/api/wizard").json()["finished"] is False
        c.put("/api/wizard", json={"done": ["hardware"], "step": 1, "junk": 1})
        c.put("/api/wizard", json={"skipped": ["radio"]})
        state = c.get("/api/wizard").json()
        assert state["done"] == ["hardware"] and state["skipped"] == ["radio"] and state["step"] == 1
        assert "junk" not in state
    with make_client(tmp_path) as c:  # survives a restart
        assert c.get("/api/wizard").json()["done"] == ["hardware"]


def test_api_activity_csv_and_backup(tmp_path):
    with make_client(tmp_path) as c:
        c.post("/api/simulator/send", json={"text": "what is a quasar?"})
        for _ in range(100):
            if c.get("/api/queries").json():
                break
            time.sleep(0.02)
        assert sum(c.get("/api/stats/activity").json()["hours"]) == 1
        csv_text = c.get("/api/queries/export.csv").text
        lines = csv_text.strip().splitlines()
        assert lines[0].startswith("time,sender_id") and "what is a quasar?" in lines[1] and "Quasar" in lines[1]
        backup = c.get("/api/settings/backup")
        assert "attachment" in backup.headers["content-disposition"]
        restored = c.put("/api/settings", json=backup.json())
        assert restored.status_code == 200 and restored.json() == c.get("/api/settings").json()


def test_api_download_endpoints_validate_and_report(tmp_path):
    with make_client(tmp_path, server()) as c:
        assert c.post("/api/kb/downloads", json={"url": "https://x/evil.exe"}).status_code == 400
        job = c.post("/api/kb/downloads", json={"url": "https://example.org/dl.zim"}).json()
        for _ in range(100):
            if c.get("/api/kb/downloads").json()[0]["state"] != "running":
                break
            time.sleep(0.02)
        assert c.get("/api/kb/downloads").json()[0]["state"] in ("done", "error")
        assert c.post("/api/kb/downloads/nope.zim/pause").status_code == 404
        assert c.post(f"/api/kb/downloads/{job['id']}/explode").status_code == 404


def test_api_downloaded_zim_is_registered_in_the_library(tmp_path):
    src = make_sample_zim(tmp_path / "src" / "sample.zim")
    with make_client(tmp_path, server(src.read_bytes())) as c:
        c.post("/api/kb/downloads", json={"url": "https://example.org/sample2.zim"})
        for _ in range(200):
            lib = c.get("/api/kb/library").json()
            if lib:
                break
            time.sleep(0.02)
        assert lib[0]["name"] == "sample2.zim" and lib[0]["articles"] == 12


def test_api_serves_built_ui(tmp_path, monkeypatch):
    ui = tmp_path / "dist"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>LoRaPedia</title>")
    monkeypatch.setenv("LORAPEDIA_STATIC", str(ui))
    with make_client(tmp_path / "data") as c:
        assert "LoRaPedia" in c.get("/").text
        assert c.get("/api/health").json()["radio"] == "simulator"  # API routes win over the static mount
        assert c.get("/api/system/info").json()["static"] is True
