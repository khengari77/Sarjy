"""Context inspection, proposal, confirmation and undo endpoints."""

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.context_operations import (
    ContextOperationError,
    confirm_proposal,
    context_state,
    create_proposal,
    list_operations,
    undo_operation,
)


class ContextProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    keep: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=20)


class ContextConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal: str = Field(min_length=1, max_length=20000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ContextUndoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ContextDiff(BaseModel):
    added: list[str]
    removed: list[str]
    kept: list[str]
    revision_before: int
    revision_after: int


class ContextMatch(BaseModel):
    id: str
    content: str
    source_turn_id: str | None
    score: float
    kind: str


class ContextPreserved(BaseModel):
    id: str
    content: str
    reason: str


class ContextProposal(BaseModel):
    conversation_id: str
    query: str
    revision: int
    proposal: str
    matches: list[ContextMatch]
    preserved: list[ContextPreserved]
    diff: ContextDiff


class ContextConfirmation(BaseModel):
    operation_id: str
    status: str
    idempotent: bool
    excluded_ids: list[str]
    kept_ids: list[str]
    revision_before: int
    revision_after: int
    diff: ContextDiff


class ContextUndoResult(BaseModel):
    operation_id: str
    restored_operation_id: str
    status: str
    idempotent: bool
    restored_ids: list[str]
    revision_before: int
    revision_after: int
    diff: ContextDiff


class ContextOperationItem(BaseModel):
    id: str
    command_type: str
    outcome: str
    revision_before: int | None = None
    revision_after: int | None = None
    idempotency_key: str
    created_at: str
    diff: ContextDiff | None


class ContextOperationList(BaseModel):
    conversation_id: str
    revision: int
    operations: list[ContextOperationItem]


class ContextState(BaseModel):
    conversation_id: str
    revision: int
    excluded_ids: list[str]


def _context_error(exc: ContextOperationError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code})


def register_context_routes(app, *, config: Settings, database, identity, semantic_search):
    def proposal_secret() -> str:
        return config.context_proposal_secret or config.agent_service_secret

    @app.get("/v1/conversations/{conversation_id}/context", response_model=ContextState)
    async def get_context(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        try:
            return await context_state(db, owner_id=user.id, conversation_id=conversation_id)
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.get("/v1/conversations/{conversation_id}/context/operations", response_model=ContextOperationList)
    async def get_context_operations(
        conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)
    ):
        try:
            return await list_operations(db, owner_id=user.id, conversation_id=conversation_id)
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.post("/v1/conversations/{conversation_id}/context/proposals", response_model=ContextProposal)
    async def propose_context(
        conversation_id: str, body: ContextProposalInput, user=Depends(identity), db: AsyncSession = Depends(database)
    ):
        try:
            return await create_proposal(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                query=body.query,
                keep=body.keep,
                limit=body.limit,
                secret=proposal_secret(),
                search=semantic_search,
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.post("/v1/conversations/{conversation_id}/context/confirm", response_model=ContextConfirmation)
    async def confirm_context(
        conversation_id: str, body: ContextConfirmInput, user=Depends(identity), db: AsyncSession = Depends(database)
    ):
        try:
            return await confirm_proposal(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                proposal=body.proposal,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
                secret=proposal_secret(),
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.post("/v1/conversations/{conversation_id}/context/undo", response_model=ContextUndoResult)
    async def undo_context(
        conversation_id: str, body: ContextUndoInput, user=Depends(identity), db: AsyncSession = Depends(database)
    ):
        try:
            return await undo_operation(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                operation_id=body.operation_id,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc
