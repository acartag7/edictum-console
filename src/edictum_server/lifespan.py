"""Application layer -- FastAPI lifespan (startup/shutdown) management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI

from edictum_server.config import get_settings
from edictum_server.db.engine import async_session_factory, init_engine
from edictum_server.notifications.base import NotificationManager
from edictum_server.notifications.loader import load_db_channels
from edictum_server.push.manager import PushManager
from edictum_server.redis.client import create_redis_client
from edictum_server.services.bootstrap_service import (
    bootstrap_admin,
    cleanup_ai_usage,
    ensure_signing_keys,
)
from edictum_server.workers import (
    _approval_timeout_worker,
    _partition_worker,
    _worker_monitor,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle hook."""
    settings = get_settings()
    settings.validate_required()

    # Warn if base_url is not HTTPS in non-local environments
    parsed = urlparse(settings.base_url)
    if not settings.base_url.startswith("https://") and parsed.hostname not in (
        "localhost",
        "127.0.0.1",
    ):
        logger.warning(
            "EDICTUM_BASE_URL is not HTTPS (%s) — session cookies will not have "
            "the Secure flag. Set EDICTUM_BASE_URL to your public HTTPS URL in production.",
            settings.base_url,
        )

    # Warn when serving behind a proxy without trusted proxy config (H1/L1).
    # Without ProxyHeadersMiddleware, Starlette uses http:// in redirects
    # and rate-limiting keys on the proxy IP instead of the real client.
    if settings.base_url.startswith("https://") and not settings.trusted_proxies:
        logger.warning(
            "EDICTUM_BASE_URL is HTTPS but EDICTUM_TRUSTED_PROXIES is not set. "
            "Trailing-slash redirects will use http:// (downgrade) and rate "
            "limiting will key on the proxy IP, not the real client. "
            "Set EDICTUM_TRUSTED_PROXIES to your reverse proxy addresses "
            "(e.g. '*' for Railway/Render, or specific CIDRs).",
        )

    # Validate signing key secret early -- log clearly if misconfigured
    try:
        settings.get_signing_secret()
    except ValueError as exc:
        logger.warning(
            "EDICTUM_SIGNING_KEY_SECRET not set — bundle signing and "
            "notification encryption disabled: %s",
            exc,
        )

    # Database
    engine = init_engine(settings.database_url)

    # Redis
    app.state.redis = create_redis_client(settings.redis_url)

    # Auth provider
    from edictum_server.auth.local import LocalAuthProvider

    app.state.auth_provider = LocalAuthProvider(
        redis=app.state.redis,
        session_ttl_hours=settings.session_ttl_hours,
        secure_cookies=settings.base_url.startswith("https://"),
        secret_key=settings.secret_key,
    )

    # Push manager (SSE)
    app.state.push_manager = PushManager()

    # Notification manager (tenant-keyed, all channels from DB)
    notification_mgr = NotificationManager()
    app.state.notification_manager = notification_mgr

    # Load DB-configured notification channels and register Telegram webhooks
    try:
        signing_secret: bytes | None = None
        with contextlib.suppress(ValueError):
            signing_secret = settings.get_signing_secret()
        async with async_session_factory()() as db:
            channels_by_tenant = await load_db_channels(
                db,
                app.state.redis,
                settings.base_url,
                secret=signing_secret,
            )
            await notification_mgr.reload(channels_by_tenant)
            total = sum(len(chs) for chs in channels_by_tenant.values())
            logger.info("Loaded %d notification channel(s) from DB", total)
    except Exception:
        logger.exception("Failed to load notification channels from DB")

    # Bootstrap admin on first run
    await bootstrap_admin()

    # Ensure every tenant has an active signing key
    await ensure_signing_keys(settings)

    # Clean up old AI usage logs
    await cleanup_ai_usage()

    # Background workers
    timeout_task = asyncio.create_task(_approval_timeout_worker(app))
    partition_task = asyncio.create_task(_partition_worker())
    app.state.push_manager.start_cleanup_task()

    # Expose workers for health monitoring
    app.state.background_workers = {
        "approval_timeout": timeout_task,
        "partition": partition_task,
    }

    # Auto-restart monitor for crashed workers
    monitor_task = asyncio.create_task(_worker_monitor(app))

    yield

    # Shutdown
    monitor_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await monitor_task
    app.state.push_manager.stop_cleanup_task()
    workers = app.state.background_workers
    workers["approval_timeout"].cancel()
    workers["partition"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await workers["approval_timeout"]
    with contextlib.suppress(asyncio.CancelledError):
        await workers["partition"]
    for ch in notification_mgr.channels:
        try:
            await ch.close()
        except Exception:
            logger.exception("Error closing notification channel %s", ch.name)
    await app.state.redis.aclose()
    await engine.dispose()
