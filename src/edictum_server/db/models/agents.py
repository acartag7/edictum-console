"""Fleet aggregate — AgentRegistration and AssignmentRule models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent agent identity — auto-created on first SSE connect."""

    __tablename__ = "agent_registrations"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", name="uq_agent_reg_tenant_agent"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bundle_name: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssignmentRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Pattern-based bundle assignment rule — lower priority = evaluated first."""

    __tablename__ = "assignment_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "priority", name="uq_assignment_rule_tenant_priority"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer)
    pattern: Mapped[str] = mapped_column(String)
    tag_match: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    bundle_name: Mapped[str] = mapped_column(String)
    env: Mapped[str] = mapped_column(String)
