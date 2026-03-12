"""Health service — bootstrap status and infrastructure checks."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from edictum_server.db.models import User


async def get_user_count(db: AsyncSession) -> int:
    """Get the total number of users. Used for bootstrap status check."""
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar() or 0
