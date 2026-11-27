"""Authenticated memory HTTP adapter; dependency providers are supplied by the app."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services.authorization import owned_conversation
from app.services.memory import MemoryResult, MemoryService
from app.services.memory_auto import ChangeConflict, ChangeNotFound, undo_change
from app.services.memory_capture import MemoryCaptureError, pending_proposal, resolve_proposal
from app.services.memory_mode import load_effective_memory_mode
from app.services.memory_visibility import delete_memory, hide_memory, unhide_memory


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
    scope: Literal["global", "conversation"]
    source_conversation_id: str
    source_turn_id: str | None
    supersedes_id: str | None
    created_at: str
    updated_at: str
    # Present only when the caller supplies a conversation scope. ``None``
    # means "not evaluated", never "stored but unused".
    excluded_in_conversation: bool | None = None
    # Active and not blocked by exclusion lineage. Distinct from the next
    # compiled context's bounded candidate window below.
    candidate_eligible: bool | None = None
    # Membership in the compiler's bounded candidate set. This is still not a
    # guarantee of final prompt inclusion (history/tool budgets can trim it),
    # so the UI presents this as candidacy, never as "included".
    included_in_conversation: bool | None = None


class MemoryList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MemoryListItem]
    total: int = Field(ge=0)
    next_offset: int | None


class ChangeUndone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    label: str
    undoable: bool
    memory_id: str | None
    changed: bool


class MemoryVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: Literal["active", "hidden", "deleted"]
    changed: bool


class MemoryProposalReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int = Field(ge=1)
    content: str


class PendingMemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    fact: str
    expires_at: str
    replaces: MemoryProposalReplacement | None = None


class MemoryProposalPending(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending: PendingMemoryProposal | None


class MemoryProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: str = Field(min_length=1, max_length=100)
    decision: Literal["save", "decline", "replace"]
    # Required for replace: the exact visible fact the user was shown.
    target_id: str | None = Field(default=None, min_length=1, max_length=100)
    target_version: int | None = Field(default=None, ge=1)


class MemoryProposalDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    memory: MemoryResult | None = None
    idempotent: bool | None = None


def memory_router(database, identity, embedder):
    router = APIRouter()

    @router.get("/v1/memories", response_model=MemoryList)
    async def list_memories(
        conversation_id: str | None = Query(default=None, min_length=1, max_length=100),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        status: Literal["active", "hidden", "superseded"] | None = Query(default=None),
        scope: Literal["global", "conversation"] | None = Query(default=None),
        user=Depends(identity),
        db=Depends(database),
    ):
        return await MemoryService(db, embedder).list_memories(
            user.id,
            limit=limit,
            offset=offset,
            conversation_id=conversation_id,
            status=status,
            scope=scope,
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

    async def _visibility(operation, memory_id, user, db) -> MemoryVisibility:
        result = await operation(db, user.id, memory_id)
        await db.commit()
        return MemoryVisibility(id=result.id, status=result.status, changed=result.changed)

    @router.post("/v1/memories/{memory_id}/hide", response_model=MemoryVisibility)
    async def hide(memory_id: str, user=Depends(identity), db=Depends(database)):
        return await _visibility(hide_memory, memory_id, user, db)

    @router.post("/v1/memories/{memory_id}/unhide", response_model=MemoryVisibility)
    async def unhide(memory_id: str, user=Depends(identity), db=Depends(database)):
        return await _visibility(unhide_memory, memory_id, user, db)

    @router.delete("/v1/memories/{memory_id}", response_model=MemoryVisibility)
    async def delete(memory_id: str, user=Depends(identity), db=Depends(database)):
        return await _visibility(delete_memory, memory_id, user, db)

    return router


def memory_proposal_router(database, identity, embedder, *, secret: str = ""):
    router = APIRouter()

    @router.get("/v1/conversations/{conversation_id}/memory-proposal", response_model=MemoryProposalPending)
    async def get_memory_proposal(conversation_id: str, user=Depends(identity), db=Depends(database)):
        await owned_conversation(db, user.id, conversation_id)
        try:
            pending = await pending_proposal(db, owner_id=user.id, conversation_id=conversation_id, secret=secret)
        except MemoryCaptureError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code, **exc.details}) from exc
        return MemoryProposalPending(pending=pending)

    @router.post(
        "/v1/conversations/{conversation_id}/memory-proposal/decision",
        response_model=MemoryProposalDecisionResult,
    )
    async def decide_memory_proposal(
        conversation_id: str, body: MemoryProposalDecision, user=Depends(identity), db=Depends(database)
    ):
        await owned_conversation(db, user.id, conversation_id)
        mode = await load_effective_memory_mode(db, conversation_id)
        try:
            return await resolve_proposal(
                db,
                MemoryService(db, embedder),
                owner_id=user.id,
                conversation_id=conversation_id,
                proposal_id=body.proposal_id,
                decision=body.decision,
                secret=secret,
                mode=mode,
                target_id=body.target_id,
                target_version=body.target_version,
            )
        except MemoryCaptureError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code, **exc.details}) from exc
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc

    return router


def changes_router(database, identity):
    """Undo for changes made without a spoken question (auto-saved memories)."""
    router = APIRouter()

    @router.post("/v1/changes/{change_id}/undo", response_model=ChangeUndone)
    async def undo(change_id: str, user=Depends(identity), db=Depends(database)):
        """Undo one auto-saved change by id. Idempotent; 409 if the memory was changed since."""
        try:
            return await undo_change(db, owner_id=user.id, change_id=change_id)
        except ChangeNotFound:
            raise HTTPException(404, detail={"code": "CHANGE_NOT_FOUND"}) from None
        except ChangeConflict:
            raise HTTPException(409, detail={"code": "CHANGE_SUPERSEDED"}) from None

    return router
