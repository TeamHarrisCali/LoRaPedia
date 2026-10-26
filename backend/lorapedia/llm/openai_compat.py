"""Client for any OpenAI-compatible chat endpoint (Ollama's /v1, llama.cpp server, LM Studio)."""

import re
from typing import Callable

import httpx

from ..config import Settings

_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    """Raised with a user-actionable message."""


class OpenAICompatLLM:
    def __init__(self, settings: Callable[[], Settings], timeout: float = 300.0) -> None:
        self._settings, self.timeout = settings, timeout  # read per call: UI edits apply immediately

    async def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        s = self._settings()
        base = s.llm_base_url.rstrip("/")
        body = {"model": s.llm_model, "temperature": temperature, "max_tokens": max_tokens, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{base}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach the LLM at {base}. Is Ollama running? ({exc.__class__.__name__})") from exc
        if resp.status_code == 404:
            raise LLMError(f"Model '{s.llm_model}' is not installed. Pull it once with: ollama pull {s.llm_model}")
        if resp.status_code != 200:
            raise LLMError(f"LLM error {resp.status_code}: {resp.text[:200]}")
        content = resp.json()["choices"][0]["message"].get("content") or ""
        return _THINK.sub("", content).strip()  # some reasoning models emit <think> blocks

    async def status(self) -> dict:
        """Cheap reachability check for the status bar: {"ok": bool, "detail": str}."""
        s = self._settings()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{s.llm_base_url.rstrip('/')}/models")
            names = [m["id"] for m in resp.json().get("data", [])]
        except Exception as exc:
            return {"ok": False, "detail": f"unreachable ({exc.__class__.__name__})"}
        if s.llm_model not in names:
            return {"ok": False, "detail": f"model '{s.llm_model}' not installed", "models": names}
        return {"ok": True, "detail": s.llm_model, "models": names}
