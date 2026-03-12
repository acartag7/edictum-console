"""Import contracts from YAML bundles into the contract library."""

from __future__ import annotations

import uuid
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import (
    BundleComposition,
    BundleCompositionItem,
)
from edictum_server.services.contract_service import (
    create_contract,
    get_contract,
    update_contract,
)


async def import_from_yaml(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    yaml_content: str,
    created_by: str,
) -> dict[str, Any]:
    """Import contracts from a YAML bundle. Returns created/updated lists."""
    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Invalid YAML: expected a mapping at the top level")

    api_version = parsed.get("apiVersion")
    kind = parsed.get("kind")
    if api_version != "edictum/v1":
        raise ValueError(f"Unsupported apiVersion: {api_version!r} (expected 'edictum/v1')")
    if kind != "ContractBundle":
        raise ValueError(f"Unsupported kind: {kind!r} (expected 'ContractBundle')")

    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("name"):
        raise ValueError("Missing required field: metadata.name")
    bundle_name: str = metadata["name"]

    contracts_list = parsed.get("contracts")
    if not isinstance(contracts_list, list) or not contracts_list:
        raise ValueError("No contracts found in YAML")

    created: list[str] = []
    updated: list[str] = []

    for entry in contracts_list:
        if not isinstance(entry, dict):
            raise ValueError("Each contract must be a mapping")
        cid = entry.get("id")
        if not cid or not isinstance(cid, str):
            raise ValueError("Each contract must have an 'id' field")

        contract_type = entry.get("type", "pre")
        # Build definition from the contract body minus the 'id' field
        definition = {k: v for k, v in entry.items() if k != "id"}

        # Humanize name from id
        display_name = cid.replace("-", " ").replace("_", " ").title()

        # Check if this contract_id already exists
        existing = await get_contract(db, tenant_id, cid)
        if existing:
            await update_contract(
                db,
                tenant_id,
                cid,
                created_by,
                definition=definition,
            )
            updated.append(cid)
        else:
            await create_contract(
                db,
                tenant_id,
                contract_id=cid,
                name=display_name,
                type=contract_type,
                definition=definition,
                created_by=created_by,
            )
            created.append(cid)

    # Auto-create a BundleComposition referencing the imported contracts
    composition_name: str | None = None
    existing_comp = await db.execute(
        select(BundleComposition).where(
            BundleComposition.tenant_id == tenant_id,
            BundleComposition.name == bundle_name,
        )
    )
    if existing_comp.scalar_one_or_none() is None:
        comp = BundleComposition(
            tenant_id=tenant_id,
            name=bundle_name,
            description="Auto-created from YAML import",
            defaults_mode="enforce",
            update_strategy="manual",
            created_by=created_by,
        )
        db.add(comp)
        await db.flush()

        # Add items referencing latest version of each imported contract
        # Deduplicate: a contract_id that appears twice in YAML ends up in
        # both created+updated — adding two items for the same UUID violates
        # the UNIQUE constraint on (composition_id, contract_id).
        all_cids = list(dict.fromkeys(created + updated))
        for position, cid in enumerate(all_cids):
            contract = await get_contract(db, tenant_id, cid)
            if contract:
                item = BundleCompositionItem(
                    tenant_id=tenant_id,
                    composition_id=comp.id,
                    contract_id=contract.id,
                    position=(position + 1) * 10,
                    enabled=True,
                )
                db.add(item)

        await db.flush()
        composition_name = bundle_name

    return {
        "contracts_created": created,
        "contracts_updated": updated,
        "bundle_composition_created": composition_name,
    }
