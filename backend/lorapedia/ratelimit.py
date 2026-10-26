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
