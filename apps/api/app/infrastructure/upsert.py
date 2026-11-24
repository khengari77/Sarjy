"""Dialect-aware ``INSERT ... ON CONFLICT`` boundary."""

from sqlalchemy.ext.asyncio import AsyncSession


def _insert_for(db: AsyncSession):
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ValueError(f"Unsupported upsert database: {dialect}")
    return insert


async def insert_ignore(db: AsyncSession, model, values: dict, *, index_elements: list[str] | None = None):
    """Insert one row and ignore a conflicting row on the backend's dialect."""
    statement = _insert_for(db)(model).values(**values)
    if index_elements is None:
        statement = statement.on_conflict_do_nothing()
    else:
        statement = statement.on_conflict_do_nothing(index_elements=index_elements)
    return await db.execute(statement)


async def insert_or_increment(
    db: AsyncSession,
    model,
    values: dict,
    *,
    index_elements: list[str],
    column: str,
    amount: int,
    limit: int,
):
    """Insert one row or add ``amount`` to ``column`` while staying within ``limit``.

    The insert and the conditional increment are one statement, so a concurrent
    writer can neither lose an increment nor push the total past the limit on the
    conflict path. A brand-new row has no existing total to compare, so the helper
    refuses ``amount <= 0`` or ``amount > limit`` itself rather than trusting callers.
    Returns the new column value, or ``None`` when the increment was refused.
    """
    if amount <= 0 or amount > limit:
        return None
    target = getattr(model, column)
    statement = (
        _insert_for(db)(model)
        .values(**values)
        .on_conflict_do_update(
            index_elements=index_elements,
            set_={column: target + amount},
            where=target <= limit - amount,
        )
        .returning(target)
    )
    return await db.scalar(statement)
