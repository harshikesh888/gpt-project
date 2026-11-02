"""Bounded in-memory sliding-window rate limiter.

The limiter deliberately stores no message content and keeps at most
``max_clients`` entries. For multi-instance deployments, replace this with a
shared Redis limiter; the interface is intentionally small.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict, deque


class SlidingWindowLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        max_clients: int = 20_000,
    ) -> None:
        self.limit = max(0, limit)
        self.window_seconds = max(1, window_seconds)
        self.max_clients = max(100, max_clients)
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._checks = 0

    async def check(self, key: str) -> tuple[bool, int, int]:
        """Return ``(allowed, remaining, retry_after_seconds)``."""
        if self.limit == 0:
            return True, 0, 0

        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = deque()
                self._entries[key] = entry
                if len(self._entries) > self.max_clients:
                    self._entries.popitem(last=False)

            while entry and entry[0] <= cutoff:
                entry.popleft()

            if len(entry) >= self.limit:
                retry_after = max(1, math.ceil(entry[0] + self.window_seconds - now))
                self._entries.move_to_end(key)
                return False, 0, retry_after

            entry.append(now)
            self._entries.move_to_end(key)
            remaining = self.limit - len(entry)

            self._checks += 1
            if self._checks % 512 == 0:
                self._prune(cutoff)

            return True, remaining, 0

    def _prune(self, cutoff: float) -> None:
        stale = [
            key
            for key, entry in self._entries.items()
            if not entry or entry[-1] <= cutoff
        ]
        for key in stale:
            self._entries.pop(key, None)
