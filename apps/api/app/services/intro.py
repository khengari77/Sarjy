"""Persist the one-time introduction acknowledgement shared by both delivery paths."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import UserSetting


async def mark_intro_seen(db: AsyncSession, owner_id: str) -> None:
    """Record that the full introduction played to the end; repeated calls change nothing."""
    row = await db.get(UserSetting, owner_id)
    if row is None:
        row = UserSetting(owner_id=owner_id)
        db.add(row)
    if row.intro_seen_at is None:
        row.intro_seen_at = datetime.now(UTC).isoformat()
        await db.commit()
