"""AI configuration service — CRUD + encryption for per-tenant AI config."""

from __future__ import annotations

import logging
import uuid

from nacl.secret import SecretBox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import TenantAiConfig
from edictum_server.security.validators import ValidationError as SecurityError
from edictum_server.security.validators import validate_url

logger = logging.getLogger(__name__)


async def get_ai_config(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> TenantAiConfig | None:
    """Fetch the AI config for a tenant, or None if not configured."""
    result = await db.execute(select(TenantAiConfig).where(TenantAiConfig.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def upsert_ai_config(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    provider: str,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
    secret: bytes,
    updated_by: str,
) -> TenantAiConfig:
    """Create or update tenant AI config. Encrypts API key before storage."""
    # Validate base_url against private/internal networks to prevent SSRF (#23)
    if base_url:
        try:
            await validate_url(base_url)
        except SecurityError as exc:
            raise ValueError(f"Invalid base_url: {exc}") from exc

    encrypted_key: bytes | None = None
    if api_key:
        box = SecretBox(secret)
        encrypted_key = box.encrypt(api_key.encode("utf-8"))

    existing = await get_ai_config(db, tenant_id)
    if existing:
        existing.provider = provider
        if encrypted_key is not None:
            existing.api_key_encrypted = encrypted_key
        existing.model = model
        existing.base_url = base_url
        existing.updated_by = updated_by
        await db.flush()
        return existing

    config = TenantAiConfig(
        tenant_id=tenant_id,
        provider=provider,
        api_key_encrypted=encrypted_key,
        model=model,
        base_url=base_url,
        updated_by=updated_by,
    )
    db.add(config)
    await db.flush()
    return config


async def delete_ai_config(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> bool:
    """Delete tenant AI config. Returns True if deleted, False if not found."""
    existing = await get_ai_config(db, tenant_id)
    if not existing:
        return False
    await db.delete(existing)
    await db.flush()
    return True


def decrypt_api_key(encrypted: bytes, secret: bytes) -> str:
    """Decrypt an API key from storage."""
    box = SecretBox(secret)
    return box.decrypt(encrypted).decode("utf-8")


def mask_api_key(raw: str) -> str:
    """Mask API key for display: first 8 + ... + last 4."""
    if len(raw) <= 12:
        return "***"
    return f"{raw[:8]}...{raw[-4:]}"


async def log_usage(
    *,
    tenant_id: uuid.UUID,
    provider_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    duration_ms: int,
    cost: float | None,
) -> None:
    """Persist an AI usage log entry. Fire-and-forget — errors are logged, not raised."""
    try:
        from edictum_server.db.engine import async_session_factory
        from edictum_server.db.models import AiUsageLog

        async with async_session_factory()() as session:
            log = AiUsageLog(
                tenant_id=tenant_id,
                provider=provider_name,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                estimated_cost_usd=cost,
            )
            session.add(log)
            await session.commit()
    except Exception:
        logger.exception("Failed to log AI usage for tenant %s", tenant_id)
