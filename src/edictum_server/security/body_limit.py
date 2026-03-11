"""Request body size limit middleware.

Prevents DoS via oversized request bodies (issue #16). Enforces
route-specific limits by checking Content-Length before reading and
counting bytes during streaming to catch spoofed/missing headers.

Returns 413 Request Entity Too Large with a JSON error body when exceeded.
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Limits in bytes
_1_KB = 1_024
_1_MB = 1_048_576
_5_MB = 5_242_880

# Route-specific limits: (prefix, method_filter, limit_bytes)
# Checked in order — first match wins. More specific prefixes must
# come before general ones (e.g. /bundles/evaluate before /bundles).
_ROUTE_LIMITS: list[tuple[str, str | None, int]] = [
    # Auth endpoints — login payloads are tiny (email + password)
    ("/api/v1/auth/", None, _1_KB),
    # Setup endpoint — bootstrap wizard payload is tiny
    ("/api/v1/setup", None, _1_KB),
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
    human = f"{limit_bytes // _1_MB}MB" if limit_bytes >= _1_MB else f"{limit_bytes // _1_KB}KB"
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
                response = _make_413_response(limit)
                await response(scope, receive, send)
                return

        # Phase 2: Wrap receive to enforce streaming byte limit.
        # This catches spoofed Content-Length or chunked transfer encoding.
        bytes_received = 0

        async def limited_receive() -> Message:
            nonlocal bytes_received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)
                if bytes_received > limit:
                    raise _BodyTooLargeError(limit)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError as exc:
            response = _make_413_response(exc.limit_bytes)
            await response(scope, receive, send)
