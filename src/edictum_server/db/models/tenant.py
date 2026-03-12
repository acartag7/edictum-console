"""Identity aggregate — Tenant and User models."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from edictum_server.db.models.auth import ApiKey, SigningKey
    from edictum_server.db.models.deployment import Bundle, Deployment


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer organisation."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String, unique=True)
    external_auth_id: Mapped[str | None] = mapped_column(
        String,
        unique=True,
        nullable=True,
        index=True,
    )

    # Relationships
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="tenant")
    signing_keys: Mapped[list[SigningKey]] = relationship(back_populates="tenant")
    bundles: Mapped[list[Bundle]] = relationship(back_populates="tenant")
    deployments: Mapped[list[Deployment]] = relationship(back_populates="tenant")
    users: Mapped[list[User]] = relationship(back_populates="tenant")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A local user account."""

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(default=False)

    tenant: Mapped[Tenant] = relationship(back_populates="users")
