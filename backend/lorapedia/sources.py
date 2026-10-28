"""Which retrieved article an answer actually came from. Shown in the web panel; never sent over the radio, where every byte is airtime."""
import re

from .retrieval.base import _STOP

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def order_sources(answer: str, chunks: list) -> list[str]:
    """Unique article titles, the one that best supports the answer first.

    Retrieval ranks by how well a page matches the QUESTION, so its top page is not always the one the answer was written from
    ("capital of France" ranks Île-de-France first, but the answer "Paris" comes from the Paris page). An article scores by the share
    of the answer's words found in its text, plus a bonus when the answer names the article; ties keep retrieval order.
    """
    ans = _words(answer)
    first: dict[str, int] = {}
    score: dict[str, float] = {}
    for rank, c in enumerate(chunks):
        title_words = _words(c.title)
        s = (len(ans & _words(c.text)) / len(ans) if ans else 0.0) + (1.0 if title_words and title_words <= ans else 0.0)
        first.setdefault(c.title, rank)
        score[c.title] = max(score.get(c.title, 0.0), s)
    return sorted(first, key=lambda t: (-score[t], first[t]))
