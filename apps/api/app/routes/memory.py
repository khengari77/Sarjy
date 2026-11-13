"""Authenticated memory HTTP adapter; dependency providers are supplied by the app."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services.memory import MemoryResult, MemoryService


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    source_turn_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class MemoryCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)
    source_turn_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)


class MemoryListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    content: str
    kind: str
    version: int = Field(ge=1)
    status: str
    source_conversation_id: str
    source_turn_id: str | None
    supersedes_id: str | None
    created_at: str
    updated_at: str
    # Present only when the caller supplies a conversation scope. ``None``
    # means "not evaluated", never "stored but unused".
    excluded_in_conversation: bool | None = None
    included_in_conversation: bool | None = None


class MemoryList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MemoryListItem]
    total: int = Field(ge=0)
    next_offset: int | None


def memory_router(database, identity, embedder):
    router = APIRouter()

    @router.get("/v1/memories", response_model=MemoryList)
    async def list_memories(
        conversation_id: str | None = Query(default=None, min_length=1, max_length=100),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        user=Depends(identity),
        db=Depends(database),
    ):
        return await MemoryService(db, embedder).list_memories(
            user.id, limit=limit, offset=offset, conversation_id=conversation_id
        )

    @router.post("/v1/conversations/{conversation_id}/memories", response_model=MemoryResult, status_code=201)
    async def create(conversation_id: str, body: MemoryWrite, user=Depends(identity), db=Depends(database)):
        try:
            return await MemoryService(db, embedder).create_memory(
                user.id, conversation_id, body.source_turn_id, body.content, body.idempotency_key
            )
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc

    @router.post("/v1/memories/{memory_id}/correct", response_model=MemoryResult)
    async def correct(memory_id: str, body: MemoryCorrection, user=Depends(identity), db=Depends(database)):
        try:
            return await MemoryService(db, embedder).correct_memory(
                user.id,
                memory_id,
                body.expected_version,
                body.content,
                body.idempotency_key,
                source_turn_id=body.source_turn_id,
            )
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc

    @router.post("/v1/memories/search", response_model=tuple[MemoryResult, ...])
    async def search(body: MemoryQuery, user=Depends(identity), db=Depends(database)):
        try:
            return await MemoryService(db, embedder).search_memories(
                user.id, body.conversation_id, body.query, body.limit
            )
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc

    return router
