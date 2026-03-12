"""Notifications — NotificationChannel model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NotificationChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configuration for a notification channel (e.g. Telegram, Slack).

    The ``config`` dict is encrypted at rest in ``config_encrypted`` using
    NaCl SecretBox (same pattern as ``SigningKey.private_key_encrypted``
    and ``TenantAiConfig.api_key_encrypted``).

    After migration 006, new rows store secrets in ``config_encrypted``
    only; the ``config`` JSON column is NULL.  Pre-migration rows may
    still have plain-text config until the migration runs.

    Encryption/decryption is handled by the service layer — see
    ``notification_service.encrypt_config()`` / ``decrypt_config()``.
    """

    __tablename__ = "notification_channels"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    channel_type: Mapped[str] = mapped_column(String)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    config_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    filters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
