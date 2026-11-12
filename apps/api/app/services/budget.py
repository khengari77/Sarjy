from datetime import datetime, timezone
from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncConnection, async_sessionmaker
from app.domain.budget import budget_day, OPENAI_DAILY_MICRO_USD, OPENAI_REQUEST_MICRO_USD, ELEVENLABS_DAILY_CHARACTERS
from app.infrastructure.database import DevelopmentBudget


async def reserve_in_transaction(
    db: AsyncSession, provider: str, characters: int = 0, *, now: datetime | None = None
) -> dict:
    if provider not in {"openai", "elevenlabs"}:
        raise ValueError("Unsupported development budget")
    units = OPENAI_REQUEST_MICRO_USD if provider == "openai" else characters
    limit = OPENAI_DAILY_MICRO_USD if provider == "openai" else ELEVENLABS_DAILY_CHARACTERS
    if units <= 0 or units > limit:
        raise ValueError("DAILY_BUDGET_EXHAUSTED")
    day = budget_day(now or datetime.now(timezone.utc))
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ValueError("Unsupported budget database")
    await db.execute(
        insert(DevelopmentBudget).values(day=day, provider=provider, reserved_units=0).on_conflict_do_nothing()
    )
    changed = await db.execute(
        update(DevelopmentBudget)
        .where(
            DevelopmentBudget.day == day,
            DevelopmentBudget.provider == provider,
            DevelopmentBudget.reserved_units <= limit - units,
        )
        .values(reserved_units=DevelopmentBudget.reserved_units + units)
    )
    if changed.rowcount != 1:
        raise ValueError("DAILY_BUDGET_EXHAUSTED")
    total = await db.scalar(
        select(DevelopmentBudget.reserved_units).where(
            DevelopmentBudget.day == day, DevelopmentBudget.provider == provider
        )
    )
    return {
        "day": day,
        "provider": provider,
        "reserved_units": total,
        "remaining_units": limit - total,
        "unit": "micro_usd" if provider == "openai" else "characters",
    }


async def reserve(db: AsyncSession, provider: str, characters: int = 0, *, now: datetime | None = None) -> dict:
    """Compatibility boundary owning commit/rollback; use only a clean session."""
    try:
        result = await reserve_in_transaction(db, provider, characters, now=now)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def guarded_openai_request(db_or_factory, request):
    """Commit the shared reservation in a dedicated session before delivery.

    Accept a session factory, or an existing session solely to discover its engine.
    Never flush, commit or roll back the caller's business session. Delivery errors
    retain the committed reservation. The callable must not perform ledger writes.
    """
    if isinstance(db_or_factory, AsyncSession):
        bind = db_or_factory.bind
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        factory = async_sessionmaker(engine, expire_on_commit=False)
    else:
        factory = db_or_factory
    async with factory() as budget_db:
        async with budget_db.begin():
            await reserve_in_transaction(budget_db, "openai")
    return await request()
