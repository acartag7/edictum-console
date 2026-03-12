"""Read-model queries for bundles — dashboard / UI aggregation logic.

These functions cross aggregate boundaries (Bundle + Deployment) and serve
the UI layer.  They are separated from the core domain operations in
``bundle_service`` following a CQRS-style split.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import Bundle, Deployment


async def list_bundle_names(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    env: str | None = None,
) -> list[dict[str, object]]:
    """Return distinct bundle names with aggregates (version_count, latest_version).

    When *env* is provided, only versions deployed to that environment are
    counted — prevents cross-env metadata leakage for env-scoped API keys.
    """
    if env is not None:
        # Scoped: only count versions that have a deployment to this env
        stmt = (
            select(
                Bundle.name,
                func.count(func.distinct(Bundle.id)).label("version_count"),
                func.max(Bundle.version).label("latest_version"),
                func.max(Bundle.created_at).label("last_updated"),
            )
            .join(
                Deployment,
                (Bundle.tenant_id == Deployment.tenant_id)
                & (Bundle.name == Deployment.bundle_name)
                & (Bundle.version == Deployment.bundle_version),
            )
            .where(Bundle.tenant_id == tenant_id, Deployment.env == env)
            .group_by(Bundle.name)
            .order_by(func.max(Bundle.created_at).desc())
        )
    else:
        stmt = (
            select(
                Bundle.name,
                func.count(Bundle.id).label("version_count"),
                func.max(Bundle.version).label("latest_version"),
                func.max(Bundle.created_at).label("last_updated"),
            )
            .where(Bundle.tenant_id == tenant_id)
            .group_by(Bundle.name)
            .order_by(func.max(Bundle.created_at).desc())
        )
    result = await db.execute(stmt)
    return [
        {
            "name": row.name,
            "version_count": row.version_count,
            "latest_version": row.latest_version,
            "last_updated": row.last_updated,
        }
        for row in result.all()
    ]


async def get_deployed_envs_map(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    bundle_name: str | None = None,
) -> dict[int, list[str]]:
    """Return mapping of bundle_version -> deployed env names (current only)."""
    filters = [Deployment.tenant_id == tenant_id]
    if bundle_name is not None:
        filters.append(Deployment.bundle_name == bundle_name)

    # Subquery: rank deployments per env by recency
    ranked = (
        select(
            Deployment.env,
            Deployment.bundle_version,
            func.row_number()
            .over(
                partition_by=Deployment.env,
                order_by=Deployment.created_at.desc(),
            )
            .label("rn"),
        )
        .where(*filters)
        .subquery()
    )

    result = await db.execute(
        select(ranked.c.bundle_version, ranked.c.env).where(ranked.c.rn == 1)
    )

    mapping: dict[int, list[str]] = defaultdict(list)
    for version, env in result.all():
        mapping[version].append(env)
    for envs in mapping.values():
        envs.sort()
    return dict(mapping)


async def get_deployed_envs_by_bundle_name(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict[str, list[str]]:
    """Return mapping of bundle_name -> list of currently deployed env names.

    Single query for all bundles — avoids the N+1 problem of calling
    ``get_deployed_envs_map`` per bundle name.
    """
    ranked = (
        select(
            Deployment.bundle_name,
            Deployment.env,
            func.row_number()
            .over(
                partition_by=[Deployment.bundle_name, Deployment.env],
                order_by=Deployment.created_at.desc(),
            )
            .label("rn"),
        )
        .where(Deployment.tenant_id == tenant_id)
        .subquery()
    )

    result = await db.execute(select(ranked.c.bundle_name, ranked.c.env).where(ranked.c.rn == 1))

    mapping: dict[str, list[str]] = defaultdict(list)
    for name, env in result.all():
        mapping[name].append(env)
    for envs in mapping.values():
        envs.sort()
    return dict(mapping)


async def get_bundle_enrichment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    env: str | None = None,
) -> dict[str, dict[str, object]]:
    """Return contract_count and last_deployed_at per bundle name.

    contract_count is parsed from the latest deployed version's YAML.
    last_deployed_at comes from the deployments table.

    When *env* is provided, both the "latest version" subquery and the
    deployment timestamp are scoped to that environment — prevents
    cross-env metadata leakage for env-scoped API keys.
    """
    # Get latest version per bundle name (optionally scoped to env)
    if env is not None:
        # Latest version deployed to this specific env
        latest_subq = (
            select(
                Deployment.bundle_name.label("name"),
                func.max(Deployment.bundle_version).label("max_version"),
            )
            .where(Deployment.tenant_id == tenant_id, Deployment.env == env)
            .group_by(Deployment.bundle_name)
            .subquery()
        )
    else:
        # Latest version across all envs (dashboard view)
        latest_subq = (
            select(
                Bundle.name,
                func.max(Bundle.version).label("max_version"),
            )
            .where(Bundle.tenant_id == tenant_id)
            .group_by(Bundle.name)
            .subquery()
        )
    latest_bundles = await db.execute(
        select(Bundle)
        .join(
            latest_subq,
            (Bundle.name == latest_subq.c.name) & (Bundle.version == latest_subq.c.max_version),
        )
        .where(Bundle.tenant_id == tenant_id)
    )

    enrichment: dict[str, dict[str, object]] = {}
    for bundle in latest_bundles.scalars().all():
        contract_count = 0
        try:
            parsed = yaml.safe_load(bundle.yaml_bytes)
            if isinstance(parsed, dict):
                contracts = parsed.get("contracts", [])
                if isinstance(contracts, list):
                    contract_count = len(contracts)
        except yaml.YAMLError:
            pass
        enrichment[bundle.name] = {"contract_count": contract_count, "last_deployed_at": None}

    # Get last deployment date per bundle name (scoped to env if provided)
    dep_filters = [Deployment.tenant_id == tenant_id]
    if env is not None:
        dep_filters.append(Deployment.env == env)
    dep_result = await db.execute(
        select(
            Deployment.bundle_name,
            func.max(Deployment.created_at).label("last_deployed_at"),
        )
        .where(*dep_filters)
        .group_by(Deployment.bundle_name)
    )
    for row in dep_result.all():
        if row.bundle_name in enrichment:
            enrichment[row.bundle_name]["last_deployed_at"] = row.last_deployed_at

    return enrichment
