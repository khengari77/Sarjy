"""The visibility rule for temporary and permanent memories."""

import asyncio

import pytest
from app.infrastructure.database import Base, Conversation, Memory
from app.services.memory_scope import is_visible, visible_in
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def memory(identifier, scope, chat):
    return Memory(
        id=identifier,
        owner_id="owner",
        source_conversation_id=chat,
        content=identifier,
        scope=scope,
        status="active",
        version=1,
    )


def test_the_sql_predicate_shows_global_everywhere_and_temporary_only_in_its_own_chat(tmp_path):
    async def main():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/scope.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                db.add_all([Conversation(id="a", owner_id="owner"), Conversation(id="b", owner_id="owner")])
                db.add_all(
                    [
                        memory("global-a", "global", "a"),
                        memory("global-b", "global", "b"),
                        memory("temp-a", "conversation", "a"),
                        memory("temp-b", "conversation", "b"),
                    ]
                )
                await db.commit()

                async def seen(chat):
                    rows = await db.scalars(select(Memory.id).where(visible_in(chat)).order_by(Memory.id))
                    return list(rows)

                assert await seen("a") == ["global-a", "global-b", "temp-a"]
                assert await seen("b") == ["global-a", "global-b", "temp-b"]
                assert await seen("someone-else") == ["global-a", "global-b"]
                assert await seen(None) == ["global-a", "global-b"]
        finally:
            await engine.dispose()

    asyncio.run(main())


@pytest.mark.parametrize(
    ("scope", "source", "chat", "expected"),
    [
        ("global", "a", "b", True),
        ("global", "a", None, True),
        ("conversation", "a", "a", True),
        ("conversation", "a", "b", False),
        ("conversation", "a", None, False),
        ("unknown", "a", "a", False),
    ],
)
def test_the_python_rule_matches_the_sql_rule(scope, source, chat, expected):
    assert is_visible(scope, source, chat) is expected
