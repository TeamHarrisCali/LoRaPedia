import re
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Chunk:
    title: str
    text: str
    score: float = 0.0
    section: str = ""
    detail: dict = field(default_factory=dict)  # per-signal ranks, shown in the search playground


class Retriever(Protocol):
    async def search(self, query: str, k: int) -> list[Chunk]: ...


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "of", "what", "who", "how", "why", "to", "in", "and", "do", "does"}


class DemoRetriever:
    """Keyword-overlap search over a tiny built-in corpus (simulator/demo mode).

    Replaced by hybrid FTS5 + vector retrieval over the indexed ZIM in phase 2.
    """

    def __init__(self, corpus: list[Chunk] | None = None) -> None:
        self.corpus = corpus if corpus is not None else DEMO_CORPUS

    async def search(self, query: str, k: int) -> list[Chunk]:
        terms = {w for w in _WORD.findall(query.lower()) if w not in _STOP}
        scored = []
        for c in self.corpus:
            words = set(_WORD.findall(f"{c.title} {c.text}".lower()))
            title_words = set(_WORD.findall(c.title.lower()))
            score = len(terms & words) + 2 * len(terms & title_words)
            if score:
                scored.append(Chunk(c.title, c.text, float(score)))
        return sorted(scored, key=lambda c: -c.score)[:k]


DEMO_CORPUS = [
    Chunk("Quasar", "A quasar is an extremely luminous active galactic nucleus powered by a supermassive "
          "black hole accreting matter. Quasars can outshine all the stars in their host galaxy combined. "
          "Their light takes billions of years to reach Earth."),
    Chunk("Photosynthesis", "Photosynthesis is the process by which plants, algae and some bacteria convert "
          "light energy into chemical energy. It uses carbon dioxide and water and releases oxygen. "
          "It takes place mainly in chloroplasts."),
    Chunk("Mesh networking", "A mesh network is a local network topology in which nodes connect directly "
          "and cooperate to relay data. Meshtastic uses LoRa radios to build long-range mesh networks "
          "without cell service."),
    Chunk("Water purification", "Boiling water for one minute kills most pathogens. At altitudes above "
          "2,000 metres, boil for three minutes. Filters and chemical tablets are common alternatives."),
    Chunk("Cafe", "A café serves coffee and light meals. The word comes from French. "
          "Naïve visitors often confuse it with a bar."),
]
