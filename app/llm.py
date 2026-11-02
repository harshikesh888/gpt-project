"""Async OpenAI-compatible client (Ollama by default) with streaming and fallback."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamEvent:
    type: Literal["start", "token", "reasoning", "done"]
    text: str = ""
    model: str = ""
    finish_reason: str = "stop"
    usage: dict[str, Any] | None = field(default=None)


class LLMError(Exception):
    """An upstream failure with a message that is safe to show to users."""

    def __init__(
        self,
        public_message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
        detail: str = "",
    ) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code
        self.retryable = retryable
        self.detail = detail


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._url = f"{settings.llm_base_url}/chat/completions"
        timeout = httpx.Timeout(
            connect=settings.connect_timeout,
            read=settings.read_timeout,
            write=settings.write_timeout,
            pool=settings.pool_timeout,
        )
        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": f"jarvis/{settings.app_version}",
        }
        # Ollama ignores auth headers; only send one when a key is configured.
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        # A shared client enables connection reuse (TLS handshakes are the
        # dominant cost on the first token) across all requests.
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers=headers,
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def candidates(self, mode: str) -> list[str]:
        primary = self.settings.model_for(mode)
        alternate = (
            self.settings.fast_model if mode == "smart" else self.settings.deep_model
        )
        ordered: list[str] = []
        for model in [primary, self.settings.fallback_model, alternate]:
            if model and model not in ordered:
                ordered.append(model)
        return ordered

    def _payload(self, model: str, messages: list[dict[str, str]], mode: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.settings.temperature_for(mode),
            "max_tokens": self.settings.max_tokens_for(mode),
            "top_p": 0.9,
            "stream": True,
        }
        if self.settings.provider:
            payload["provider"] = self.settings.provider
        return payload

    @staticmethod
    def _error_for_status(status: int, body: str, model: str) -> LLMError:
        detail = f"model={model} status={status} body={body[:600]}"
        if status in (401, 403):
            return LLMError(
                "The AI service is not configured correctly. Please try again later.",
                status_code=503,
                retryable=False,
                detail=detail,
            )
        if status == 429:
            return LLMError(
                "The AI service is busy right now. Please try again in a moment.",
                status_code=503,
                retryable=True,
                detail=detail,
            )
        if status in (408, 504):
            return LLMError(
                "The AI service took too long to respond. Please try again.",
                status_code=504,
                retryable=True,
                detail=detail,
            )
        if status == 404:
            return LLMError(
                "The configured AI model is not available. Please try again later.",
                status_code=503,
                retryable=True,
                detail=detail,
            )
        return LLMError(
            "The AI service is temporarily unavailable. Please try again.",
            status_code=502,
            retryable=True,
            detail=detail,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        mode: str,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion, falling back to another model before first token."""
        candidates = self.candidates(mode)
        last_error: LLMError | None = None

        for index, model in enumerate(candidates):
            started = False
            try:
                async for event in self._stream_once(model, messages, mode):
                    # A model can emit its start frame and then fail before any
                    # visible token. Keep fallback available until content has
                    # actually reached the user.
                    if event.type == "token":
                        started = True
                    yield event
                return
            except LLMError as exc:
                last_error = exc
                logger.warning(
                    "llm_attempt_failed model=%s attempt=%s retryable=%s detail=%s",
                    model,
                    index + 1,
                    exc.retryable,
                    exc.detail,
                )
                if started or not exc.retryable or index == len(candidates) - 1:
                    raise
            except httpx.HTTPError as exc:
                logger.warning("llm_transport_error model=%s error=%s", model, exc)
                last_error = LLMError(
                    "The AI service is unreachable. Please try again.",
                    status_code=502,
                    retryable=True,
                    detail=str(exc),
                )
                if started or index == len(candidates) - 1:
                    raise last_error from exc
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("llm_unexpected_error model=%s", model)
                raise LLMError(
                    "Something went wrong while generating a response.",
                    status_code=500,
                    retryable=False,
                    detail=str(exc),
                ) from exc

        raise last_error or LLMError("No model is available.", status_code=503)

    async def _stream_once(
        self,
        model: str,
        messages: list[dict[str, str]],
        mode: str,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._payload(model, messages, mode)
        try:
            async with self._client.stream("POST", self._url, json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._error_for_status(response.status_code, body, model)

                yield StreamEvent(type="start", model=model)

                buffer = ""
                finish_reason = "stop"
                usage: dict[str, Any] | None = None

                async for chunk in response.aiter_text():
                    buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
                    while "\n\n" in buffer:
                        raw_event, buffer = buffer.split("\n\n", 1)
                        data_lines = [
                            line[5:].lstrip()
                            for line in raw_event.split("\n")
                            if line.startswith("data:")
                        ]
                        if not data_lines:
                            continue
                        data = "\n".join(data_lines).strip()
                        if data == "[DONE]":
                            yield StreamEvent(
                                type="done",
                                model=model,
                                finish_reason=finish_reason,
                                usage=usage,
                            )
                            return

                        try:
                            parsed = json.loads(data)
                        except json.JSONDecodeError:
                            logger.debug("llm_bad_json chunk=%r", data[:200])
                            continue

                        if isinstance(parsed.get("error"), dict):
                            message = str(parsed["error"].get("message", "upstream error"))
                            raise LLMError(
                                "The AI service returned an error. Please try again.",
                                status_code=502,
                                retryable=True,
                                detail=f"model={model} error={message[:400]}",
                            )

                        usage = parsed.get("usage") or usage
                        choice = (parsed.get("choices") or [{}])[0] or {}
                        delta = choice.get("delta") or {}
                        finish_reason = choice.get("finish_reason") or finish_reason

                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        reasoning_text = _content_text(reasoning)
                        if reasoning_text:
                            yield StreamEvent(type="reasoning", text=reasoning_text)

                        content = _content_text(delta.get("content"))
                        if content:
                            yield StreamEvent(type="token", text=content)

                # Process a trailing event that may not end with a blank line.
                if buffer.strip():
                    for line in buffer.split("\n"):
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                parsed = json.loads(data)
                                usage = parsed.get("usage") or usage
                                choice = (parsed.get("choices") or [{}])[0] or {}
                                delta = choice.get("delta") or {}
                                finish_reason = choice.get("finish_reason") or finish_reason
                                content = _content_text(delta.get("content"))
                                if content:
                                    yield StreamEvent(type="token", text=content)
                            except json.JSONDecodeError:
                                continue

                yield StreamEvent(
                    type="done",
                    model=model,
                    finish_reason=finish_reason,
                    usage=usage,
                )
        except httpx.TimeoutException as exc:
            raise LLMError(
                "The AI service took too long to respond. Please try again.",
                status_code=504,
                retryable=True,
                detail=f"model={model} timeout={exc}",
            ) from exc
