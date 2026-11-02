"""Pure-ASGI middleware: request IDs, access logs, security headers, limits."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.request")


class BodyTooLarge(Exception):
    pass


class RequestContextMiddleware:
    """Attach a request id, enforce a body limit, and emit access logs.

    Implemented as pure ASGI (rather than ``BaseHTTPMiddleware``) so streaming
    responses are never buffered.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        body_limit: int = 64 * 1024,
        stats: Any | None = None,
    ) -> None:
        self.app = app
        self.body_limit = body_limit
        self.stats = stats

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self.stats is not None:
            self.stats.requests_total += 1

        request_id = uuid.uuid4().hex[:16]
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        started = time.perf_counter()
        status_code = 500
        response_started = False

        # Reject oversized bodies before FastAPI reads them. The receive-side
        # guard below also covers chunked uploads, but frameworks often turn
        # that error into a generic 400, so the header check is the fast path.
        declared_length = Headers(scope=scope).get("content-length")
        if declared_length:
            try:
                too_large = int(declared_length) > self.body_limit
            except ValueError:
                too_large = False
            if too_large:
                body = json.dumps(
                    {
                        "error": {
                            "code": "payload_too_large",
                            "message": f"Request body must be under {self.body_limit} bytes.",
                            "request_id": request_id,
                        }
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"x-request-id", request_id.encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                logger.info(
                    "request method=%s path=%s status=413 duration_ms=%.1f request_id=%s",
                    scope.get("method", "?"),
                    scope.get("path", "?"),
                    (time.perf_counter() - started) * 1000,
                    request_id,
                )
                return

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Request-ID", request_id)
            await send(message)

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.body_limit:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except BodyTooLarge:
            if response_started:
                raise
            body = json.dumps(
                {
                    "error": {
                        "code": "payload_too_large",
                        "message": f"Request body must be under {self.body_limit} bytes.",
                        "request_id": request_id,
                    }
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"x-request-id", request_id.encode()),
                        (b"connection", b"close"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            status_code = 413
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "request method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
                scope.get("method", "?"),
                scope.get("path", "?"),
                status_code,
                duration_ms,
                request_id,
            )


_APP_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# FastAPI's generated Swagger UI uses the jsDelivr CDN and an inline bootstrap
# script. Keep the strict policy for the chat app and relax it only on /docs.
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    (
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    ),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_https = scope.get("scheme") == "https"
        if not is_https:
            forwarded = ""
            for key, value in scope.get("headers", []):
                if key == b"x-forwarded-proto":
                    forwarded = value.decode("latin-1").split(",")[0].strip()
                    break
            is_https = forwarded == "https"

        path = scope.get("path", "")
        csp = _DOCS_CSP if path == "/docs" or path.startswith("/docs/") else _APP_CSP

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS:
                    headers.setdefault(name, value)
                headers.setdefault("Content-Security-Policy", csp)
                if is_https:
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
                # Do not advertise the server implementation.
                if "server" in headers:
                    del headers["server"]
            await send(message)

        await self.app(scope, receive, send_wrapper)


class StaticCacheMiddleware:
    """Give versioned static assets a long-lived immutable cache policy."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
            await send(message)

        await self.app(scope, receive, send_wrapper)
