"""Infrastructure layer -- background worker tasks.

Contains the partition maintenance worker, approval timeout worker,
and the monitor that restarts crashed workers.
"""

from __future__ import annotations

import asyncio
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import FastAPI

from edictum_server.db.engine import async_session_factory, get_engine
from edictum_server.notifications.base import NotificationManager
from edictum_server.push.manager import PushManager
from edictum_server.services.approval_service import expire_approvals

logger = structlog.get_logger(__name__)

_PARTITION_INTERVAL = 24 * 60 * 60  # 24 hours


async def _partition_worker() -> None:
    """Ensure event partitions exist for the next 3 months, once per day."""
    structlog.contextvars.bind_contextvars(worker="partition")
    while True:
        try:
            engine = get_engine()
            if engine.dialect.name != "postgresql":
                return  # no-op for SQLite (tests)
            async with async_session_factory()() as db:
                await db.execute(sa.text("SELECT ensure_event_partitions(3)"))
                await db.commit()
                logger.info("Ensured event partitions for next 3 months")
        except Exception:
            logger.exception("Partition worker error")
        await asyncio.sleep(_PARTITION_INTERVAL)


async def _approval_timeout_worker(app: FastAPI) -> None:
    """Periodically expire pending approvals past their deadline."""
    structlog.contextvars.bind_contextvars(worker="approval_timeout")
    while True:
        try:
            async with async_session_factory()() as db:
                expired = await expire_approvals(db)
                await db.commit()
                if expired:
                    logger.info("Expired %d approval(s)", len(expired))
                    push: PushManager = app.state.push_manager
                    for item in expired:
                        timeout_data = {
                            "type": "approval_timeout",
                            "approval_id": item["id"],
                            "agent_id": item["agent_id"],
                            "tool_name": item["tool_name"],
                        }
                        push.push_to_env(item["env"], timeout_data, tenant_id=item["tenant_id"])
                        push.push_to_dashboard(item["tenant_id"], timeout_data)
                    # Group expired items by tenant for tenant-scoped notification
                    mgr: NotificationManager = app.state.notification_manager
                    by_tenant: dict[str, list[dict[str, Any]]] = {}
                    for item in expired:
                        tid = str(item["tenant_id"])
                        by_tenant.setdefault(tid, []).append(item)
                    for tid, tenant_items in by_tenant.items():
                        for ch in mgr.channels_for_tenant(tid):
                            if hasattr(ch, "update_expired"):
                                try:
                                    await ch.update_expired(tenant_items)
                                except Exception:
                                    logger.exception("Failed to update expired notifications")
        except Exception:
            logger.exception("Approval timeout worker error")
        await asyncio.sleep(10)


async def _worker_monitor(app: FastAPI) -> None:
    """Restart crashed background workers every 60 seconds."""
    structlog.contextvars.bind_contextvars(worker="monitor")
    while True:
        await asyncio.sleep(60)
        try:
            workers = app.state.background_workers
            if workers["approval_timeout"].done():
                logger.warning("Restarting crashed approval_timeout worker")
                workers["approval_timeout"] = asyncio.create_task(_approval_timeout_worker(app))
            if workers["partition"].done():
                logger.warning("Restarting crashed partition worker")
                workers["partition"] = asyncio.create_task(_partition_worker())
        except Exception:
            logger.exception("Worker monitor error")
