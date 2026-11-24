"""Terminal finalization failures are durable and never re-embed."""

import asyncio

import pytest
from app.infrastructure.database import MemoryWriteRequest, Turn
from app.services.memory import MemoryService
from fastapi import HTTPException
from sqlalchemy import delete, select
from test_memory import setup


class SourceDeletingEmbedder:
    def __init__(self, sessions):
        self.sessions, self.calls = sessions, 0

    async def embed_reserved(self, text):
        self.calls += 1
        async with self.sessions() as db:
            await db.execute(delete(Turn).where(Turn.id == "source"))
            await db.commit()
        return tuple([1.0] + [0.0] * 1535)


def test_source_loss_during_embedding_records_terminal_failure(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "terminal.db")
        embedder = SourceDeletingEmbedder(sessions)
        try:
            async with sessions() as db:
                service = MemoryService(db, embedder)
                with pytest.raises(HTTPException) as first:
                    await service.create_memory("alice", "a1", "source", "Tripoli", "terminal")
                assert first.value.status_code == 404
                assert first.value.detail["code"] == "SOURCE_TURN_NOT_FOUND"
                with pytest.raises(HTTPException) as retry:
                    await service.create_memory("alice", "a1", "source", "Tripoli", "terminal")
                assert retry.value.status_code == 404
                assert retry.value.detail["code"] == "SOURCE_TURN_NOT_FOUND"
            async with sessions() as db:
                claim = await db.scalar(select(MemoryWriteRequest))
                assert claim.state == "failed"
                assert claim.error_code == "SOURCE_TURN_NOT_FOUND"
                assert claim.error_status == 404
            assert embedder.calls == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
