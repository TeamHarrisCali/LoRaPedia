"""Street-map tiles: off by default, fetched once from OpenStreetMap on demand, kept on disk, never from anywhere else."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from lorapedia import maptiles
from lorapedia.api import create_app
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.maptiles import MAX_TILE_BYTES, REFRESH_AFTER_S, RETRY_AFTER_S, TileCache, TileError

TILE = maptiles.PNG + b"\x00" * 200


class Net:
    """A fake network that records what was asked and answers with `reply`."""

    def __init__(self, reply=None):
        self.requests: list[httpx.Request] = []
        self.reply = reply or (lambda req: httpx.Response(200, content=TILE))

    def transport(self):
        def handler(req):
            self.requests.append(req)
            return self.reply(req)
        return httpx.MockTransport(handler)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def cache(tmp_path, net, clock=None, **kw):
    return TileCache(tmp_path / "tiles", transport=net.transport(), clock=clock or Clock(), **kw)


async def test_a_tile_is_fetched_once_then_kept(tmp_path):
    net = Net()
    c = cache(tmp_path, net)
    assert await c.get(3, 4, 5) == TILE
    assert await c.get(3, 4, 5) == TILE
    assert len(net.requests) == 1
    assert (tmp_path / "tiles" / "3" / "4" / "5.png").read_bytes() == TILE


async def test_it_asks_openstreetmap_and_says_who_is_asking(tmp_path):
    net = Net()
    await cache(tmp_path, net).get(12, 654, 1583)
    req = net.requests[0]
    assert (req.url.scheme, req.url.host, req.url.path) == ("https", "tile.openstreetmap.org", "/12/654/1583.png")
    assert "LoRaPedia" in req.headers["user-agent"] and "github.com/TeamHarrisCali/LoRaPedia" in req.headers["user-agent"]
    assert set(req.headers) >= {"user-agent"} and "cookie" not in req.headers and "authorization" not in req.headers


@pytest.mark.parametrize("z,x,y", [(-1, 0, 0), (20, 0, 0), (0, 1, 0), (0, 0, 1), (3, 8, 0), (3, 0, 8), (3, -1, 0), (3, 0, -1)])
async def test_a_tile_that_cannot_exist_is_refused_without_asking_anyone(tmp_path, z, x, y):
    net = Net()
    with pytest.raises(TileError) as e:
        await cache(tmp_path, net).get(z, x, y)
    assert e.value.status == 404 and net.requests == []


@pytest.mark.parametrize("reply", [
    lambda r: httpx.Response(200, content=b"<html>captive portal</html>"),                       # not a picture
    lambda r: httpx.Response(200, content=maptiles.PNG + b"\x00" * MAX_TILE_BYTES),               # far too large
    lambda r: httpx.Response(404),
    lambda r: httpx.Response(429),
    lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"}),             # redirects are not followed
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("no internet")),
], ids=["html", "huge", "404", "429", "redirect", "offline"])
async def test_a_bad_answer_is_an_error_and_nothing_is_kept(tmp_path, reply):
    net = Net(reply)
    c = cache(tmp_path, net)
    with pytest.raises(TileError) as e:
        await c.get(2, 1, 1)
    assert e.value.status == 502
    assert not (tmp_path / "tiles").exists() or c.stats()["tiles"] == 0
    assert len(net.requests) == 1 and all(r.url.host == "tile.openstreetmap.org" for r in net.requests)


async def test_a_failed_tile_is_not_asked_for_again_for_a_minute(tmp_path):
    clock = Clock()
    net = Net(lambda r: httpx.Response(503))
    c = cache(tmp_path, net, clock)
    for _ in range(3):
        with pytest.raises(TileError):
            await c.get(2, 1, 1)
    assert len(net.requests) == 1
    clock.now += RETRY_AFTER_S + 1
    net.reply = lambda r: httpx.Response(200, content=TILE)
    assert await c.get(2, 1, 1) == TILE and len(net.requests) == 2


async def test_an_old_tile_is_refreshed_and_the_old_one_is_used_if_that_fails(tmp_path):
    clock = Clock()
    net = Net()
    c = cache(tmp_path, net, clock)
    await c.get(1, 0, 0)
    path = tmp_path / "tiles" / "1" / "0" / "0.png"
    import os
    os.utime(path, (clock.now, clock.now))
    clock.now += REFRESH_AFTER_S - 10
    await c.get(1, 0, 0)
    assert len(net.requests) == 1                      # still fresh
    clock.now += 20
    net.reply = lambda r: httpx.Response(503)
    assert await c.get(1, 0, 0) == TILE                # the old picture is better than none
    assert len(net.requests) == 2
    net.reply = lambda r: httpx.Response(200, content=maptiles.PNG + b"new")
    clock.now += RETRY_AFTER_S + 1
    assert await c.get(1, 0, 0) == maptiles.PNG + b"new"


async def test_two_requests_for_one_tile_fetch_it_once(tmp_path):
    net = Net()
    c = cache(tmp_path, net)
    a, b = await asyncio.gather(c.get(5, 3, 3), c.get(5, 3, 3))
    assert a == b == TILE and len(net.requests) == 1


async def test_at_most_two_downloads_run_at_once(tmp_path):
    running = peak = 0
    gate = asyncio.Event()

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await gate.wait()
            running -= 1
            return httpx.Response(200, content=TILE)

    c = TileCache(tmp_path / "tiles", transport=Slow())
    jobs = [asyncio.create_task(c.get(4, i, 0)) for i in range(6)]
    await asyncio.sleep(0.05)
    assert peak == 2
    gate.set()
    await asyncio.gather(*jobs)


async def test_the_cache_is_trimmed_to_its_limit_oldest_first(tmp_path, monkeypatch):
    net = Net()
    c = cache(tmp_path, net, limit_bytes=len(TILE) * 5)
    import os
    for i in range(8):
        await c.get(4, i, 0)
        p = tmp_path / "tiles" / "4" / str(i) / "0.png"
        os.utime(p, (1000 + i, 1000 + i))                 # tile 0 is the oldest
    c._prune()
    left = sorted(int(p.parent.name) for p in c._files())
    assert c.stats()["bytes"] <= c.limit * 0.9 and left == list(range(8 - len(left), 8))   # the newest ones survive


async def test_stats_and_clear(tmp_path):
    c = cache(tmp_path, Net())
    assert c.stats()["tiles"] == 0
    await c.get(1, 0, 0)
    await c.get(1, 1, 0)
    assert c.stats()["tiles"] == 2 and c.stats()["bytes"] == 2 * len(TILE)
    assert c.clear() == 2 and c.stats()["tiles"] == 0


def test_the_address_is_fixed_and_not_a_setting():
    assert maptiles.TILE_URL.startswith("https://tile.openstreetmap.org/")
    assert not [k for k in Settings.model_fields if "tile" in k and k != "map_tiles"]
    assert Settings().map_tiles is False                    # nothing is fetched unless the operator turns it on


# ---- through the web API --------------------------------------------------------------------------

def client(tmp_path, net):
    app = create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options={"port_lister": lambda: [], "poll_s": 60})
    app.state.tiles = cache(tmp_path / "x", net)
    return TestClient(app)


def test_off_by_default_nothing_is_fetched(tmp_path):
    net = Net()
    with client(tmp_path, net) as c:
        r = c.get("/api/map/tiles/3/4/5.png")
        assert r.status_code == 404 and "off" in r.json()["detail"].lower()
        assert net.requests == []
        assert c.get("/api/map/tiles").json()["enabled"] is False


def test_turned_on_it_serves_a_png_and_keeps_it(tmp_path):
    net = Net()
    with client(tmp_path, net) as c:
        assert c.put("/api/settings", json={"map_tiles": True}).status_code == 200
        r = c.get("/api/map/tiles/3/4/5.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == TILE
        assert "max-age" in r.headers["cache-control"]
        c.get("/api/map/tiles/3/4/5.png")
        assert len(net.requests) == 1
        info = c.get("/api/map/tiles").json()
        assert info["enabled"] and info["tiles"] == 1 and "OpenStreetMap" in info["attribution"]
        assert c.delete("/api/map/tiles").json() == {"removed": 1}
        assert c.get("/api/map/tiles").json()["tiles"] == 0


def test_turning_it_off_again_stops_serving_even_what_is_kept(tmp_path):
    net = Net()
    with client(tmp_path, net) as c:
        c.put("/api/settings", json={"map_tiles": True})
        assert c.get("/api/map/tiles/1/0/0.png").status_code == 200
        c.put("/api/settings", json={"map_tiles": False})
        assert c.get("/api/map/tiles/1/0/0.png").status_code == 404


@pytest.mark.parametrize("path,status", [
    ("/api/map/tiles/20/0/0.png", 404), ("/api/map/tiles/3/9/0.png", 404), ("/api/map/tiles/a/0/0.png", 422),
    ("/api/map/tiles/3/0/0.jpg", 404), ("/api/map/tiles/3/../../x/0.png", 404), ("/api/map/tiles/3/0/-1.png", 404),
])
def test_odd_requests_are_refused_and_reach_no_one(tmp_path, path, status):
    net = Net()
    with client(tmp_path, net) as c:
        c.put("/api/settings", json={"map_tiles": True})
        assert c.get(path).status_code == status
        assert net.requests == []


def test_a_failure_upstream_is_a_plain_502(tmp_path):
    net = Net(lambda r: httpx.Response(503))
    with client(tmp_path, net) as c:
        c.put("/api/settings", json={"map_tiles": True})
        r = c.get("/api/map/tiles/3/1/1.png")
        assert r.status_code == 502 and "503" in r.json()["detail"]
