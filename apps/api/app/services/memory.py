"""Owner-scoped atomic memory and durable, reserve-before-delivery request claims."""
import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite, sqrt
from typing import Protocol
from uuid import uuid4

import httpx
from app.domain.contracts import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from app.infrastructure.database import Conversation, Memory, MemoryWriteRequest, Turn, now
from app.services.budget import guarded_openai_request, reserve_in_transaction
from app.services.memory_eligibility import blocked_memory_ids
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

EMBEDDING_TIMEOUT_SECONDS = 20
CLAIM_TIMEOUT_SECONDS = 30


def validate_embedding(values) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if len(vector) != EMBEDDING_DIMENSIONS or not all(isfinite(value) for value in vector) or not any(vector):
        raise ValueError('INVALID_EMBEDDING')
    return vector


def cosine_distance(left, right):
    return 1 - sum(a*b for a,b in zip(left,right,strict=True)) / (sqrt(sum(a*a for a in left))*sqrt(sum(b*b for b in right)))


class Embedder(Protocol):
    async def embed(self, db: AsyncSession, text: str) -> tuple[float, ...]: ...


class EmbeddingFailure(HTTPException):
    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(503, detail={'code': code})
        self.uncertain = uncertain


class OpenAIEmbedder:
    def __init__(self, api_key: str, *, transport=None):
        self.api_key, self.transport = api_key, transport

    def require_configuration(self):
        if not self.api_key:
            raise HTTPException(503, detail={'code': 'EMBEDDINGS_NOT_CONFIGURED'})

    async def embed(self, db: AsyncSession, text: str) -> tuple[float, ...]:
        """Ordinary search: reserve using a separate committed budget session."""
        self.require_configuration()
        return await guarded_openai_request(db, lambda: self.embed_reserved(text))

    async def embed_reserved(self, text: str) -> tuple[float, ...]:
        """Transport for a durable write claim whose allowance is already reserved."""
        self.require_configuration()
        try:
            async with asyncio.timeout(EMBEDDING_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=self.transport, timeout=15) as client:
                    response = await client.post('https://api.openai.com/v1/embeddings',
                        headers={'authorization': 'Bearer '+self.api_key},
                        json={'model': EMBEDDING_MODEL, 'input': text, 'dimensions': EMBEDDING_DIMENSIONS, 'encoding_format': 'float'})
                    response.raise_for_status()
                    return validate_embedding(response.json()['data'][0]['embedding'])
        except httpx.HTTPStatusError as exc:
            raise EmbeddingFailure('EMBEDDINGS_UNAVAILABLE') from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise EmbeddingFailure('EMBEDDING_DELIVERY_UNKNOWN', uncertain=True) from exc
        except (ValueError, KeyError, IndexError, TypeError, OverflowError) as exc:
            raise EmbeddingFailure('EMBEDDING_RESPONSE_INVALID') from exc


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


def _fingerprint(conversation_id, source_turn_id, content, previous_id, expected_version):
    payload = {'kind': 'correct' if previous_id else 'create', 'conversation_id': conversation_id,
        'source_turn_id': source_turn_id, 'content': content, 'previous_id': previous_id,
        'expected_version': expected_version, 'embedding_model': EMBEDDING_MODEL,
        'embedding_dimensions': EMBEDDING_DIMENSIONS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _claim_value(row):
    return {name: getattr(row, name) for name in ('id', 'fingerprint', 'state', 'embedding',
        'result', 'error_code', 'error_status', 'created_at')}


class MemoryService:
    def __init__(self, db: AsyncSession, embedder: Embedder):
        self.db, self.embedder = db, embedder
        self.sessions = async_sessionmaker(db.bind, expire_on_commit=False)

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

    async def create_memory(self, owner_id, conversation_id, source_turn_id, content, idempotency_key):
        return await self._write(owner_id,conversation_id,source_turn_id,content,idempotency_key,None,None)

    async def correct_memory(self, owner_id, memory_id, expected_version, content, idempotency_key, *, source_turn_id=None):
        previous = await self.get_memory(owner_id,memory_id)
        conversation_id = previous.source_conversation_id
        return await self._write(owner_id,conversation_id,source_turn_id,content,idempotency_key,memory_id,expected_version)

    async def _claim(self, owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version, fingerprint):
        if self.db.new or self.db.dirty or self.db.deleted:
            raise ValueError('MEMORY_SESSION_HAS_PENDING_WRITES')
        # Authorization/source reads are complete; never hold their transaction across HTTP.
        await self.db.rollback()
        async with self.sessions() as db, db.begin():
                existing = await db.scalar(select(MemoryWriteRequest).where(
                    MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key))
                if existing is not None:
                    return _claim_value(existing), False
                legacy = await db.scalar(select(Memory).where(Memory.owner_id == owner_id, Memory.request_key == key))
                if legacy is not None:
                    legacy_fingerprint = _fingerprint(legacy.source_conversation_id, legacy.source_turn_id,
                        legacy.content, legacy.supersedes_id, legacy.version - 1 if legacy.supersedes_id else None)
                    if legacy_fingerprint != fingerprint:
                        raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
                if db.bind.dialect.name == 'sqlite':
                    from sqlalchemy.dialects.sqlite import insert
                else:
                    from sqlalchemy.dialects.postgresql import insert
                identifier = str(uuid4())
                values = dict(id=identifier, owner_id=owner_id, conversation_id=conversation_id,
                    request_key=key, fingerprint=fingerprint, state='completed' if legacy else 'reserved',
                    result=asdict(memory_result(legacy)) if legacy else None, created_at=now(), updated_at=now())
                inserted = await db.execute(insert(MemoryWriteRequest).values(**values).on_conflict_do_nothing(
                    index_elements=['owner_id', 'request_key']))
                if inserted.rowcount != 1:
                    existing = await db.scalar(select(MemoryWriteRequest).where(
                        MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key))
                    return _claim_value(existing), False
                if legacy is None:
                    if hasattr(self.embedder, 'require_configuration'):
                        self.embedder.require_configuration()
                    if previous_id is not None:
                        previous = await db.get(Memory, previous_id)
                        if previous is None or previous.owner_id != owner_id or previous.status != 'active' or previous.version != expected_version:
                            raise HTTPException(409, detail={'code': 'MEMORY_VERSION_CONFLICT'})
                    # This helper never commits: claim and allowance become durable together.
                    await reserve_in_transaction(db, 'openai')
                row = await db.get(MemoryWriteRequest, identifier)
                return _claim_value(row), legacy is None

    def _replay(self, claim, fingerprint):
        if claim['fingerprint'] != fingerprint:
            raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
        if claim['state'] == 'completed':
            return MemoryResult(**claim['result'])
        if claim['state'] in {'failed', 'unknown'}:
            raise HTTPException(claim['error_status'] or 503, detail={'code': claim['error_code'] or 'MEMORY_REQUEST_UNKNOWN'})
        if claim['state'] == 'reserved':
            age = (datetime.now(UTC) - datetime.fromisoformat(claim['created_at'])).total_seconds()
            if age >= CLAIM_TIMEOUT_SECONDS:
                # No automatic lease takeover: delivery after a process crash is uncertain.
                raise HTTPException(503, detail={'code': 'MEMORY_REQUEST_UNKNOWN'})
            raise HTTPException(409, detail={'code': 'MEMORY_REQUEST_IN_PROGRESS'})
        return None

    async def _mark_error(self, identifier, code, status=503, *, uncertain=False):
        async with self.sessions() as db, db.begin():
            await db.execute(update(MemoryWriteRequest).where(
                MemoryWriteRequest.id == identifier,
                MemoryWriteRequest.state.in_(['reserved', 'embedded']),
            ).values(state='unknown' if uncertain else 'failed', error_code=code,
                error_status=status, updated_at=now()))

    async def _finalize(self, identifier, fingerprint, owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version):
        async with self.sessions() as db, db.begin():
                # Short row-local write lock (also effective on SQLite), never held across inference.
                await db.execute(update(MemoryWriteRequest).where(MemoryWriteRequest.id == identifier).values(updated_at=now()))
                row = await db.get(MemoryWriteRequest, identifier)
                claim = _claim_value(row)
                if claim['state'] != 'embedded':
                    result = self._replay(claim, fingerprint)
                    if result is not None:
                        return result
                    raise HTTPException(503, detail={'code': 'MEMORY_REQUEST_UNKNOWN'})
                if claim['fingerprint'] != fingerprint:
                    raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
                conversation = await db.scalar(select(Conversation).where(
                    Conversation.id == conversation_id, Conversation.owner_id == owner_id))
                if conversation is None:
                    raise HTTPException(404, detail={'code': 'CONVERSATION_NOT_FOUND'})
                if source_turn_id is not None:
                    source = await db.scalar(select(Turn).where(
                        Turn.id == source_turn_id, Turn.conversation_id == conversation_id))
                    if source is None:
                        raise HTTPException(404, detail={'code': 'SOURCE_TURN_NOT_FOUND'})
                version = 1
                if previous_id is not None:
                    changed = await db.execute(update(Memory).where(Memory.id == previous_id,
                        Memory.owner_id == owner_id, Memory.status == 'active', Memory.version == expected_version,
                    ).values(status='superseded'))
                    if changed.rowcount != 1:
                        raise HTTPException(409, detail={'code': 'MEMORY_VERSION_CONFLICT'})
                    version = expected_version + 1
                memory = Memory(id=str(uuid4()), owner_id=owner_id, content=content,
                    embedding=list(validate_embedding(claim['embedding'])), embedding_model=EMBEDDING_MODEL,
                    source_turn_id=source_turn_id, source_conversation_id=conversation_id,
                    version=version, status='active', supersedes_id=previous_id, request_key=key)
                db.add(memory)
                await db.flush()
                result = memory_result(memory)
                row.state, row.result, row.updated_at = 'completed', asdict(result), now()
                return result

    async def _write(self, owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version):
        if not isinstance(content,str) or not content.strip() or len(content)>4000 or not isinstance(key,str) or not 1<=len(key)<=100:
            raise HTTPException(422, detail={'code':'INVALID_MEMORY_REQUEST'})
        if previous_id is not None and (not isinstance(expected_version, int) or expected_version < 1):
            raise HTTPException(422, detail={'code': 'INVALID_MEMORY_VERSION'})
        await self.conversation(owner_id,conversation_id)
        if source_turn_id is not None:
            turn = await self.db.scalar(select(Turn).where(Turn.id==source_turn_id,Turn.conversation_id==conversation_id))
            if turn is None:
                raise HTTPException(404, detail={'code':'SOURCE_TURN_NOT_FOUND'})
        fingerprint = _fingerprint(conversation_id,source_turn_id,content,previous_id,expected_version)
        claim, winner = await self._claim(owner_id,conversation_id,source_turn_id,content,key,previous_id,expected_version,fingerprint)
        if not winner:
            result = self._replay(claim, fingerprint)
            if result is not None:
                return result
        else:
            try:
                async with asyncio.timeout(EMBEDDING_TIMEOUT_SECONDS):
                    if hasattr(self.embedder, 'embed_reserved'):
                        vector = validate_embedding(await self.embedder.embed_reserved(content))
                    else:
                        # Backwards-compatible deterministic test/embedder adapters must not reserve again.
                        vector = validate_embedding(await self.embedder.embed(self.db,content))
                async with self.sessions() as db, db.begin():
                    await db.execute(update(MemoryWriteRequest).where(
                        MemoryWriteRequest.id == claim['id'], MemoryWriteRequest.state == 'reserved',
                    ).values(state='embedded', embedding=list(vector), updated_at=now()))
            except asyncio.CancelledError:
                await asyncio.shield(self._mark_error(claim['id'], 'MEMORY_REQUEST_UNKNOWN', uncertain=True))
                raise
            except EmbeddingFailure as exc:
                await self._mark_error(claim['id'], exc.detail['code'], uncertain=exc.uncertain)
                raise
            except (httpx.HTTPError, TimeoutError) as exc:
                await self._mark_error(claim['id'], 'MEMORY_REQUEST_UNKNOWN', uncertain=True)
                raise HTTPException(503, detail={'code': 'MEMORY_REQUEST_UNKNOWN'}) from exc
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
                await self._mark_error(claim['id'], 'EMBEDDING_RESPONSE_INVALID')
                raise HTTPException(503, detail={'code': 'EMBEDDING_RESPONSE_INVALID'}) from exc
        try:
            return await self._finalize(claim['id'],fingerprint,owner_id,conversation_id,source_turn_id,content,key,previous_id,expected_version)
        except HTTPException as exc:
            if exc.status_code == 409:
                await self._mark_error(claim['id'], exc.detail['code'], 409)
            raise

    async def search_memories(self, owner_id, conversation_id, query, limit=5):
        if not isinstance(query,str) or not query.strip() or len(query)>4000 or not 1<=limit<=20:
            raise HTTPException(422, detail={'code':'INVALID_MEMORY_QUERY'})
        # Search owns its read and budget sessions; never rollback a caller's effects.
        async with self.sessions() as access:
            conversation = await access.scalar(select(Conversation).where(
                Conversation.id == conversation_id, Conversation.owner_id == owner_id))
            if conversation is None:
                raise HTTPException(404, detail={'code':'CONVERSATION_NOT_FOUND'})
        async with self.sessions() as budget_db:
            vector = validate_embedding(await self.embedder.embed(budget_db,query))
        async with self.sessions() as db:
            excluded = await blocked_memory_ids(db, owner_id, conversation_id)
            statement = select(Memory).where(Memory.owner_id==owner_id,Memory.status=='active',Memory.embedding.is_not(None), Memory.id.not_in(excluded))
            if db.bind.dialect.name=='postgresql':
                distance = Memory.embedding.cosine_distance(list(vector)).label('distance')
                rows = (await db.execute(statement.add_columns(distance).order_by(distance,Memory.id).limit(limit))).all()
                return tuple(memory_result(row, float(score)) for row,score in rows)
            rows = (await db.scalars(statement)).all()
            ranked = sorted(((cosine_distance(vector,validate_embedding(row.embedding)),row) for row in rows),key=lambda pair:(pair[0],pair[1].id))
            return tuple(memory_result(row,float(score)) for score,row in ranked[:limit])
