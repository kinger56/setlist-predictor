"""Rate limiting: "at most N attempts per key in the last W seconds".

Used to stop one visitor from using up the setlist.fm API quota.
It keeps attempts in memory, which is right for one server process.
With several servers, this would move to a shared store like Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable


class Throttle:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock  # swappable, so tests can "wait" instantly
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        attempts = self._attempts[key]
        cutoff = self._clock() - self.window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again (0 = allowed now)."""
        attempts = self._prune(key)
        if len(attempts) < self.limit:
            return 0
        return max(1, int(attempts[0] + self.window - self._clock()) + 1)

    def record(self, key: str) -> None:
        self._prune(key).append(self._clock())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)
