"""Distribution aggregate — Bundle and Deployment models."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from edictum_server.db.models.tenant import Tenant


class Bundle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A versioned contract bundle."""

    __tablename__ = "bundles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_bundle_tenant_name_version"),
        ForeignKeyConstraint(
            ["tenant_id", "composition_id"],
            ["bundle_compositions.tenant_id", "bundle_compositions.id"],
            ondelete="SET NULL",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int]
    revision_hash: Mapped[str] = mapped_column(String(64), index=True)
    yaml_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    signature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_hub_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    source_hub_revision: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String)
    composition_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    composition_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="bundles")


class Deployment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Record of a bundle version being deployed to an environment."""

    __tablename__ = "deployments"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    env: Mapped[str] = mapped_column(String)
    bundle_name: Mapped[str] = mapped_column(String, index=True)
    bundle_version: Mapped[int]
    deployed_by: Mapped[str] = mapped_column(String)

    tenant: Mapped[Tenant] = relationship(back_populates="deployments")
