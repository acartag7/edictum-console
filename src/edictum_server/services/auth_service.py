"""Authentication service — user lookup operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import User


async def find_user_by_email(
    db: AsyncSession,
    email: str,
) -> User | None:
    """Find a user by email. Returns None if not found."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()
