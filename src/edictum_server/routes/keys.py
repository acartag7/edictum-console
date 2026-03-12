"""API key management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.auth.api_keys import generate_api_key
from edictum_server.auth.dependencies import AuthContext, require_admin, require_dashboard_auth
from edictum_server.db.engine import get_db
from edictum_server.db.models import ApiKey
from edictum_server.push.manager import PushManager, get_push_manager
from edictum_server.schemas.keys import ApiKeyInfo, CreateKeyRequest, CreateKeyResponse
from edictum_server.services import key_service

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])


@router.post(
    "",
    response_model=CreateKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_key(
    body: CreateKeyRequest,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    push: PushManager = Depends(get_push_manager),
) -> CreateKeyResponse:
    """Create a new API key for the authenticated tenant.

    Only available to dashboard-authenticated users.
    The full key is returned once and cannot be retrieved again.
    """
    full_key, prefix, key_hash = generate_api_key(body.env)

    api_key = ApiKey(
        tenant_id=auth.tenant_id,
        key_prefix=prefix,
        key_hash=key_hash,
        env=body.env,
        label=body.label,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    push.push_to_dashboard(
        auth.tenant_id,
        {
            "type": "api_key_created",
            "key_id": str(api_key.id),
            "env": body.env,
            "label": body.label,
        },
    )

    return CreateKeyResponse(
        id=str(api_key.id),
        key=full_key,
        prefix=api_key.key_prefix,
        env=body.env,
        label=body.label,
        created_at=api_key.created_at,
    )


@router.get("", response_model=list[ApiKeyInfo])
async def list_keys(
    auth: AuthContext = Depends(require_dashboard_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyInfo]:
    """List all non-revoked API keys for the authenticated tenant."""
    rows = await key_service.list_api_keys(db, auth.tenant_id)

    return [
        ApiKeyInfo(
            id=str(row.id),
            prefix=row.key_prefix,
            env=row.env,
            label=row.label,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    push: PushManager = Depends(get_push_manager),
) -> None:
    """Revoke an API key by setting its revoked_at timestamp.

    Only the owning tenant (via dashboard auth) can revoke their keys.
    """
    revoked = await key_service.revoke_api_key(db, auth.tenant_id, key_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or already revoked.",
        )
    await db.commit()

    push.push_to_dashboard(
        auth.tenant_id,
        {
            "type": "api_key_revoked",
            "key_id": str(key_id),
        },
    )
