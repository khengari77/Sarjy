from datetime import datetime, timezone
from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.budget import budget_day, OPENAI_DAILY_MICRO_USD, OPENAI_REQUEST_MICRO_USD, ELEVENLABS_DAILY_CHARACTERS
from app.infrastructure.database import DevelopmentBudget


async def reserve(db: AsyncSession, provider: str, characters: int = 0, *, now: datetime | None = None) -> dict:
    if provider not in {'openai','elevenlabs'}:
        raise ValueError('Unsupported development budget')
    units = OPENAI_REQUEST_MICRO_USD if provider == 'openai' else characters
    limit = OPENAI_DAILY_MICRO_USD if provider == 'openai' else ELEVENLABS_DAILY_CHARACTERS
    if units <= 0 or units > limit:
        raise ValueError('DAILY_BUDGET_EXHAUSTED')
    day = budget_day(now or datetime.now(timezone.utc))
    if db.bind.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif db.bind.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ValueError('Unsupported budget database')
    await db.execute(insert(DevelopmentBudget).values(day=day,provider=provider,reserved_units=0).on_conflict_do_nothing())
    changed = await db.execute(update(DevelopmentBudget).where(DevelopmentBudget.day == day,DevelopmentBudget.provider == provider,DevelopmentBudget.reserved_units <= limit-units).values(reserved_units=DevelopmentBudget.reserved_units+units))
    if changed.rowcount != 1:
        await db.rollback()
        raise ValueError('DAILY_BUDGET_EXHAUSTED')
    total = await db.scalar(select(DevelopmentBudget.reserved_units).where(DevelopmentBudget.day == day,DevelopmentBudget.provider == provider))
    await db.commit()
    return {'day':day,'provider':provider,'reserved_units':total,'remaining_units':limit-total,'unit':'micro_usd' if provider=='openai' else 'characters'}


async def guarded_openai_request(db: AsyncSession, request):
    """Reserve on the shared durable ledger before invoking an async boundary.

    This covers embeddings/extraction as well as generation. No refunds follow
    delivery uncertainty. Call only after authorization and argument validation.
    """
    await reserve(db, 'openai')
    return await request()
