"""Embedding backends. Vectors are always L2-normalised so L2 distance ranks like cosine."""

import hashlib
import math
import re
from typing import Protocol

import httpx

_WORD = re.compile(r"[a-z0-9]+")


class EmbedError(RuntimeError):
    """Raised with a user-actionable message (shown as a toast in the UI)."""


class Embedder(Protocol):
    name: str  # stored in the index; a different embedder means the vectors are incompatible

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class HashEmbedder:
    """Dependency-free hashing bag-of-words embedder (unigrams + bigrams).

    Not semantic, but fully offline and instant: used for tests and as a lightweight
    fallback so the index works before any embedding model is installed.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        vec = [0.0] * self.dim
        for tok in words + [f"{a}_{b}" for a, b in zip(words, words[1:])]:
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return _normalise(vec)


class OllamaEmbedder:
    """Embeddings via Ollama's /api/embed (e.g. nomic-embed-text)."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text",
                 timeout: float = 120.0) -> None:
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.name = f"ollama:{model}"

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if self.model.startswith("nomic-embed-text"):  # this model expects task prefixes
            prefix = "search_query: " if query else "search_document: "
            texts = [prefix + t for t in texts]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts})
        except httpx.HTTPError as exc:
            raise EmbedError(f"Cannot reach Ollama at {self.base_url}. Is it running? ({exc.__class__.__name__})") from exc
        if resp.status_code == 404:
            raise EmbedError(f"Embedding model '{self.model}' is not installed. Pull it once with: ollama pull {self.model}")
        if resp.status_code != 200:
            raise EmbedError(f"Ollama embedding error {resp.status_code}: {resp.text[:200]}")
        return [_normalise(v) for v in resp.json()["embeddings"]]
