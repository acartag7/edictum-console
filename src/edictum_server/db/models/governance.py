"""Contract authoring aggregate — Contract, BundleComposition, BundleCompositionItem."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Contract(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single reusable governance rule in the contract library."""

    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "contract_id",
            "version",
            name="uq_contract_tenant_id_version",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_contract_tenant_pk"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    contract_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int]
    type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String)


class BundleComposition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Defines which contracts compose a bundle, with mode and ordering."""

    __tablename__ = "bundle_compositions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_composition_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_composition_tenant_pk"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    defaults_mode: Mapped[str] = mapped_column(String, default="enforce")
    update_strategy: Mapped[str] = mapped_column(String, default="manual")
    tools_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    observability: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class BundleCompositionItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Join table: contract membership within a bundle composition."""

    __tablename__ = "bundle_composition_items"
    __table_args__ = (
        UniqueConstraint("composition_id", "contract_id", name="uq_item_composition_contract"),
        ForeignKeyConstraint(
            ["tenant_id", "composition_id"],
            ["bundle_compositions.tenant_id", "bundle_compositions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contract_id"],
            ["contracts.tenant_id", "contracts.id"],
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    composition_id: Mapped[uuid.UUID] = mapped_column()
    contract_id: Mapped[uuid.UUID] = mapped_column()
    position: Mapped[int]
    mode_override: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
