"""Per-node sliding-window rate limiter."""

import math
import time
from collections import defaultdict, deque
from typing import Callable


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, node: str, max_queries: int, window_s: float) -> int:
        """Return 0 if allowed (and record the hit), else seconds until retry."""
        if max_queries <= 0 or window_s <= 0:
            return 0  # limiting disabled
        now = self._clock()
        hits = self._hits[node]
        while hits and now - hits[0] >= window_s:
            hits.popleft()
        if len(hits) >= max_queries:
            return max(1, math.ceil(window_s - (now - hits[0])))
        hits.append(now)
        return 0

    def pending(self, node: str) -> int:
        """How many reserved slots for this node are still in the future (questions waiting their turn)."""
        now = self._clock()
        return sum(1 for t in self._hits[node] if t > now)

    def reserve(self, node: str, max_queries: int, window_s: float) -> int:
        """Always accepts the question. Returns 0 if it may run now, else the seconds until its turn.

        Over the limit, the question is given the next free slot, and later questions line up behind it
        one window apart, so a burst is answered in order instead of being rejected.
        """
        if max_queries <= 0 or window_s <= 0:
            return 0
        now = self._clock()
        hits = self._hits[node]
        while hits and now - hits[0] >= window_s:
            hits.popleft()
        if len(hits) < max_queries:
            hits.append(now)
            return 0
        slot = hits[len(hits) - max_queries] + window_s  # when the window frees up one more place
        hits.append(slot)
        return max(1, math.ceil(slot - now))
