"""Jarvis AI — production FastAPI application."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.cache import ResponseCache, cache_key
from app.config import BASE_DIR, Settings
from app.llm import LLMClient, LLMError, StreamEvent
from app.middleware import (
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    StaticCacheMiddleware,
)
from app.prompt import build_messages, estimate_tokens
from app.ratelimit import SlidingWindowLimiter
from app.schemas import ChatRequest
from app.sse import HEARTBEAT, event
from app.stats import Stats
from app.text import LeadingThinkFilter

logger = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "static"
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"
HEARTBEAT_SECONDS = 12.0
_GZIP_TYPES = [
    "text/html",
    "text/css",
    "application/javascript",
    "application/json",
    "image/svg+xml",
]


def _asset_version() -> str:
    digest = hashlib.sha256()
    files = [
        STATIC_DIR / "styles.css",
        STATIC_DIR / "app.js",
        STATIC_DIR / "favicon.svg",
        STATIC_DIR / "manifest.webmanifest",
        STATIC_DIR / "vendor" / "marked.min.js",
        STATIC_DIR / "vendor" / "purify.min.js",
        STATIC_DIR / "vendor" / "highlight.min.js",
        STATIC_DIR / "sw.js",
    ]
    for path in files:
        if path.exists():
            digest.update(path.read_bytes())
    digest.update(b"2")
    return digest.hexdigest()[:12]


def _render_index(settings: Settings, asset_version: str) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "__APP_NAME__": html.escape(settings.app_name),
        "__APP_INITIAL__": html.escape((settings.app_name.strip()[:1] or "A").upper()),
        "__APP_VERSION__": html.escape(settings.app_version),
        "__ASSET_VERSION__": asset_version,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _chunks(text: str, size: int = 96) -> list[str]:
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            window = text.rfind(" ", start + size // 2, end)
            newline = text.rfind("\n", start + size // 2, end)
            split = max(window, newline)
            if split > start:
                end = split + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def create_app(
    settings: Settings | None = None,
    *,
    llm_client: LLMClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, settings.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    asset_version = _asset_version()
    index_html = _render_index(settings, asset_version)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "startup name=%s version=%s env=%s ready=%s cache=%s rate_limit=%s/%ss",
            settings.app_name,
            settings.app_version,
            settings.environment,
            settings.is_ready,
            settings.cache_enabled,
            settings.rate_limit_requests,
            settings.rate_limit_window_seconds,
        )
        yield
        await app.state.llm.aclose()
        logger.info("shutdown")

    docs_url = "/docs" if settings.docs_enabled else None
    openapi_url = "/openapi.json" if settings.docs_enabled else None
    app = FastAPI(
        title=settings.app_name,
        description=(
            "A fast, streaming AI assistant. The public UI lives at `/`; "
            "the API streams Server-Sent Events from `POST /api/chat`."
        ),
        version=settings.app_version,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        contact={"name": settings.app_name},
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.index_html = index_html
    app.state.asset_version = asset_version
    app.state.llm = llm_client or LLMClient(settings)
    app.state.cache = ResponseCache(
        enabled=settings.cache_enabled,
        ttl_seconds=settings.cache_ttl_seconds,
        max_entries=settings.cache_max_entries,
        max_chars=settings.cache_max_chars,
    )
    app.state.limiter = SlidingWindowLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
        max_clients=settings.rate_limit_max_clients,
    )
    app.state.semaphore = asyncio.Semaphore(settings.max_concurrent_streams)
    app.state.stats = Stats.create()
    app.state.ip_salt = secrets.token_hex(16)

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
            max_age=600,
        )
    app.add_middleware(StaticCacheMiddleware)
    app.add_middleware(
        RequestContextMiddleware,
        body_limit=settings.request_body_limit,
        stats=app.state.stats,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        GZipMiddleware,
        minimum_size=1024,
        compresslevel=6,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            fields.append(
                {
                    "field": location or "body",
                    "message": error.get("msg", "Invalid value"),
                }
            )
        first = fields[0] if fields else {"field": "body", "message": "Invalid request."}
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": f"{first['field']}: {first['message']}",
                    "fields": fields,
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            413: "payload_too_large",
            422: "invalid_request",
            429: "rate_limited",
            503: "service_unavailable",
            504: "upstream_timeout",
        }
        headers = dict(exc.headers or {})
        headers.setdefault("Cache-Control", "no-store")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": codes.get(exc.status_code, "error"),
                    "message": str(exc.detail),
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "-")
        logger.exception("unhandled_error request_id=%s path=%s", request_id, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Something went wrong. Please try again.",
                    "request_id": request_id,
                }
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home() -> HTMLResponse:
        return HTMLResponse(
            app.state.index_html,
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> PlainTextResponse:
        source = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
        return PlainTextResponse(
            source.replace("__ASSET_VERSION__", app.state.asset_version),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/",
            },
        )

    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nAllow: /\n")

    @app.get("/healthz", tags=["system"], summary="Liveness probe")
    async def healthz() -> JSONResponse:
        stats = app.state.stats.snapshot()
        return JSONResponse(
            {
                "status": "ok",
                "version": settings.app_version,
                "uptime_seconds": stats["uptime_seconds"],
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/readyz", tags=["system"], summary="Readiness probe")
    async def readyz() -> JSONResponse:
        if settings.is_ready:
            return JSONResponse({"status": "ready"}, headers={"Cache-Control": "no-store"})
        reason = (
            "missing_model"
            if not settings.fast_model
            else "missing_api_key"
        )
        return JSONResponse(
            {"status": "unavailable", "reason": reason},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/config", tags=["system"], summary="Public client configuration")
    async def config() -> JSONResponse:
        return JSONResponse(
            {
                "app_name": settings.app_name,
                "version": settings.app_version,
                "ready": settings.is_ready,
                "docs_enabled": settings.docs_enabled,
                "max_message_chars": settings.max_message_chars,
                "history_limit": settings.max_history_messages,
                "modes": [
                    {
                        "id": "fast",
                        "label": "Fast",
                        "description": "Quick answers for everyday questions.",
                    },
                    {
                        "id": "smart",
                        "label": "Deep",
                        "description": "More careful reasoning for complex problems.",
                    },
                ],
                "rate_limit": {
                    "requests": settings.rate_limit_requests,
                    "window_seconds": settings.rate_limit_window_seconds,
                },
                "privacy": "Messages are processed in memory and are not stored on the server.",
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/stats", tags=["system"], summary="Runtime counters")
    async def stats_endpoint() -> JSONResponse:
        if not settings.stats_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        snapshot = app.state.stats.snapshot()
        snapshot["cache"] = {
            "hits": app.state.cache.hits,
            "misses": app.state.cache.misses,
            "entries": len(app.state.cache._entries),
            "enabled": app.state.cache.enabled,
        }
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    @app.post(
        "/api/chat",
        tags=["chat"],
        summary="Stream a chat completion as Server-Sent Events",
        response_description="SSE stream with meta, delta, status, done, and error events.",
    )
    async def chat(request: Request, payload: ChatRequest) -> StreamingResponse:
        if not settings.is_ready:
            raise HTTPException(
                status_code=503,
                detail="Chat is not configured on this server yet.",
            )
        if len(payload.message) > settings.max_message_chars:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Message is too long. The limit is "
                    f"{settings.max_message_chars:,} characters."
                ),
            )

        stats = app.state.stats
        stats.chat_requests += 1

        client_ip = _client_ip(request, settings)
        client_key = hashlib.sha256(
            (app.state.ip_salt + client_ip).encode("utf-8")
        ).hexdigest()
        allowed, remaining, retry_after = await app.state.limiter.check(client_key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment and try again.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(settings.rate_limit_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window": str(settings.rate_limit_window_seconds),
                },
            )

        messages = build_messages(payload, settings)
        cacheable = (
            settings.cache_enabled
            and not payload.history
            and len(payload.message) <= 500
            and payload.mode == "fast"
        )
        key = cache_key(payload.mode, payload.message, messages[1:-1])
        cached = await app.state.cache.get(key) if cacheable else None

        rate_headers = {
            "X-RateLimit-Limit": str(settings.rate_limit_requests),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Window": str(settings.rate_limit_window_seconds),
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        }

        request_id = getattr(request.state, "request_id", "-")

        if cached is not None:
            stats.cache_hits += 1
            logger.info(
                "chat_cache_hit request_id=%s mode=%s chars=%s",
                request_id,
                payload.mode,
                len(cached.text),
            )

            async def cached_stream() -> AsyncIterator[str]:
                yield event(
                    "meta",
                    {
                        "request_id": request_id,
                        "model": cached.model,
                        "mode": payload.mode,
                        "cached": True,
                    },
                )
                for chunk in _chunks(cached.text):
                    yield event("delta", {"text": chunk})
                    await asyncio.sleep(0)
                yield event(
                    "done",
                    {
                        "request_id": request_id,
                        "finish_reason": cached.finish_reason,
                        "cached": True,
                        "elapsed_ms": 0,
                    },
                )

            return StreamingResponse(
                cached_stream(),
                media_type="text/event-stream",
                headers={**rate_headers, "X-Cache": "HIT"},
            )

        try:
            await asyncio.wait_for(
                app.state.semaphore.acquire(),
                timeout=settings.concurrency_wait_seconds,
            )
        except TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail="The assistant is at capacity right now. Please retry shortly.",
                headers={"Retry-After": "3"},
            ) from exc

        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                app.state.semaphore.release()
                stats.active_streams = max(0, stats.active_streams - 1)

        stats.active_streams += 1

        async def stream_reply() -> AsyncIterator[str]:
            started = time.perf_counter()
            first_token_at: float | None = None
            model = settings.model_for(payload.mode)
            finish_reason = "stop"
            usage: dict | None = None
            think = LeadingThinkFilter()
            pieces: list[str] = []
            reasoning_announced = False

            queue: asyncio.Queue[StreamEvent | BaseException | None] = asyncio.Queue(
                maxsize=128
            )

            async def pump() -> None:
                try:
                    async for stream_event in app.state.llm.stream_chat(
                        messages, payload.mode
                    ):
                        await queue.put(stream_event)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001 - forwarded to consumer
                    try:
                        queue.put_nowait(exc)
                    except asyncio.QueueFull:  # pragma: no cover
                        logger.error("llm_queue_full request_id=%s", request_id)
                else:
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:  # pragma: no cover
                        logger.error("llm_queue_full request_id=%s", request_id)

            task = asyncio.create_task(pump(), name=f"llm-{request_id}")

            try:
                yield event(
                    "meta",
                    {
                        "request_id": request_id,
                        "model": model,
                        "mode": payload.mode,
                        "cached": False,
                    },
                )

                while True:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=HEARTBEAT_SECONDS
                        )
                    except TimeoutError:
                        yield HEARTBEAT
                        continue

                    if item is None:
                        break
                    if isinstance(item, BaseException):
                        raise item

                    if item.type == "start":
                        if item.model and item.model != model:
                            model = item.model
                            yield event(
                                "meta",
                                {
                                    "request_id": request_id,
                                    "model": model,
                                    "mode": payload.mode,
                                    "cached": False,
                                    "fallback": True,
                                },
                            )
                        continue

                    if item.type == "reasoning":
                        if not reasoning_announced:
                            reasoning_announced = True
                            yield event("status", {"state": "reasoning"})
                        continue

                    if item.type == "token":
                        visible = think.feed(item.text)
                        if visible:
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            pieces.append(visible)
                            yield event("delta", {"text": visible})
                        continue

                    if item.type == "done":
                        finish_reason = item.finish_reason or "stop"
                        usage = item.usage
                        continue

                tail = think.flush()
                if tail:
                    pieces.append(tail)
                    yield event("delta", {"text": tail})

                text = "".join(pieces).strip()
                if not text:
                    raise LLMError(
                        "The model returned an empty response. Please try again.",
                        status_code=502,
                        retryable=True,
                    )

                total_ms = round((time.perf_counter() - started) * 1000, 1)
                ttft_ms = (
                    round((first_token_at - started) * 1000, 1)
                    if first_token_at is not None
                    else None
                )
                stats.tokens_out += estimate_tokens(text)
                if cacheable:
                    await app.state.cache.set(
                        key,
                        text=text,
                        model=model,
                        finish_reason=finish_reason,
                    )
                logger.info(
                    "chat_complete request_id=%s mode=%s model=%s chars=%s "
                    "ttft_ms=%s total_ms=%s finish=%s",
                    request_id,
                    payload.mode,
                    model,
                    len(text),
                    ttft_ms,
                    total_ms,
                    finish_reason,
                )
                yield event(
                    "done",
                    {
                        "request_id": request_id,
                        "model": model,
                        "finish_reason": finish_reason,
                        "ttft_ms": ttft_ms,
                        "elapsed_ms": total_ms,
                        "usage": usage,
                        "cached": False,
                    },
                )
            except LLMError as exc:
                stats.upstream_errors += 1
                logger.warning(
                    "chat_upstream_error request_id=%s mode=%s status=%s detail=%s",
                    request_id,
                    payload.mode,
                    exc.status_code,
                    exc.detail,
                )
                yield event(
                    "error",
                    {
                        "request_id": request_id,
                        "code": "upstream_unavailable",
                        "message": exc.public_message,
                    },
                )
            except asyncio.CancelledError:
                logger.info("chat_cancelled request_id=%s", request_id)
                raise
            except Exception:  # pragma: no cover - defensive
                stats.upstream_errors += 1
                logger.exception("chat_failed request_id=%s", request_id)
                yield event(
                    "error",
                    {
                        "request_id": request_id,
                        "code": "internal_error",
                        "message": "Something went wrong while generating a response.",
                    },
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                release()

        return StreamingResponse(
            stream_reply(),
            media_type="text/event-stream",
            headers=rate_headers,
            background=BackgroundTask(release),
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
