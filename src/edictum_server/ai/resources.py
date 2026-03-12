"""AI assistant resources — pre-fetched context injected at conversation start.

Follows the MCP pattern: resources are data the LLM can read (always-available
context), vs tools which are functions it calls on-demand. This module fetches
tenant-specific and static data, assembles it into a compact text block, and
injects it as a context message before the first LLM call.

Architecture note: This module is intentionally decoupled from HTTP transport.
Resources can be exposed as MCP server resources in the future — each section
maps to a separate MCP resource URI.

Security:
- All tenant-scoped queries filter by ``tenant_id`` from auth context.
- Resource content is injected as a user data message (not system prompt)
  to prevent prompt injection from stored contract names/descriptions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from importlib import resources as importlib_resources

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Cache for static template content (loaded once, never changes at runtime).
_templates_cache: dict[str, str] = {}


async def build_resource_context(
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> str:
    """Pre-fetch tenant context to inject before the conversation.

    Returns a formatted text block with three sections:
    1. Contract templates (static, from edictum package)
    2. Existing contracts summary (tenant-specific)
    3. Agent tool usage summary (tenant-specific, last 7 days)
    """
    sections: list[str] = []

    # 1. Contract templates (static, cached)
    templates = _load_templates()
    if templates:
        sections.append(f"## Contract Templates (built-in examples)\n{templates}")

    # 2. Existing contracts
    contracts = await _fetch_existing_contracts(db, tenant_id)
    if contracts:
        sections.append(f"## Your Existing Contracts\n{contracts}")
    else:
        sections.append("## Your Existing Contracts\nNo contracts created yet.")

    # 3. Agent tool usage
    tool_usage = await _fetch_agent_tool_usage(db, tenant_id)
    if tool_usage:
        sections.append(f"## Your Agents' Tool Usage (last 7 days)\n{tool_usage}")
    else:
        sections.append("## Your Agents' Tool Usage (last 7 days)\nNo agent events recorded yet.")

    return "\n\n".join(sections)


def _load_templates() -> str:
    """Load contract templates from the edictum package (cached).

    Uses a dict as a mutable cache container to avoid PLW0603 global statement.
    """
    if "value" in _templates_cache:
        return _templates_cache["value"]

    try:
        templates_dir = importlib_resources.files("edictum.yaml_engine") / "templates"
        parts: list[str] = []
        for item in sorted(templates_dir.iterdir(), key=lambda t: t.name):
            if not item.name.endswith(".yaml"):
                continue
            content = item.read_text(encoding="utf-8")
            try:
                parsed = yaml.safe_load(content)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue

            bundle_name = parsed.get("metadata", {}).get("name", item.name)
            description = parsed.get("metadata", {}).get("description", "")
            contracts = parsed.get("contracts", [])
            if not contracts:
                continue

            # Include only the contracts array, trimmed for context efficiency
            contracts_yaml = yaml.safe_dump(
                contracts,
                default_flow_style=False,
                sort_keys=False,
            )
            parts.append(
                f"### {bundle_name}\n{description}\n```yaml\n{contracts_yaml.strip()}\n```"
            )

        _templates_cache["value"] = "\n\n".join(parts) if parts else ""
    except Exception:
        logger.warning("Failed to load contract templates", exc_info=True)
        _templates_cache["value"] = ""

    return _templates_cache["value"]


async def _fetch_existing_contracts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 30,
) -> str:
    """Fetch a compact summary of existing contracts for this tenant."""
    from edictum_server.db.models import Contract

    result = await db.execute(
        select(Contract)
        .where(
            Contract.tenant_id == tenant_id,
            Contract.is_latest.is_(True),
        )
        .order_by(Contract.created_at.desc())
        .limit(limit)
    )
    contracts = result.scalars().all()
    if not contracts:
        return ""

    lines: list[str] = []
    for c in contracts:
        tags = ", ".join(c.tags) if c.tags else ""
        tag_str = f" [tags: {tags}]" if tags else ""
        desc = f" — {c.description}" if c.description else ""
        lines.append(f"- `{c.contract_id}` ({c.type}): {c.name}{desc}{tag_str}")

    return "\n".join(lines)


async def _fetch_agent_tool_usage(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days: int = 7,
    limit: int = 30,
) -> str:
    """Fetch agent tool usage grouped by tool_name for the last N days."""
    from edictum_server.db.models import Event

    since = datetime.now(UTC) - timedelta(days=days)

    # Build a grouped query — works on both PostgreSQL and SQLite
    stmt = (
        select(
            Event.tool_name,
            func.count().label("call_count"),
        )
        .where(
            Event.tenant_id == tenant_id,
            Event.created_at >= since,
        )
        .group_by(Event.tool_name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        return ""

    # Separate query for deny counts (avoids FILTER which isn't in SQLite)
    deny_stmt = (
        select(
            Event.tool_name,
            func.count().label("deny_count"),
        )
        .where(
            Event.tenant_id == tenant_id,
            Event.created_at >= since,
            Event.verdict == "deny",
        )
        .group_by(Event.tool_name)
    )
    deny_result = await db.execute(deny_stmt)
    deny_map = {row.tool_name: row.deny_count for row in deny_result.all()}

    lines: list[str] = []
    for row in rows:
        deny_count = deny_map.get(row.tool_name, 0)
        deny_str = f", {deny_count} denied" if deny_count else ""
        lines.append(f"- `{row.tool_name}`: {row.call_count} calls{deny_str}")

    return "\n".join(lines)
