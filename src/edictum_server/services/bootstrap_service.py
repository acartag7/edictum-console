"""Domain layer -- first-run bootstrap and maintenance logic.

Handles admin user creation on first run, signing key backfill
for existing tenants, and periodic AI usage log cleanup.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import func, select

from edictum_server.config import Settings, get_settings
from edictum_server.db.engine import async_session_factory

logger = logging.getLogger(__name__)


async def bootstrap_admin() -> None:
    """Create default tenant + admin user on first run if no users exist."""
    settings = get_settings()
    from edictum_server.auth.local import LocalAuthProvider
    from edictum_server.db.models import SigningKey as SigningKeyModel
    from edictum_server.db.models import Tenant, User
    from edictum_server.services.signing_service import generate_signing_keypair

    async with async_session_factory()() as db:
        # Advisory lock prevents concurrent bootstrap across instances (S7).
        # Lock 42 is shared with the /api/v1/setup endpoint so the two
        # bootstrap paths are mutually exclusive.
        await db.execute(sa.text("SELECT pg_advisory_xact_lock(42)"))

        result = await db.execute(select(func.count()).select_from(User))
        user_count = result.scalar() or 0

        if user_count > 0:
            return

        # No users yet -- check if env-var bootstrap is configured
        if not settings.admin_email or not settings.admin_password:
            logger.warning(
                "No admin account exists. "
                "Visit /dashboard/setup to create one, or set "
                "EDICTUM_ADMIN_EMAIL and EDICTUM_ADMIN_PASSWORD and restart."
            )
            return

        if len(settings.admin_password) < 12:
            logger.error(
                "EDICTUM_ADMIN_PASSWORD must be at least 12 characters. "
                "Bootstrap aborted — visit /dashboard/setup instead."
            )
            return

        # Create default tenant
        tenant = Tenant(name="default")
        db.add(tenant)
        await db.flush()

        # Create admin user
        password_hash = LocalAuthProvider.hash_password(settings.admin_password)
        admin = User(
            tenant_id=tenant.id,
            email=settings.admin_email,
            password_hash=password_hash,
            is_admin=True,
        )
        db.add(admin)
        await db.flush()

        # Create initial signing key for bundle deployment
        if settings.signing_key_secret:
            secret = bytes.fromhex(settings.signing_key_secret)
            public_key_bytes, encrypted_private_key = generate_signing_keypair(secret)
            signing_key = SigningKeyModel(
                tenant_id=tenant.id,
                public_key=public_key_bytes,
                private_key_encrypted=encrypted_private_key,
                active=True,
            )
            db.add(signing_key)
            logger.info("Created initial signing key for tenant")

        await db.commit()
        logger.info("Bootstrapped admin user: %s", settings.admin_email)


async def ensure_signing_keys(settings: Settings) -> None:
    """Backfill: create signing keys for tenants that don't have one.

    This handles existing deployments that were bootstrapped before
    signing key auto-creation was added.
    """
    if not settings.signing_key_secret:
        return

    from edictum_server.db.models import SigningKey as SigningKeyModel
    from edictum_server.db.models import Tenant
    from edictum_server.services.signing_service import generate_signing_keypair

    async with async_session_factory()() as db:
        # Find tenants without an active signing key
        tenants_with_keys = (
            select(SigningKeyModel.tenant_id).where(SigningKeyModel.active.is_(True)).subquery()
        )
        result = await db.execute(
            select(Tenant).where(Tenant.id.not_in(select(tenants_with_keys.c.tenant_id)))
        )
        tenants = result.scalars().all()

        if not tenants:
            return

        secret = bytes.fromhex(settings.signing_key_secret)
        for tenant in tenants:
            public_key_bytes, encrypted_private_key = generate_signing_keypair(secret)
            key = SigningKeyModel(
                tenant_id=tenant.id,
                public_key=public_key_bytes,
                private_key_encrypted=encrypted_private_key,
                active=True,
            )
            db.add(key)
            logger.info("Created signing key for tenant %s", tenant.id)

        await db.commit()


async def cleanup_ai_usage() -> None:
    """Delete AI usage log rows older than 90 days.

    NOTE: Intentionally cross-tenant -- this is an internal maintenance
    operation that only deletes expired rows and never returns data.
    Do not copy this pattern for data-access queries.
    """
    from edictum_server.db.models import AiUsageLog

    try:
        cutoff = datetime.now(UTC) - timedelta(days=90)
        async with async_session_factory()() as db:
            result = await db.execute(sa.delete(AiUsageLog).where(AiUsageLog.created_at < cutoff))
            rows_deleted = result.rowcount  # type: ignore[attr-defined]
            if rows_deleted:
                await db.commit()
                logger.info("Cleaned up %d old AI usage log(s)", rows_deleted)
    except Exception:
        logger.exception("AI usage cleanup error")
