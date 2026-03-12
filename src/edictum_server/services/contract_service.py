"""Contract library CRUD — create, version, list, delete."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import (
    BundleComposition,
    BundleCompositionItem,
    Contract,
)

logger = logging.getLogger(__name__)

_CONTRACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VALID_TYPES = {"pre", "post", "session", "sandbox"}
_MAX_VERSION_RETRIES = 5


async def create_contract(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: str,
    name: str,
    type: str,
    definition: dict[str, Any],
    created_by: str,
    description: str | None = None,
    tags: list[str] | None = None,
) -> Contract:
    """Create a new contract (version 1). Raises ValueError if contract_id exists."""
    if not _CONTRACT_ID_RE.match(contract_id):
        raise ValueError("contract_id must match [a-z0-9][a-z0-9_-]*")
    if type not in _VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")

    # Use savepoint so a concurrent create of the same contract_id
    # hits the unique constraint gracefully instead of causing a 500.
    contract = Contract(
        tenant_id=tenant_id,
        contract_id=contract_id,
        version=1,
        type=type,
        name=name,
        description=description,
        definition=definition,
        tags=tags or [],
        is_latest=True,
        created_by=created_by,
    )
    try:
        async with db.begin_nested():
            db.add(contract)
            await db.flush()
    except IntegrityError as exc:
        raise ValueError(
            f"Contract '{contract_id}' already exists. Use PUT to create a new version."
        ) from exc
    return contract


async def update_contract(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: str,
    created_by: str,
    name: str | None = None,
    description: str | None = None,
    definition: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Contract:
    """Create a new version of an existing contract. Uses savepoint retry for races."""
    if all(v is None for v in (name, description, definition, tags)):
        raise ValueError("At least one field must be provided for update")

    for attempt in range(_MAX_VERSION_RETRIES):
        result = await db.execute(
            select(Contract).where(
                Contract.tenant_id == tenant_id,
                Contract.contract_id == contract_id,
                Contract.is_latest.is_(True),
            )
        )
        latest = result.scalar_one_or_none()
        if latest is None:
            raise ValueError(f"Contract '{contract_id}' not found")

        next_version = latest.version + 1
        new_contract = Contract(
            tenant_id=tenant_id,
            contract_id=contract_id,
            version=next_version,
            type=latest.type,
            name=name if name is not None else latest.name,
            description=description if description is not None else latest.description,
            definition=definition if definition is not None else latest.definition,
            tags=tags if tags is not None else latest.tags,
            is_latest=True,
            created_by=created_by,
        )

        try:
            async with db.begin_nested():
                latest.is_latest = False
                db.add(new_contract)
                await db.flush()
            return new_contract
        except IntegrityError:
            logger.warning(
                "Contract version conflict: %s v%d (attempt %d/%d)",
                contract_id,
                next_version,
                attempt + 1,
                _MAX_VERSION_RETRIES,
            )
            db.expire_all()
            continue

    raise RuntimeError(
        f"Failed to assign version for contract '{contract_id}' "
        f"after {_MAX_VERSION_RETRIES} attempts."
    )


async def get_contract(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: str,
    version: int | None = None,
) -> Contract | None:
    """Get a contract by contract_id. Latest version if version is None."""
    query = select(Contract).where(
        Contract.tenant_id == tenant_id,
        Contract.contract_id == contract_id,
    )
    if version is not None:
        query = query.where(Contract.version == version)
    else:
        query = query.where(Contract.is_latest.is_(True))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_contract_versions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: str,
) -> list[Contract]:
    """Return all versions of a contract, newest first."""
    result = await db.execute(
        select(Contract)
        .where(
            Contract.tenant_id == tenant_id,
            Contract.contract_id == contract_id,
        )
        .order_by(Contract.version.desc())
    )
    return list(result.scalars().all())


async def list_contracts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    type_filter: str | None = None,
    tag_filter: str | None = None,
    search: str | None = None,
) -> list[Contract]:
    """List latest versions of all contracts, with optional filters."""
    query = select(Contract).where(
        Contract.tenant_id == tenant_id,
        Contract.is_latest.is_(True),
    )
    if type_filter:
        query = query.where(Contract.type == type_filter)
    if search:
        # Escape LIKE wildcards in user input to prevent wildcard injection
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            Contract.name.ilike(pattern)
            | Contract.description.ilike(pattern)
            | Contract.contract_id.ilike(pattern)
        )
    query = query.order_by(Contract.created_at.desc())
    result = await db.execute(query)
    contracts = list(result.scalars().all())

    # Tag filter — applied in Python since JSON contains varies by DB
    if tag_filter:
        contracts = [c for c in contracts if tag_filter in (c.tags or [])]

    return contracts


async def delete_contract(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: str,
) -> None:
    """Delete all versions of a contract. Raises ValueError if referenced."""
    # Get all version UUIDs for this contract_id
    result = await db.execute(
        select(Contract.id).where(
            Contract.tenant_id == tenant_id,
            Contract.contract_id == contract_id,
        )
    )
    contract_uuids = list(result.scalars().all())
    if not contract_uuids:
        raise ValueError(f"Contract '{contract_id}' not found")

    # Check if any BundleCompositionItem references any version
    ref_result = await db.execute(
        select(BundleComposition.name)
        .join(
            BundleCompositionItem,
            BundleCompositionItem.composition_id == BundleComposition.id,
        )
        .where(
            BundleCompositionItem.contract_id.in_(contract_uuids),
            BundleComposition.tenant_id == tenant_id,
        )
        .distinct()
    )
    composition_names = list(ref_result.scalars().all())
    if composition_names:
        raise ValueError(
            f"Contract '{contract_id}' is referenced by compositions: "
            f"{', '.join(composition_names)}. Remove it from those first."
        )

    # Delete all versions
    for cid in contract_uuids:
        obj = await db.get(Contract, cid)
        if obj:
            await db.delete(obj)
    await db.flush()


async def get_contract_usage(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contract_id: str,
) -> list[dict[str, Any]]:
    """Return compositions that reference any version of this contract."""
    result = await db.execute(
        select(Contract.id).where(
            Contract.tenant_id == tenant_id,
            Contract.contract_id == contract_id,
        )
    )
    contract_uuids = list(result.scalars().all())
    if not contract_uuids:
        return []

    comp_result = await db.execute(
        select(
            BundleComposition.id.label("composition_id"),
            BundleComposition.name.label("composition_name"),
        )
        .join(
            BundleCompositionItem,
            BundleCompositionItem.composition_id == BundleComposition.id,
        )
        .where(
            BundleCompositionItem.contract_id.in_(contract_uuids),
            BundleComposition.tenant_id == tenant_id,
        )
        .distinct()
    )
    return [
        {"composition_id": row.composition_id, "composition_name": row.composition_name}
        for row in comp_result.all()
    ]
