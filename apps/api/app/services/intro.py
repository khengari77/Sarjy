"""Persist the one-time introduction acknowledgement shared by both delivery paths."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import UserSetting
from app.infrastructure.upsert import insert_ignore


async def mark_intro_seen(db: AsyncSession, owner_id: str) -> None:
    """Record that the full introduction played to the end; repeated calls change nothing.

    The row is inserted with ON CONFLICT DO NOTHING so overlapping confirmations
    (worker and browser) cannot race each other into an IntegrityError, then one
    conditional update sets the timestamp only while it is still unset.
    """
    await insert_ignore(db, UserSetting, {"owner_id": owner_id}, index_elements=["owner_id"])
    await db.execute(
        update(UserSetting)
        .where(UserSetting.owner_id == owner_id, UserSetting.intro_seen_at.is_(None))
        .values(intro_seen_at=datetime.now(UTC).isoformat())
    )
    await db.commit()
