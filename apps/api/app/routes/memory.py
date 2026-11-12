"""Authenticated memory HTTP adapter; dependency providers are supplied by the app."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services.memory import MemoryResult, MemoryService


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=4000)
    source_turn_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class MemoryCorrection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)
    source_turn_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)


def memory_router(database, identity, embedder):
    router = APIRouter()

    @router.post('/v1/conversations/{conversation_id}/memories', response_model=MemoryResult, status_code=201)
    async def create(conversation_id: str, body: MemoryWrite, user=Depends(identity), db=Depends(database)):
        try:
            return await MemoryService(db,embedder).create_memory(user.id,conversation_id,body.source_turn_id,body.content,body.idempotency_key)
        except ValueError as exc:
            raise HTTPException(429 if str(exc)=='DAILY_BUDGET_EXHAUSTED' else 422,detail={'code':str(exc)}) from exc

    @router.post('/v1/memories/{memory_id}/correct', response_model=MemoryResult)
    async def correct(memory_id: str, body: MemoryCorrection, user=Depends(identity), db=Depends(database)):
        try:
            return await MemoryService(db,embedder).correct_memory(user.id,memory_id,body.expected_version,body.content,body.idempotency_key,source_turn_id=body.source_turn_id)
        except ValueError as exc:
            raise HTTPException(429 if str(exc)=='DAILY_BUDGET_EXHAUSTED' else 422,detail={'code':str(exc)}) from exc

    @router.post('/v1/memories/search', response_model=tuple[MemoryResult,...])
    async def search(body: MemoryQuery, user=Depends(identity), db=Depends(database)):
        try:
            return await MemoryService(db,embedder).search_memories(user.id,body.conversation_id,body.query,body.limit)
        except ValueError as exc:
            raise HTTPException(429 if str(exc)=='DAILY_BUDGET_EXHAUSTED' else 422,detail={'code':str(exc)}) from exc

    return router
