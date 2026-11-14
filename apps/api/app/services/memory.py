"""Owner-scoped atomic memory and durable, reserve-before-delivery request claims."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite, sqrt
from typing import Protocol
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.context_operations import classify as _classify
from app.domain.contracts import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from app.domain.memory_capture import is_referential, resolve_subject
from app.infrastructure.database import ContextExclusion, Memory, MemoryWriteRequest, Turn, now
from app.infrastructure.upsert import insert_ignore
from app.services.authorization import owned_conversation, owned_memory
from app.services.budget import guarded_openai_request, reserve_in_transaction
from app.services.context_assembly import MEMORY_CANDIDATE_LIMIT
from app.services.memory_eligibility import blocked_memory_ids

EMBEDDING_TIMEOUT_SECONDS = 20
CLAIM_TIMEOUT_SECONDS = 30
# SQLite has no vector index, so cosine ranking reads a bounded deterministic
# candidate set instead of every active owner memory.
SEARCH_CANDIDATE_LIMIT = 500
SQLITE_BUSY = 5
SQLITE_LOCKED = 6


def _sqlite_busy(exc: OperationalError) -> bool:
    code = getattr(getattr(exc, "orig", None), "sqlite_errorcode", None)
    if code is not None:
        return code in {SQLITE_BUSY, SQLITE_LOCKED}
    return "database is locked" in str(exc).lower()


def validate_embedding(values) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if len(vector) != EMBEDDING_DIMENSIONS or not all(isfinite(value) for value in vector) or not any(vector):
        raise ValueError("INVALID_EMBEDDING")
    return vector


def cosine_distance(left, right):
    return 1 - sum(a * b for a, b in zip(left, right, strict=True)) / (
        sqrt(sum(a * a for a in left)) * sqrt(sum(b * b for b in right))
    )


class Embedder(Protocol):
    async def embed(self, db: AsyncSession, text: str) -> tuple[float, ...]: ...


LeaseGuard = Callable[[AsyncSession], Awaitable[None]]


class EmbeddingFailure(HTTPException):
    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(503, detail={"code": code})
        self.uncertain = uncertain


class OpenAIEmbedder:
    def __init__(self, api_key: str, *, transport=None):
        self.api_key, self.transport = api_key, transport

    def require_configuration(self):
        if not self.api_key:
            raise HTTPException(503, detail={"code": "EMBEDDINGS_NOT_CONFIGURED"})

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
                    response = await client.post(
                        "https://api.openai.com/v1/embeddings",
                        headers={"authorization": "Bearer " + self.api_key},
                        json={
                            "model": EMBEDDING_MODEL,
                            "input": text,
                            "dimensions": EMBEDDING_DIMENSIONS,
                            "encoding_format": "float",
                        },
                    )
                    response.raise_for_status()
                    return validate_embedding(response.json()["data"][0]["embedding"])
        except httpx.HTTPStatusError as exc:
            raise EmbeddingFailure("EMBEDDINGS_UNAVAILABLE") from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise EmbeddingFailure("EMBEDDING_DELIVERY_UNKNOWN", uncertain=True) from exc
        except (ValueError, KeyError, IndexError, TypeError, OverflowError) as exc:
            raise EmbeddingFailure("EMBEDDING_RESPONSE_INVALID") from exc


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
    return MemoryResult(
        row.id,
        row.owner_id,
        row.content,
        row.version,
        row.source_conversation_id,
        row.source_turn_id,
        row.status,
        row.supersedes_id,
        distance,
    )


@dataclass(frozen=True)
class MemoryListItem:
    """One stored fact with the provenance the explorer must show.

    ``included_in_conversation`` mirrors the next compiled context's bounded
    candidate view for a conversation: active, not excluded, and inside the
    newest candidate window. It is not a claim about past model input.
    """

    id: str
    content: str
    kind: str
    version: int
    status: str
    source_conversation_id: str
    source_turn_id: str | None
    supersedes_id: str | None
    created_at: str
    updated_at: str
    excluded_in_conversation: bool | None = None
    included_in_conversation: bool | None = None


@dataclass(frozen=True)
class MemoryListPage:
    items: tuple[MemoryListItem, ...]
    total: int
    next_offset: int | None


def _fingerprint(conversation_id, source_turn_id, content, previous_id, expected_version):
    payload = {
        "kind": "correct" if previous_id else "create",
        "conversation_id": conversation_id,
        "source_turn_id": source_turn_id,
        "content": content,
        "previous_id": previous_id,
        "expected_version": expected_version,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _claim_value(row):
    return {
        name: getattr(row, name)
        for name in ("id", "fingerprint", "state", "embedding", "result", "error_code", "error_status", "created_at")
    }


def _validate_write(content, key, previous_id, expected_version) -> None:
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content) > 4000
        or not isinstance(key, str)
        or not 1 <= len(key) <= 100
    ):
        raise HTTPException(422, detail={"code": "INVALID_MEMORY_REQUEST"})
    if is_referential(content):
        # A bare back-reference ("it", "that thing") carries no fact of its own.
        raise HTTPException(422, detail={"code": "REFERENTIAL_MEMORY_CONTENT"})
    if previous_id is not None and (not isinstance(expected_version, int) or expected_version < 1):
        raise HTTPException(422, detail={"code": "INVALID_MEMORY_VERSION"})


async def _require_source_turn(db: AsyncSession, source_turn_id, conversation_id) -> None:
    if source_turn_id is None:
        return
    turn = await db.scalar(select(Turn).where(Turn.id == source_turn_id, Turn.conversation_id == conversation_id))
    if turn is None:
        raise HTTPException(404, detail={"code": "SOURCE_TURN_NOT_FOUND"})


class MemoryService:
    """Memory writes own their sessions.

    ``db_or_factory`` may be a session (for backwards compatibility), a session
    factory, or an engine. Writes never read, flush, commit, or roll back the
    caller's session, so pending business changes survive a memory operation.
    """

    def __init__(self, db_or_factory, embedder: Embedder):
        self.embedder = embedder
        self.caller = db_or_factory if isinstance(db_or_factory, AsyncSession) else None
        self.caller_dialect = None
        self.shared_connection = False
        if isinstance(db_or_factory, AsyncSession):
            bind = db_or_factory.bind
            engine = bind.engine if isinstance(bind, AsyncConnection) else bind
            self.caller_dialect = engine.dialect.name
            self.shared_connection = isinstance(engine.pool, StaticPool)
            self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        elif isinstance(db_or_factory, AsyncEngine):
            self.sessions = async_sessionmaker(db_or_factory, expire_on_commit=False)
        else:
            self.sessions = db_or_factory

    def _require_isolated_caller(self) -> None:
        if self.caller is not None and self.shared_connection:
            raise HTTPException(503, detail={"code": "MEMORY_SHARED_CONNECTION_UNSUPPORTED"})

    async def get_memory(self, owner_id, memory_id):
        self._require_isolated_caller()
        async with self.sessions() as db:
            return await owned_memory(db, owner_id, memory_id)

    async def create_memory(self, owner_id, conversation_id, source_turn_id, content, idempotency_key, *, guard=None):
        self._require_isolated_caller()
        return await self._write(
            owner_id, conversation_id, source_turn_id, content, idempotency_key, None, None, guard=guard
        )

    async def correct_memory(
        self,
        owner_id,
        memory_id,
        expected_version,
        content,
        idempotency_key,
        *,
        source_turn_id=None,
        conversation_id=None,
        guard=None,
    ):
        self._require_isolated_caller()
        previous = await self.get_memory(owner_id, memory_id)
        return await self._write(
            owner_id,
            conversation_id or previous.source_conversation_id,
            source_turn_id,
            content,
            idempotency_key,
            memory_id,
            expected_version,
            guard=guard,
        )

    async def _claim(
        self, owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version, fingerprint
    ):
        # The claim transaction owns authorization and source reads as well as
        # the reservation, so no caller transaction is touched.
        async with self.sessions() as db, db.begin():
            existing = await db.scalar(
                select(MemoryWriteRequest).where(
                    MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key
                )
            )
            if existing is not None:
                return _claim_value(existing), False
            await owned_conversation(db, owner_id, conversation_id)
            await _require_source_turn(db, source_turn_id, conversation_id)
            legacy = await db.scalar(select(Memory).where(Memory.owner_id == owner_id, Memory.request_key == key))
            if legacy is not None:
                legacy_fingerprint = _fingerprint(
                    legacy.source_conversation_id,
                    legacy.source_turn_id,
                    legacy.content,
                    legacy.supersedes_id,
                    legacy.version - 1 if legacy.supersedes_id else None,
                )
                if legacy_fingerprint != fingerprint:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            identifier = str(uuid4())
            values = dict(
                id=identifier,
                owner_id=owner_id,
                conversation_id=conversation_id,
                request_key=key,
                fingerprint=fingerprint,
                state="completed" if legacy else "reserved",
                result=asdict(memory_result(legacy)) if legacy else None,
                created_at=now(),
                updated_at=now(),
            )
            inserted = await insert_ignore(db, MemoryWriteRequest, values, index_elements=["owner_id", "request_key"])
            if inserted.rowcount != 1:
                existing = await db.scalar(
                    select(MemoryWriteRequest).where(
                        MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key
                    )
                )
                return _claim_value(existing), False
            if legacy is None:
                if hasattr(self.embedder, "require_configuration"):
                    self.embedder.require_configuration()
                if previous_id is not None:
                    previous = await db.get(Memory, previous_id)
                    if (
                        previous is None
                        or previous.owner_id != owner_id
                        or previous.status != "active"
                        or previous.version != expected_version
                    ):
                        raise HTTPException(409, detail={"code": "MEMORY_VERSION_CONFLICT"})
                # This helper never commits: claim and allowance become durable together.
                await reserve_in_transaction(db, "openai")
            row = await db.get(MemoryWriteRequest, identifier)
            return _claim_value(row), legacy is None

    def _replay(self, claim, fingerprint):
        if claim["fingerprint"] != fingerprint:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        if claim["state"] == "completed":
            return MemoryResult(**claim["result"])
        if claim["state"] in {"failed", "unknown"}:
            raise HTTPException(
                claim["error_status"] or 503, detail={"code": claim["error_code"] or "MEMORY_REQUEST_UNKNOWN"}
            )
        if claim["state"] == "reserved":
            age = (datetime.now(UTC) - datetime.fromisoformat(claim["created_at"])).total_seconds()
            if age >= CLAIM_TIMEOUT_SECONDS:
                # No automatic lease takeover: delivery after a process crash is uncertain.
                raise HTTPException(503, detail={"code": "MEMORY_REQUEST_UNKNOWN"})
            raise HTTPException(409, detail={"code": "MEMORY_REQUEST_IN_PROGRESS"})
        return None

    async def _mark_error(self, identifier, code, status=503, *, uncertain=False):
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(MemoryWriteRequest)
                .where(
                    MemoryWriteRequest.id == identifier,
                    MemoryWriteRequest.state.in_(["reserved", "embedded"]),
                )
                .values(
                    state="unknown" if uncertain else "failed", error_code=code, error_status=status, updated_at=now()
                )
            )

    async def _finalize(
        self,
        identifier,
        fingerprint,
        owner_id,
        conversation_id,
        source_turn_id,
        content,
        key,
        previous_id,
        expected_version,
        *,
        guard=None,
    ):
        async with self.sessions() as db, db.begin():
            # Short row-local write lock (also effective on SQLite), never held across inference.
            await db.execute(
                update(MemoryWriteRequest).where(MemoryWriteRequest.id == identifier).values(updated_at=now())
            )
            row = await db.get(MemoryWriteRequest, identifier)
            claim = _claim_value(row)
            if claim["state"] != "embedded":
                result = self._replay(claim, fingerprint)
                if result is not None:
                    return result
                raise HTTPException(503, detail={"code": "MEMORY_REQUEST_UNKNOWN"})
            if claim["fingerprint"] != fingerprint:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            await owned_conversation(db, owner_id, conversation_id)
            if source_turn_id is not None:
                source = await db.scalar(
                    select(Turn).where(Turn.id == source_turn_id, Turn.conversation_id == conversation_id)
                )
                if source is None:
                    raise HTTPException(404, detail={"code": "SOURCE_TURN_NOT_FOUND"})
            if guard is not None:
                # Fenced in this transaction: a worker whose lease was taken
                # over cannot commit the memory row it protects.
                await guard(db)
            version = 1
            if previous_id is not None:
                changed = await db.execute(
                    update(Memory)
                    .where(
                        Memory.id == previous_id,
                        Memory.owner_id == owner_id,
                        Memory.status == "active",
                        Memory.version == expected_version,
                    )
                    .values(status="superseded")
                )
                if changed.rowcount != 1:
                    raise HTTPException(409, detail={"code": "MEMORY_VERSION_CONFLICT"})
                version = expected_version + 1
            memory = Memory(
                id=str(uuid4()),
                owner_id=owner_id,
                # New rows always resolve a subject; only pre-migration rows stay NULL.
                subject=resolve_subject(content),
                content=content,
                embedding=list(validate_embedding(claim["embedding"])),
                embedding_model=EMBEDDING_MODEL,
                source_turn_id=source_turn_id,
                source_conversation_id=conversation_id,
                version=version,
                status="active",
                supersedes_id=previous_id,
                request_key=key,
            )
            db.add(memory)
            await db.flush()
            result = memory_result(memory)
            row.state, row.result, row.updated_at = "completed", asdict(result), now()
            return result

    async def _write(
        self, owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version, *, guard=None
    ):
        self._require_isolated_caller()
        _validate_write(content, key, previous_id, expected_version)
        if (
            self.caller_dialect == "sqlite"
            and self.caller is not None
            and (self.caller.new or self.caller.dirty or self.caller.deleted)
        ):
            # SQLite cannot co-exist with an independent writer. Never autoflush
            # or roll back caller work; reject before touching any ledger row.
            raise HTTPException(409, detail={"code": "MEMORY_CALLER_TRANSACTION_ACTIVE"})
        fingerprint = _fingerprint(conversation_id, source_turn_id, content, previous_id, expected_version)
        try:
            claim, winner = await self._claim(
                owner_id, conversation_id, source_turn_id, content, key, previous_id, expected_version, fingerprint
            )
        except OperationalError as exc:
            # SQLite cannot open an independent writer while the caller holds a
            # flushed write transaction. Reject with a typed error; nothing was
            # durably changed.
            if _sqlite_busy(exc):
                raise HTTPException(409, detail={"code": "MEMORY_CALLER_TRANSACTION_ACTIVE"}) from exc
            raise
        if not winner:
            result = self._replay(claim, fingerprint)
            if result is not None:
                return result
        else:
            await self._embed_claim(claim, content)
        try:
            return await self._finalize(
                claim["id"],
                fingerprint,
                owner_id,
                conversation_id,
                source_turn_id,
                content,
                key,
                previous_id,
                expected_version,
                guard=guard,
            )
        except HTTPException as exc:
            if exc.status_code in {400, 403, 404, 409, 422}:
                # Terminal finalization failures must be durable so a retry
                # returns the recorded outcome instead of re-running finalize.
                await self._mark_error(claim["id"], exc.detail["code"], exc.status_code)
            raise

    async def _embed_claim(self, claim, content):
        try:
            async with asyncio.timeout(EMBEDDING_TIMEOUT_SECONDS):
                if hasattr(self.embedder, "embed_reserved"):
                    vector = validate_embedding(await self.embedder.embed_reserved(content))
                else:
                    # Backwards-compatible deterministic test/embedder adapters must not reserve again.
                    async with self.sessions() as embed_db:
                        vector = validate_embedding(await self.embedder.embed(embed_db, content))
            async with self.sessions() as db, db.begin():
                await db.execute(
                    update(MemoryWriteRequest)
                    .where(MemoryWriteRequest.id == claim["id"], MemoryWriteRequest.state == "reserved")
                    .values(state="embedded", embedding=list(vector), updated_at=now())
                )
        except asyncio.CancelledError:
            await asyncio.shield(self._mark_error(claim["id"], "MEMORY_REQUEST_UNKNOWN", uncertain=True))
            raise
        except EmbeddingFailure as exc:
            await self._mark_error(claim["id"], exc.detail["code"], uncertain=exc.uncertain)
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            await self._mark_error(claim["id"], "MEMORY_REQUEST_UNKNOWN", uncertain=True)
            raise HTTPException(503, detail={"code": "MEMORY_REQUEST_UNKNOWN"}) from exc
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
            await self._mark_error(claim["id"], "EMBEDDING_RESPONSE_INVALID")
            raise HTTPException(503, detail={"code": "EMBEDDING_RESPONSE_INVALID"}) from exc

    async def list_memories(
        self,
        owner_id,
        *,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> MemoryListPage:
        """Owner-scoped stored-fact page; never scans another owner's rows.

        Pagination is ordered by ``updated_at`` then id so callers can page
        deterministically while corrections move a fact to the front. When a
        conversation is supplied, active exclusions and the next compiled
        context's bounded candidate view are projected onto the page.
        """
        if not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(offset, int) or offset < 0:
            raise HTTPException(422, detail={"code": "INVALID_MEMORY_QUERY"})
        self._require_isolated_caller()
        async with self.sessions() as db:
            if conversation_id is not None:
                await owned_conversation(db, owner_id, conversation_id)
            total = await db.scalar(select(func.count()).select_from(Memory).where(Memory.owner_id == owner_id))
            rows = list(
                (
                    await db.scalars(
                        select(Memory)
                        .where(Memory.owner_id == owner_id)
                        .order_by(Memory.updated_at.desc(), Memory.id.desc())
                        .offset(offset)
                        .limit(limit + 1)
                    )
                ).all()
            )
            has_more = len(rows) > limit
            rows = rows[:limit]
            excluded_ids: set[str] = set()
            included_ids: set[str] = set()
            if conversation_id is not None and rows:
                excluded_ids = set(
                    await db.scalars(
                        select(ContextExclusion.memory_id).where(
                            ContextExclusion.owner_id == owner_id,
                            ContextExclusion.conversation_id == conversation_id,
                            ContextExclusion.active.is_(True),
                        )
                    )
                )
                included_ids = set(
                    await db.scalars(
                        select(Memory.id)
                        .where(
                            Memory.owner_id == owner_id,
                            Memory.status == "active",
                            Memory.id.not_in(excluded_ids),
                        )
                        .order_by(Memory.updated_at.desc(), Memory.id.desc())
                        .limit(MEMORY_CANDIDATE_LIMIT)
                    )
                )
            items = tuple(
                MemoryListItem(
                    id=row.id,
                    content=row.content,
                    kind="budget" if _classify(row.content) else "memory",
                    version=row.version,
                    status=row.status,
                    source_conversation_id=row.source_conversation_id,
                    source_turn_id=row.source_turn_id,
                    supersedes_id=row.supersedes_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    excluded_in_conversation=(row.id in excluded_ids) if conversation_id is not None else None,
                    included_in_conversation=(
                        row.id in included_ids if conversation_id is not None and row.status == "active" else False
                    )
                    if conversation_id is not None
                    else None,
                )
                for row in rows
            )
            return MemoryListPage(items, int(total or 0), offset + limit if has_more else None)

    async def search_memories(self, owner_id, conversation_id, query, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or not 1 <= limit <= 20:
            raise HTTPException(422, detail={"code": "INVALID_MEMORY_QUERY"})
        self._require_isolated_caller()
        # Search owns its read and budget sessions; never rollback a caller's effects.
        async with self.sessions() as access:
            await owned_conversation(access, owner_id, conversation_id)
        async with self.sessions() as budget_db:
            vector = validate_embedding(await self.embedder.embed(budget_db, query))
        async with self.sessions() as db:
            excluded = await blocked_memory_ids(db, owner_id, conversation_id)
            statement = select(Memory).where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.embedding.is_not(None),
                Memory.id.not_in(excluded),
            )
            if db.bind.dialect.name == "postgresql":
                distance = Memory.embedding.cosine_distance(list(vector)).label("distance")
                rows = (
                    await db.execute(statement.add_columns(distance).order_by(distance, Memory.id).limit(limit))
                ).all()
                return tuple(memory_result(row, float(score)) for row, score in rows)
            rows = (await db.scalars(statement.order_by(Memory.id).limit(SEARCH_CANDIDATE_LIMIT))).all()
            ranked = sorted(
                ((cosine_distance(vector, validate_embedding(row.embedding)), row) for row in rows),
                key=lambda pair: (pair[0], pair[1].id),
            )
            return tuple(memory_result(row, float(score)) for score, row in ranked[:limit])
