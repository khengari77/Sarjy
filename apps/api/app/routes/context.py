"""Context inspection, proposal, confirmation and undo endpoints."""

from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.context_blocks import DEFAULT_LIMIT, MAX_LIMIT, list_context_blocks
from app.services.context_operations import (
    ContextOperationError,
    confirm_proposal,
    context_state,
    create_exclusion_proposal,
    create_proposal,
    list_operations,
    undo_operation,
)
from app.services.turn_context_receipt import get_turn_context_receipt


class ContextProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    keep: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=20)


class ContextExclusionProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


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


class ContextAmbiguity(BaseModel):
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
    ambiguous: list[ContextAmbiguity]
    requires_clarification: bool
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


class ContextBlock(BaseModel):
    id: str
    kind: str
    role: str
    text: str
    truncated: bool
    estimated_tokens: int
    state: str
    reason: str | None


class ContextBlockCounts(BaseModel):
    included: int
    excluded: int
    blocked: int
    outside_window: int
    not_sent: int
    total: int


class ContextBlocks(BaseModel):
    conversation_id: str
    revision: int
    token_estimate_method: str
    included_tokens_estimate: int
    counts: ContextBlockCounts
    limit: int
    offset: int
    has_more: bool
    blocks: list[ContextBlock]


class TurnContextIncluded(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["memory", "tool", "history"]
    text: str
    trust: Literal["data", "conversation"]
    # True when the live policy now blocks this record: its id stays, its text is not shown.
    withheld: bool = False


class TurnContextExcluded(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reason: Literal[
        "hidden",
        "excluded_in_conversation",
        "deleted",
        "derived",
        "source_of_blocked_memory",
        "restates_forgotten_fact",
    ]


class TurnContextBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    used_chars: int
    limit_chars: int


class TurnContextDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    added: list[str]
    removed: list[str]


class TurnContextReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    revision: int
    prompt_sha256: str = Field(
        description="Digest of this receipt's own content (revision, ids, shown text), not of the compiled prompt."
    )
    history_count: int
    included: list[TurnContextIncluded]
    excluded: list[TurnContextExcluded]
    budget: TurnContextBudget
    previous_turn_id: str | None
    diff: TurnContextDiff


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
                exclude=body.exclude,
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


def register_exclusion_routes(app, *, config: Settings, database, identity):
    """Exact-id preview for the transparent memory explorer.

    Kept separate from the search-driven proposal routes so each registration
    function stays within the project's complexity budget.
    """

    def proposal_secret() -> str:
        return config.context_proposal_secret or config.agent_service_secret

    @app.post("/v1/conversations/{conversation_id}/context/exclusions", response_model=ContextProposal)
    async def propose_exclusion(
        conversation_id: str,
        body: ContextExclusionProposalInput,
        user=Depends(identity),
        db: AsyncSession = Depends(database),
    ):
        try:
            return await create_exclusion_proposal(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                memory_ids=body.memory_ids,
                secret=proposal_secret(),
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.get(
        "/v1/conversations/{conversation_id}/turns/{turn_id}/context",
        response_model=TurnContextReceipt,
    )
    async def get_turn_context(
        conversation_id: str,
        turn_id: str,
        user=Depends(identity),
        db: AsyncSession = Depends(database),
    ):
        return await get_turn_context_receipt(db, owner_id=user.id, conversation_id=conversation_id, turn_id=turn_id)

    @app.get("/v1/conversations/{conversation_id}/context/blocks", response_model=ContextBlocks)
    async def get_context_blocks(
        conversation_id: str,
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0),
        user=Depends(identity),
        db: AsyncSession = Depends(database),
    ):
        try:
            return await list_context_blocks(
                db, owner_id=user.id, conversation_id=conversation_id, limit=limit, offset=offset
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc
