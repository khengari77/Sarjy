"""Owner-scoped atomic memory storage and replaceable embedding boundary."""
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Protocol
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from app.infrastructure.database import Conversation, Memory, Turn, ContextExclusion
from app.services.budget import reserve


def validate_embedding(values) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if len(vector) != EMBEDDING_DIMENSIONS or not all(isfinite(value) for value in vector) or not any(vector):
        raise ValueError('INVALID_EMBEDDING')
    return vector


def cosine_distance(left, right):
    return 1 - sum(a*b for a,b in zip(left,right)) / (sqrt(sum(a*a for a in left))*sqrt(sum(b*b for b in right)))


class Embedder(Protocol):
    async def embed(self, db: AsyncSession, text: str) -> tuple[float, ...]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: str, *, transport=None):
        self.api_key, self.transport = api_key, transport

    async def embed(self, db: AsyncSession, text: str) -> tuple[float, ...]:
        if not self.api_key:
            raise HTTPException(503, detail={'code': 'EMBEDDINGS_NOT_CONFIGURED'})
        await reserve(db, 'openai')
        async with httpx.AsyncClient(transport=self.transport, timeout=15) as client:
            response = await client.post('https://api.openai.com/v1/embeddings',
                headers={'authorization': 'Bearer '+self.api_key},
                json={'model': EMBEDDING_MODEL, 'input': text, 'dimensions': EMBEDDING_DIMENSIONS, 'encoding_format': 'float'})
            response.raise_for_status()
            return validate_embedding(response.json()['data'][0]['embedding'])


@dataclass(frozen=True)
class MemoryResult:
    id: str
    owner_id: str
    content: str
    version: int
    source_conversation_id: str
    source_turn_id: str | None
    status: str
    supersedes_id: str | None
    distance: float | None = None


def memory_result(row: Memory, distance=None) -> MemoryResult:
    return MemoryResult(row.id,row.owner_id,row.content,row.version,row.source_conversation_id,
        row.source_turn_id,row.status,row.supersedes_id,distance)


class MemoryService:
    def __init__(self, db: AsyncSession, embedder: Embedder):
        self.db, self.embedder = db, embedder

    async def conversation(self, owner_id, conversation_id):
        row = await self.db.scalar(select(Conversation).where(Conversation.id==conversation_id,Conversation.owner_id==owner_id))
        if row is None:
            raise HTTPException(404, detail={'code':'CONVERSATION_NOT_FOUND'})
        return row

    async def get_memory(self, owner_id, memory_id):
        row = await self.db.scalar(select(Memory).where(Memory.id==memory_id,Memory.owner_id==owner_id))
        if row is None:
            raise HTTPException(404, detail={'code':'MEMORY_NOT_FOUND'})
        return row

    async def _retry(self, owner_id, key, content, supersedes_id, conversation_id):
        row = await self.db.scalar(select(Memory).where(Memory.owner_id==owner_id,Memory.request_key==key))
        if row is not None and (row.content != content or row.supersedes_id != supersedes_id or row.source_conversation_id != conversation_id):
            raise HTTPException(409, detail={'code':'IDEMPOTENCY_CONFLICT'})
        return row

    async def create_memory(self, owner_id, conversation_id, source_turn_id, content, idempotency_key):
        return await self._write(owner_id,conversation_id,source_turn_id,content,idempotency_key,None,None)

    async def correct_memory(self, owner_id, memory_id, expected_version, content, idempotency_key, *, source_turn_id=None):
        previous = await self.get_memory(owner_id,memory_id)
        return await self._write(owner_id,previous.source_conversation_id,source_turn_id,content,
            idempotency_key,memory_id,expected_version)

    async def _write(self, owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version):
        if not isinstance(content,str) or not content.strip() or len(content)>4000 or not isinstance(key,str) or not 1<=len(key)<=100:
            raise HTTPException(422, detail={'code':'INVALID_MEMORY_REQUEST'})
        await self.conversation(owner_id,conversation_id)
        if source_turn_id is not None:
            turn = await self.db.scalar(select(Turn).where(Turn.id==source_turn_id,Turn.conversation_id==conversation_id))
            if turn is None:
                raise HTTPException(404, detail={'code':'SOURCE_TURN_NOT_FOUND'})
        retry = await self._retry(owner_id,key,content,previous_id,conversation_id)
        if retry is not None:
            if retry.source_turn_id != source_turn_id or (previous_id is not None and retry.version != expected_version+1):
                raise HTTPException(409, detail={'code':'IDEMPOTENCY_CONFLICT'})
            return memory_result(retry)
        if previous_id is not None:
            previous = await self.get_memory(owner_id,previous_id)
            if previous.status != 'active' or previous.version != expected_version:
                raise HTTPException(409, detail={'code':'MEMORY_VERSION_CONFLICT'})
        vector = validate_embedding(await self.embedder.embed(self.db,content))
        # Obtain a database write lock on SQLite and a row lock on PostgreSQL.
        await self.db.execute(update(Conversation).where(Conversation.id==conversation_id,Conversation.owner_id==owner_id).values(revision=Conversation.revision))
        retry = await self._retry(owner_id,key,content,previous_id,conversation_id)
        if retry is not None:
            await self.db.commit()
            return memory_result(retry)
        version = 1
        if previous_id is not None:
            changed = await self.db.execute(update(Memory).where(
                Memory.id==previous_id, Memory.owner_id==owner_id,
                Memory.status=='active', Memory.version==expected_version,
            ).values(status='superseded'))
            if changed.rowcount != 1:
                await self.db.rollback()
                raise HTTPException(409, detail={'code':'MEMORY_VERSION_CONFLICT'})
            version = expected_version+1
        row = Memory(id=str(uuid4()),owner_id=owner_id,content=content,embedding=list(vector),embedding_model=EMBEDDING_MODEL,
            source_turn_id=source_turn_id,source_conversation_id=conversation_id,version=version,status='active',supersedes_id=previous_id,request_key=key)
        self.db.add(row)
        await self.db.commit()
        return memory_result(row)

    async def search_memories(self, owner_id, conversation_id, query, limit=5):
        await self.conversation(owner_id,conversation_id)
        if not isinstance(query,str) or not query.strip() or len(query)>4000 or not 1<=limit<=20:
            raise HTTPException(422, detail={'code':'INVALID_MEMORY_QUERY'})
        vector = validate_embedding(await self.embedder.embed(self.db,query))
        excluded = select(ContextExclusion.memory_id).where(ContextExclusion.owner_id==owner_id,ContextExclusion.conversation_id==conversation_id,ContextExclusion.active.is_(True))
        statement = select(Memory).where(Memory.owner_id==owner_id,Memory.status=='active',Memory.embedding.is_not(None), Memory.id.not_in(excluded))
        if self.db.bind.dialect.name=='postgresql':
            distance = Memory.embedding.cosine_distance(list(vector)).label('distance')
            rows = (await self.db.execute(statement.add_columns(distance).order_by(distance,Memory.id).limit(limit))).all()
            return tuple(memory_result(row, float(score)) for row,score in rows)
        rows = (await self.db.scalars(statement)).all()
        ranked = sorted(((cosine_distance(vector,validate_embedding(row.embedding)),row) for row in rows),key=lambda pair:(pair[0],pair[1].id))
        return tuple(memory_result(row,float(score)) for score,row in ranked[:limit])
