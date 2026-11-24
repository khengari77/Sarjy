"""Reading memories for a turn must not read their embeddings (#81)."""

from app.infrastructure.database import Memory
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import undefer


def test_embeddings_are_read_only_on_request():
    def compiled(statement):
        return str(statement.compile(dialect=postgresql.dialect()))

    assert "memories.content" in compiled(select(Memory))
    assert "memories.embedding," not in compiled(select(Memory))
    assert "memories.embedding," in compiled(select(Memory).options(undefer(Memory.embedding)))
