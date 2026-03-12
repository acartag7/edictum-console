"""FastAPI application factory -- app creation, middleware, routing, and SPA serving."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from edictum_server.config import get_settings
from edictum_server.lifespan import lifespan
from edictum_server.routes import (
    agent_registrations,
    agents,
    ai,
    ai_usage,
    approvals,
    assignment_rules,
    auth,
    bundles,
    compositions,
    contracts,
    deployments,
    discord,
    evaluate,
    events,
    health,
    keys,
    notifications,
    sessions,
    settings,
    setup,
    slack,
    stats,
    stream,
    telegram,
)

logger = logging.getLogger(__name__)

_settings = get_settings()
_is_production = _settings.env_name == "production"
app = FastAPI(
    title="Edictum Console",
    description="Self-hostable agent operations console -- runtime governance for AI agents",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With", "Authorization", "X-Edictum-Agent-Id"],
)

# Security response headers (HSTS, CSP, X-Frame-Options, etc.)
from edictum_server.security.headers import SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)

# CSRF protection — must be added after CORS so it runs on the inner request.
# Requires X-Requested-With header on cookie-auth mutating requests.
from edictum_server.auth.csrf import CSRFMiddleware  # noqa: E402

app.add_middleware(CSRFMiddleware)

# Trusted proxy support — properly resolve client IPs behind reverse proxies
if _settings.trusted_proxies:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # noqa: E402

    _trusted_hosts = [h.strip() for h in _settings.trusted_proxies.split(",") if h.strip()]
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_hosts)

# Request body size limits — outermost middleware (added last = runs first).
# Rejects oversized bodies with 413 before any parsing or auth (issue #16).
from edictum_server.security.body_limit import BodySizeLimitMiddleware  # noqa: E402

app.add_middleware(BodySizeLimitMiddleware)

# Routers
app.include_router(health.router)
app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(keys.router)
app.include_router(bundles.router)
app.include_router(compositions.router)
app.include_router(contracts.router)
app.include_router(evaluate.router)
app.include_router(deployments.router)
app.include_router(stream.router)
app.include_router(events.router)
app.include_router(sessions.router)
app.include_router(approvals.router)
app.include_router(stats.router)
app.include_router(telegram.router)
app.include_router(discord.router)
app.include_router(slack.router)
app.include_router(agents.router)
app.include_router(agent_registrations.router)
app.include_router(assignment_rules.router)
app.include_router(notifications.router)
app.include_router(settings.router)
app.include_router(ai.router)
app.include_router(ai_usage.router)


# --- Validation error handler: strip Pydantic internals ----------------------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 422 with sanitized error details.

    Strips ``ctx`` and ``type`` fields from Pydantic errors to avoid
    leaking framework internals (L4 finding).
    """
    sanitized = [
        {"loc": e.get("loc", []), "msg": e.get("msg", "Validation error")} for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": sanitized})


# --- 404 handler: redirect non-API paths to dashboard -------------------------
@app.exception_handler(404)
async def not_found_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse | RedirectResponse:
    """API routes get JSON 404; everything else redirects to the dashboard."""
    if request.url.path.startswith("/api/"):
        detail = exc.detail if exc.detail else "Not Found"
        return JSONResponse({"detail": detail}, status_code=404)
    return RedirectResponse(url="/dashboard", status_code=302)


# --- SPA serving (dashboard) ---------------------------------------------------
_STATIC_DIR = Path(os.environ.get("EDICTUM_STATIC_DIR", "/app/static/dashboard"))

# Mount static assets BEFORE the SPA catch-all so /dashboard/assets/* serves
# real files instead of falling through to index.html.
_ASSETS_DIR = _STATIC_DIR / "assets"
if _ASSETS_DIR.is_dir():
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=str(_ASSETS_DIR)),
        name="dashboard-assets",
    )


@app.get("/dashboard/{full_path:path}", response_model=None)
async def serve_spa(request: Request, full_path: str) -> FileResponse | HTMLResponse:  # noqa: ARG001
    """Serve the React SPA — return index.html for all routes (client-side routing)."""
    index = _STATIC_DIR.resolve() / "index.html"
    if index.is_file():
        return FileResponse(
            index,
            # Never cache index.html at CDN layer — it references content-hashed
            # assets that change on every build.  Without this, CDNs (Railway's
            # Fastly edge, Cloudflare) cache stale HTML for hours.
            headers={"Cache-Control": "no-cache"},
        )
    return HTMLResponse(
        "<h1>Dashboard not built</h1>"
        "<p>Run <code>cd dashboard && pnpm build</code> or use the Vite dev server.</p>",
        status_code=404,
    )
