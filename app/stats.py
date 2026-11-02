"""Small in-process counters for the optional stats endpoint."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class Stats:
    started_at: float
    requests_total: int = 0
    chat_requests: int = 0
    active_streams: int = 0
    upstream_errors: int = 0
    cache_hits: int = 0
    tokens_out: int = 0

    @classmethod
    def create(cls) -> Stats:
        return cls(started_at=time.monotonic())

    def snapshot(self) -> dict[str, int | float]:
        return {
            "uptime_seconds": round(time.monotonic() - self.started_at, 1),
            "requests_total": self.requests_total,
            "chat_requests": self.chat_requests,
            "active_streams": self.active_streams,
            "upstream_errors": self.upstream_errors,
            "cache_hits": self.cache_hits,
            "tokens_out": self.tokens_out,
        }
