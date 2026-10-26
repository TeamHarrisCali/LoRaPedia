"""Ollama model management (list, pull with progress). Local network only, no cloud APIs."""

import asyncio
import json
import time
from typing import Any

import httpx

from .config import Settings


def native_base(s: Settings) -> str:
    """Ollama's native API root, derived from the OpenAI-compatible URL (".../v1")."""
    return s.llm_base_url.rstrip("/").removesuffix("/v1")


async def list_models(base: str, transport: httpx.AsyncBaseTransport | None = None) -> list[dict]:
    async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
        resp = await client.get(f"{base}/api/tags")
    resp.raise_for_status()
    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 2),
             "params": (m.get("details") or {}).get("parameter_size", "")} for m in resp.json().get("models", [])]


class PullManager:
    """Runs `ollama pull` in the background and exposes progress for the UI to poll."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self._transport = transport
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, base: str, tag: str) -> dict:
        job = self.jobs.get(tag)
        if job and job["state"] == "running":
            return job
        job = self.jobs[tag] = {"tag": tag, "state": "running", "status": "starting", "completed": 0, "total": 0,
                                "error": None, "started": time.time()}
        self._tasks[tag] = asyncio.create_task(self._run(base, tag, job))
        return job

    async def wait(self, tag: str) -> None:
        await self._tasks[tag]

    async def _run(self, base: str, tag: str, job: dict) -> None:
        layers: dict[str, tuple[int, int]] = {}  # digest -> (completed, total)
        try:
            async with httpx.AsyncClient(timeout=None, transport=self._transport) as client:
                async with client.stream("POST", f"{base}/api/pull", json={"model": tag, "stream": True}) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"Ollama returned {resp.status_code}")
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        msg = json.loads(line)
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        job["status"] = msg.get("status", job["status"])
                        if msg.get("digest") and msg.get("total"):
                            layers[msg["digest"]] = (msg.get("completed", 0), msg["total"])
                            job["completed"] = sum(c for c, _ in layers.values())
                            job["total"] = sum(t for _, t in layers.values())
            job["state"] = "done"
        except httpx.HTTPError as exc:
            job["state"], job["error"] = "error", f"Cannot reach Ollama at {base}. Is it running? ({exc.__class__.__name__})"
        except Exception as exc:
            job["state"], job["error"] = "error", str(exc)
