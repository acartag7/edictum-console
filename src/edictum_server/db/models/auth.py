"""Auth infrastructure — ApiKey and SigningKey models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from edictum_server.db.models.tenant import Tenant


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hashed API key for agent authentication."""

    __tablename__ = "api_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    key_prefix: Mapped[str] = mapped_column(String(32), index=True)
    key_hash: Mapped[str] = mapped_column(String)
    env: Mapped[str] = mapped_column(String)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")


class SigningKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Ed25519 key pair for bundle signing."""

    __tablename__ = "signing_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    private_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="signing_keys")
