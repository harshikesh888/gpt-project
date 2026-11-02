"""Streaming-safe text filters."""

from __future__ import annotations

_OPEN = "<think>"
_OPEN_LEN = len(_OPEN)
_CLOSE = "</think>"
_CLOSE_LEN = len(_CLOSE)


class LeadingThinkFilter:
    """Strip a ``<think>...</think>`` block only when it starts the response.

    Some reasoning models inline their chain of thought in ``content`` instead
    of a dedicated ``reasoning_content`` field. Removing it in the backend
    avoids shipping hidden reasoning to the browser, but doing so naively
    breaks when tags are split across SSE chunks. This state machine handles
    partial tags and leaves ordinary text (including code that mentions
    ``<think>`` later in the answer) untouched.
    """

    def __init__(self) -> None:
        self._state = "detect"
        self._pending = ""
        self.reasoning_started = False
        self.reasoning_finished = False

    def feed(self, text: str) -> str:
        if self._state == "passthrough":
            return text

        self._pending += text
        out: list[str] = []

        while True:
            if self._state == "detect":
                stripped = self._pending.lstrip()
                lowered = stripped.lower()

                if lowered.startswith(_OPEN):
                    self.reasoning_started = True
                    self._pending = stripped[_OPEN_LEN:]
                    self._state = "thinking"
                    continue

                if _OPEN.startswith(lowered) and len(lowered) < _OPEN_LEN:
                    # A tag may still be arriving across chunks.
                    return "".join(out)

                out.append(self._pending)
                self._pending = ""
                self._state = "passthrough"
                return "".join(out)

            # thinking
            index = self._pending.lower().find(_CLOSE)
            if index != -1:
                remainder = self._pending[index + _CLOSE_LEN :]
                self._pending = ""
                self._state = "passthrough"
                self.reasoning_finished = True
                out.append(remainder)
                return "".join(out)

            # Keep only enough tail to detect a closing tag split across chunks.
            if len(self._pending) > _CLOSE_LEN:
                self._pending = self._pending[-_CLOSE_LEN:]
            return "".join(out)

    def flush(self) -> str:
        if self._state == "detect":
            pending = self._pending
            self._pending = ""
            self._state = "passthrough"
            return pending
        return ""
