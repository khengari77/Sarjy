"""Dialect-aware ``INSERT ... ON CONFLICT DO NOTHING`` boundary."""

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
