"""Server-Sent Events helpers."""

from __future__ import annotations

import json
from typing import Any

HEARTBEAT = ": keepalive\n\n"


def event(name: str, data: Any) -> str:
    """Serialise a single named SSE event."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n"
