"""Request body size limit middleware.

Prevents DoS via oversized request bodies (issue #16). Enforces
route-specific limits by checking Content-Length before reading and
counting bytes during streaming to catch spoofed/missing headers.

Returns 413 Request Entity Too Large with a JSON error body when exceeded.
"""

from __future__ import annotations

import json

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger(__name__)

# Limits in bytes
_4_KB = 4_096
_1_MB = 1_048_576
_5_MB = 5_242_880

# Route-specific limits: (prefix, method_filter, limit_bytes)
# Checked in order — first match wins. More specific prefixes must
# come before general ones (e.g. /bundles/evaluate before /bundles).
_ROUTE_LIMITS: list[tuple[str, str | None, int]] = [
    # Auth endpoints — password field allows up to 1024 chars, JSON overhead
    # pushes legitimate long-passphrase requests past 1KB. 4KB is generous.
    ("/api/v1/auth/", None, _4_KB),
    # Setup endpoint — same schema constraints as auth
    ("/api/v1/setup", None, _4_KB),
    # Evaluate endpoint — playground sends YAML + args (before /bundles)
    ("/api/v1/bundles/evaluate", "POST", _5_MB),
    # Bundle upload — contracts can be large but bounded
    ("/api/v1/bundles", "POST", _5_MB),
    # Contract creation/update — YAML content can be sizeable
    ("/api/v1/contracts", "POST", _5_MB),
    ("/api/v1/contracts/", "PUT", _5_MB),
    # Composition creation/update — may reference multiple contracts
    ("/api/v1/compositions", "POST", _5_MB),
    ("/api/v1/compositions/", "PUT", _5_MB),
]

_DEFAULT_LIMIT = _1_MB


def _get_limit_for_request(path: str, method: str) -> int:
    """Return the body size limit in bytes for a given path and method."""
    for prefix, method_filter, limit in _ROUTE_LIMITS:
        if path.startswith(prefix) and (method_filter is None or method == method_filter):
            return limit
    return _DEFAULT_LIMIT


def _make_413_response(limit_bytes: int) -> Response:
    """Build a 413 JSON response with a human-readable size description."""
    human = f"{limit_bytes // _1_MB}MB" if limit_bytes >= _1_MB else f"{limit_bytes // 1024}KB"
    return JSONResponse(
        {"detail": f"Request body too large. Maximum allowed: {human}."},
        status_code=413,
    )


class _BodyTooLargeError(Exception):
    """Internal signal raised when streaming body exceeds the limit."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        super().__init__()


class BodySizeLimitMiddleware:
    """ASGI middleware enforcing request body size limits.

    Two-phase enforcement:
    1. Content-Length header check — rejects immediately before any I/O.
    2. Streaming byte counter — wraps the ASGI ``receive`` callable to
       count bytes as they arrive, catching spoofed/missing Content-Length
       and chunked transfer encoding.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        method = request.method

        # GET, HEAD, OPTIONS typically have no body — skip
        if method in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return

        path = request.url.path

        # Only enforce on API routes
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        limit = _get_limit_for_request(path, method)

        # Phase 1: Check Content-Length header if present
        content_length_str = request.headers.get("content-length")
        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
            except (ValueError, OverflowError):
                content_length = 0
            if content_length > limit:
                logger.warning(
                    "body_size_rejected_header",
                    path=path,
                    method=method,
                    content_length=content_length,
                    limit_bytes=limit,
                )
                response = _make_413_response(limit)
                await response(scope, receive, send)
                return

        # Phase 2: Wrap receive to enforce streaming byte limit.
        # This catches spoofed Content-Length or chunked transfer encoding.
        #
        # When the limit is exceeded, _BodyTooLargeError is raised inside
        # limited_receive(). If it propagates to us, we send 413 directly.
        # If the app catches the error internally (e.g. FastAPI returns 400),
        # we intercept send() and replace the app's response with 413.
        bytes_received = 0
        limit_exceeded = False
        response_replaced = False

        async def limited_receive() -> Message:
            nonlocal bytes_received, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)
                if bytes_received > limit:
                    limit_exceeded = True
                    logger.warning(
                        "body_size_rejected_streaming",
                        path=path,
                        method=method,
                        bytes_received=bytes_received,
                        limit_bytes=limit,
                    )
                    raise _BodyTooLargeError(limit)
            return message

        async def intercepting_send(message: Message) -> None:
            nonlocal response_replaced
            if limit_exceeded:
                if not response_replaced and message["type"] == "http.response.start":
                    # Replace the app's response with our 413
                    response_replaced = True
                    human = f"{limit // _1_MB}MB" if limit >= _1_MB else f"{limit // 1024}KB"
                    body_bytes = json.dumps(
                        {"detail": f"Request body too large. Maximum allowed: {human}."}
                    ).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [
                                [b"content-type", b"application/json"],
                                [b"content-length", str(len(body_bytes)).encode()],
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body_bytes})
                # Suppress ALL messages from the app when limit is exceeded
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, intercepting_send)
        except _BodyTooLargeError as exc:
            if not response_replaced:
                response = _make_413_response(exc.limit_bytes)
                await response(scope, receive, send)
