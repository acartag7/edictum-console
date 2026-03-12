"""API key management service — list, revoke operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import ApiKey


async def list_api_keys(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[ApiKey]:
    """List all non-revoked API keys for a tenant."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.tenant_id == tenant_id,
            ApiKey.revoked_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def get_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> ApiKey | None:
    """Get a single non-revoked API key by ID, scoped to tenant."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.tenant_id == tenant_id,
            ApiKey.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def revoke_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> bool:
    """Revoke an API key by setting revoked_at. Returns False if not found."""
    key = await get_api_key(db, tenant_id, key_id)
    if key is None:
        return False
    await db.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
        .values(revoked_at=datetime.now(UTC))
    )
    return True
