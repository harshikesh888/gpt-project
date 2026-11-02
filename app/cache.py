"""Exact-match response cache for repeated, context-free prompts.

Only the hash of the prompt is used as a key. Cached answer text lives in
process memory and expires automatically. This removes an entire upstream
round-trip for frequently asked questions while keeping memory bounded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CachedReply:
    text: str
    model: str
    finish_reason: str
    created_at: float


def cache_key(mode: str, message: str, history: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {"mode": mode, "message": message, "history": history},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_seconds: int = 900,
        max_entries: int = 256,
        max_chars: int = 6_000,
    ) -> None:
        self.enabled = enabled and ttl_seconds > 0 and max_entries > 0
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_chars = max_chars
        self._entries: OrderedDict[str, CachedReply] = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> CachedReply | None:
        if not self.enabled:
            return None
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if time.monotonic() - entry.created_at > self.ttl_seconds:
                self._entries.pop(key, None)
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry

    async def set(self, key: str, *, text: str, model: str, finish_reason: str) -> None:
        if not self.enabled or not text or len(text) > self.max_chars:
            return
        async with self._lock:
            self._entries[key] = CachedReply(
                text=text,
                model=model,
                finish_reason=finish_reason,
                created_at=time.monotonic(),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
