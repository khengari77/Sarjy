"""Worker-only endpoints for bootstrap, turns, weather and budget reservations."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import and_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.commands import CommandResult, parse_exact_command
from app.domain.context import ContextCandidate, compile_turn_context, validate_assembly
from app.domain.contracts import (
    MemoryRecord,
    MemorySource,
    ModelRequest,
    SetMemoryMode,
    UserSettings,
    resolve_response_policy,
)
from app.domain.intro import intro_for
from app.domain.memory_policy import Decision, Sensitivity, classify_sensitivity, plan_for_proposal
from app.domain.policy import normalize_pipeline, pipeline_instructions
from app.domain.secrets import secret_matches
from app.domain.tool_operations import ToolOperationState, terminal_state
from app.domain.weather_permissions import decide_weather_request
from app.infrastructure.database import Conversation, Memory, Turn, TurnMetric, UserSetting, VoiceSession
from app.infrastructure.upsert import insert_ignore
from app.routes.wordle_tools import register_wordle_tool_routes
from app.services.budget import reserve as reserve_development_budget
from app.services.commands import command_request_id, handle_finalized_utterance
from app.services.context_assembly import location_evidence, read_context_inputs
from app.services.memory import MemoryService
from app.services.memory_auto import auto_commit_memory
from app.services.memory_capture import (
    MemoryCaptureError,
    confirm_memory,
    load_proposal,
    proposal_summary,
    propose_memory,
    record_proposal,
)
from app.services.memory_tools import memory_forget_tool, memory_propose_tool, memory_recall_tool
from app.services.titles import generate_conversation_title
from app.services.tool_operations import (
    ToolOperationError,
    claim_tool_operation,
    complete_tool_operation,
    fail_tool_operation,
    unknown_tool_operation,
)
from app.services.weather import WeatherError, get_current_weather


class BudgetReservation(BaseModel):
    provider: Literal["openai", "deepgram_tts", "deepseek"]
    characters: int = Field(default=0, ge=0, le=500)


class AgentInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=12000)
    # Whether the Wordle popup is open, reported by the worker; None (an older worker) keeps the full Wordle rules.
    # A retried turn/start replays the first attempt's snapshot, so its rules keep that attempt's popup state.
    wordle_open: bool | None = None


class MemoryConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=20000)
    decision: Literal["save", "discard", "replace"]


class MemoryAuto(BaseModel):
    token: str = Field(min_length=1, max_length=20000)


class AgentOutput(BaseModel):
    turn_id: str
    text: str = Field(max_length=20000)
    status: Literal["completed", "interrupted", "failed"]


class WeatherInput(BaseModel):
    turn_id: str
    request_id: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=120)
    country: str | None = Field(default=None, max_length=120)


class TurnStageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_start_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    command_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    llm_reserve_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    tts_reserve_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    turn_start_server_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    turn_start_db_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    api_connect_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    api_headers_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    api_body_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    command_server_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    command_db_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    command_api_headers_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    model_first_chunk_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    release_wait_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    reassurance_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    first_answer_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_turn_start_begin_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_turn_start_end_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_command_begin_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_command_end_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_model_request_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_model_first_chunk_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_tool_start_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_tool_end_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    t_tts_first_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    prompt_cache_hit_tokens: StrictInt | None = Field(default=None, ge=0, le=2_000_000)
    prompt_cache_miss_tokens: StrictInt | None = Field(default=None, ge=0, le=2_000_000)
    prompt_cache_hit_tokens_2: StrictInt | None = Field(default=None, ge=0, le=2_000_000)
    prompt_cache_miss_tokens_2: StrictInt | None = Field(default=None, ge=0, le=2_000_000)
    request_bytes: StrictInt | None = Field(default=None, ge=0, le=1_000_000)
    provider_connect_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    provider_headers_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    loop_lag_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    predicted_prefix_hit_pct: StrictInt | None = Field(default=None, ge=0, le=100)


class TurnMetricsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(max_length=36)
    stt_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    eot_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    llm_ttft_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    tts_ttfb_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    ttfa_ms: StrictInt | None = Field(default=None, ge=0, le=120000)
    stages: TurnStageMetrics = Field(default_factory=TurnStageMetrics)


class MemoryToolBase(BaseModel):
    turn_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)


class MemoryProposeInput(MemoryToolBase):
    fact: str = Field(min_length=1, max_length=1000)


class MemoryQueryInput(MemoryToolBase):
    query: str = Field(min_length=1, max_length=500)


class CommandInput(BaseModel):
    turn_id: str = Field(min_length=1, max_length=100)


class MemoryOpAnswer(BaseModel):
    choice: str
    probability: float


class CommandResponse(BaseModel):
    command: CommandResult | None = None
    # Jev's memory_op answer from the same single classification call (absent when none was made).
    memory_op: MemoryOpAnswer | None = None


def _require_agent_secret(request: Request, config: Settings) -> None:
    secret = request.headers.get("x-agent-secret", "")
    if not secret_matches(secret, config.agent_service_secret):
        raise HTTPException(401, detail={"code": "WORKER_AUTH_REQUIRED"})


def _session_window(allow_ended: bool) -> tuple[tuple[str, ...], str]:
    statuses = ("starting", "active", "ended") if allow_ended else ("starting", "active")
    cutoff = datetime.now(UTC) - timedelta(seconds=60 if allow_ended else 0)
    return statuses, cutoff.isoformat()


def _session_is_active(voice: VoiceSession | None, room_name: str | None, *, allow_ended: bool) -> bool:
    if voice is None:
        return False
    statuses, cutoff = _session_window(allow_ended)
    return voice.room_name == room_name and voice.status in statuses and voice.expires_at > cutoff


def _session_guard(config: Settings):
    async def worker_session(request: Request, db: AsyncSession, allow_ended: bool = False):
        _require_agent_secret(request, config)
        voice = await db.get(VoiceSession, request.headers.get("x-session-id", ""))
        if not _session_is_active(voice, request.headers.get("x-room-name"), allow_ended=allow_ended):
            raise HTTPException(403, detail={"code": "SESSION_NOT_ACTIVE"})
        return voice

    return worker_session


PENDING_MEMORY_INSTRUCTION = (
    "A memory candidate is pending for this turn. Ask the user to confirm or decline before it is saved. "
    "Do not claim it is saved until confirmation succeeds."
)
SENSITIVE_MEMORY_INSTRUCTION = (
    "The user's last message contains something sensitive (such as a password or a secret code) that Sarjy never "
    "saves. Do not claim it was saved and do not repeat it; briefly say you can't keep details like that."
)
SENSITIVE_TOOL_NOTE = (
    "That is sensitive (a password or secret) and is never saved. Nothing was saved. Do not repeat it; tell the "
    "user briefly that you can't keep details like that."
)


def register_worker_routes(
    app, *, config: Settings, database, sessions, embedder, catalog, approved_voice_ids=None, wordle_source=None
):
    guard = _session_guard(config)
    _register_budget_routes(app, config=config, database=database)
    _register_warm_route(app, config=config, database=database)
    _register_session_routes(app, database=database, guard=guard)
    _register_turn_routes(app, config=config, database=database, guard=guard, sessions=sessions)
    _register_command_routes(
        app,
        config=config,
        database=database,
        guard=guard,
        catalog=catalog,
        approved_voice_ids=approved_voice_ids,
        secret=_memory_secret(config),
    )
    _register_tool_routes(app, config=config, database=database, guard=guard)
    _register_memory_tool_routes(app, config=config, database=database, guard=guard)
    _register_memory_routes(app, config=config, database=database, guard=guard, embedder=embedder)
    _register_auto_memory_route(app, config=config, database=database, guard=guard, embedder=embedder)
    if wordle_source is not None:

        async def run_tool(db, request, body, name, arguments, effect):
            return await _run_memory_tool(guard, db, request, body, name, arguments, effect)

        register_wordle_tool_routes(app, config=config, database=database, run_tool=run_tool, source=wordle_source)


def _memory_secret(config: Settings) -> str:
    return config.context_proposal_secret or config.agent_service_secret


def _memory_proposal(
    config: Settings, *, owner_id: str, conversation_id: str, source_turn_id: str, text: str, strict: bool = False
):
    secret = _memory_secret(config)
    if not secret:
        return None
    # "Remember things automatically" is a memory-mode command, not a fact to remember.
    if isinstance(parse_exact_command(text), SetMemoryMode):
        return None
    try:
        return propose_memory(
            text,
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=source_turn_id,
            secret=secret,
            strict=strict,
        )
    except MemoryCaptureError:
        return None


def _context_instructions(
    config: Settings,
    pipeline,
    *,
    owner_id: str,
    conversation_id: str,
    turn,
    strict_capture: bool = False,
    memory_mode: str = "auto",
    wordle_open: bool | None = None,
) -> tuple[str, dict | None]:
    # With memory off nothing is ever proposed, so there is no question to ask and nothing to arm.
    proposal = (
        None
        if memory_mode == "off"
        else _memory_proposal(
            config,
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=turn.id,
            text=turn.text,
            strict=strict_capture,
        )
    )
    sensitive = proposal is not None and classify_sensitivity(proposal["text"]) is Sensitivity.CREDENTIAL
    if sensitive:
        # A credential creates no proposal at all, so the sensitive text is never recorded as one.
        proposal = None
    instructions = pipeline_instructions(pipeline, wordle_open=wordle_open)
    if proposal is not None:
        instructions = f"{instructions}\n\n{PENDING_MEMORY_INSTRUCTION}"
    elif sensitive:
        # Without this the model may claim it saved the secret; nothing was proposed or recorded.
        instructions = f"{instructions}\n\n{SENSITIVE_MEMORY_INSTRUCTION}"
    return instructions, proposal


async def _replayed_proposal(db: AsyncSession, *, owner_id: str, turn) -> dict | None:
    """Return the proposal recorded for the turn and its authoritative state.

    A replay must never mint a fresh confirmation id. ``memory_write_requests``
    is the state authority; the turn snapshot only carries the lookup key.
    """
    stored = (turn.snapshot or {}).get("memory_proposal")
    if not isinstance(stored, dict):
        return None
    confirmation_id = stored.get("confirmation_id")
    if not isinstance(confirmation_id, str):
        return None
    proposal, state = await load_proposal(db, owner_id=owner_id, confirmation_id=confirmation_id)
    if proposal is None:
        return None
    if state in {"saved", "discarded"}:
        return {**proposal, "token": None, "state": state, "outcome": state}
    if state != "proposed":
        # In-flight claim: nothing writable may be handed back.
        return {**proposal, "token": None, "state": state}
    return proposal


async def _replayed_turn_response(db: AsyncSession, *, owner_id: str, turn):
    return {
        "turn_id": turn.id,
        "context": turn.snapshot,
        "memory_proposal": await _replayed_proposal(db, owner_id=owner_id, turn=turn),
    }


def _refuse_unsaveable(token: str, config: Settings, owner_id: str, conversation_id: str, row) -> None:
    """A credential is never saved in any mode, so even a spoken "yes" cannot confirm one."""
    try:
        _, text, explicit, negated = proposal_summary(token, _memory_secret(config), owner_id, conversation_id)
    except MemoryCaptureError:
        return  # confirm_memory reports an invalid token itself
    mode = row.memory_mode if row is not None else "auto"
    if plan_for_proposal(mode, text, explicit=explicit, negated=negated) is Decision.SKIP:
        raise HTTPException(409, detail={"code": "MEMORY_SENSITIVE"})


def _register_memory_routes(app, *, config: Settings, database, guard, embedder):
    @app.post("/internal/agent/memory/confirm")
    async def confirm_memory_route(body: MemoryConfirm, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db, allow_ended=True)
        conversation = await db.get(Conversation, voice.conversation_id)
        if conversation is None:
            raise HTTPException(404)
        if body.decision != "discard":
            # A proposal armed before the user turned memory off must not be savable afterwards.
            row = await db.get(UserSetting, conversation.owner_id)
            if row is not None and row.memory_mode == "off":
                raise HTTPException(409, detail={"code": "MEMORY_OFF"})
            _refuse_unsaveable(body.token, config, conversation.owner_id, voice.conversation_id, row)
        try:
            return await confirm_memory(
                db,
                MemoryService(db, embedder),
                token=body.token,
                decision=body.decision,
                owner_id=conversation.owner_id,
                conversation_id=voice.conversation_id,
                source_turn_id=None,
                secret=_memory_secret(config),
            )
        except MemoryCaptureError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code, **exc.details}) from exc
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc


def _register_auto_memory_route(app, *, config: Settings, database, guard, embedder):
    @app.post("/internal/agent/memory/auto")
    async def auto_memory_route(body: MemoryAuto, request: Request, db: AsyncSession = Depends(database)):
        """Commit a proposal without a spoken question. Only valid in ``auto`` mode and only when the policy says save."""
        voice = await guard(request, db, allow_ended=True)
        conversation = await db.get(Conversation, voice.conversation_id)
        if conversation is None:
            raise HTTPException(404)
        row = await db.get(UserSetting, conversation.owner_id)
        mode = row.memory_mode if row is not None else "auto"
        if mode == "off":
            raise HTTPException(409, detail={"code": "MEMORY_OFF"})
        try:
            return await auto_commit_memory(
                db,
                MemoryService(db, embedder),
                token=body.token,
                owner_id=conversation.owner_id,
                conversation_id=voice.conversation_id,
                secret=_memory_secret(config),
                mode=mode,
            )
        except MemoryCaptureError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code, **exc.details}) from exc
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc


def _register_warm_route(app, *, config: Settings, database):
    @app.post("/internal/agent/warm")
    async def warm(request: Request, background: BackgroundTasks, db: AsyncSession = Depends(database)):
        """Best-effort latency warm-up; no turn, budget reservation or session state is involved."""
        _require_agent_secret(request, config)
        await db.execute(text("SELECT 1"))
        classifier = getattr(app.state, "ui_classifier", None)
        warm_jev = getattr(classifier, "warm", None)
        if callable(warm_jev):
            background.add_task(_warm_jev, warm_jev)
        return {"ok": True}


async def _warm_jev(warm) -> None:
    try:
        await warm()
    except Exception:
        logging.getLogger(__name__).debug("jev warm-up failed", exc_info=True)


def _register_budget_routes(app, *, config: Settings, database):
    @app.post("/internal/development/reserve")
    async def development_reservation(body: BudgetReservation, request: Request, db: AsyncSession = Depends(database)):
        if not secret_matches(request.headers.get("x-agent-secret", ""), config.agent_service_secret):
            raise HTTPException(401)
        if body.provider == "deepseek" and not config.deepseek_budget_enabled:
            return {
                "provider": "deepseek",
                "enabled": False,
                "reserved_units": 0,
                "remaining_units": None,
                "unit": "micro_usd",
            }
        overrides = (
            {"units": config.deepseek_request_micro_usd, "limit": config.deepseek_daily_micro_usd}
            if body.provider == "deepseek"
            else {"limit": config.deepgram_daily_characters}
            if body.provider == "deepgram_tts"
            else {}
        )
        try:
            return await reserve_development_budget(db, body.provider, body.characters, **overrides)
        except ValueError as exc:
            raise HTTPException(429, detail={"code": str(exc)}) from exc


def _register_session_routes(app, *, database, guard):
    @app.post("/internal/agent/bootstrap")
    async def bootstrap(request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db)
        changed = await db.execute(
            update(VoiceSession)
            .where(
                VoiceSession.id == voice.id,
                VoiceSession.status.in_(["starting", "active"]),
                VoiceSession.expires_at > datetime.now(UTC).isoformat(),
            )
            .values(status="active")
        )
        await db.commit()
        if not changed.rowcount:
            raise HTTPException(403, detail={"code": "SESSION_NOT_ACTIVE"})
        pipeline = normalize_pipeline(voice.pipeline)
        row = await db.get(UserSetting, voice.owner_id)
        intro = intro_for(
            pipeline.get("llm", {}).get("language", "en"),
            seen=row is not None and row.intro_seen_at is not None,
            tts_provider=(pipeline.get("tts") or {}).get("provider"),
        )
        return {
            "pipeline": pipeline,
            "instructions": pipeline_instructions(pipeline),
            "expires_at": voice.expires_at,
            "intro": intro,
        }

    @app.post("/internal/agent/intro-played")
    async def intro_played(request: Request, db: AsyncSession = Depends(database)):
        """Worker confirmation that the full introduction played to the end. Idempotent."""
        voice = await guard(request, db)
        row = await db.get(UserSetting, voice.owner_id)
        if row is None:
            row = UserSetting(owner_id=voice.owner_id)
            db.add(row)
        if row.intro_seen_at is None:
            row.intro_seen_at = datetime.now(UTC).isoformat()
            await db.commit()
        return {"ok": True}


async def _record_turn_metrics(db: AsyncSession, voice: VoiceSession, body: TurnMetricsInput):
    turn = await db.get(Turn, body.turn_id)
    if not turn or turn.conversation_id != voice.conversation_id or turn.role != "user":
        raise HTTPException(404)
    inserted = await insert_ignore(
        db,
        TurnMetric,
        {
            "id": str(uuid4()),
            "turn_id": turn.id,
            "stt_ms": body.stt_ms,
            "eot_ms": body.eot_ms,
            "llm_ttft_ms": body.llm_ttft_ms,
            "tts_ttfb_ms": body.tts_ttfb_ms,
            "ttfa_ms": body.ttfa_ms,
            "usage": {"stages": body.stages.model_dump(exclude_none=True)},
        },
        index_elements=["turn_id"],
    )
    await db.commit()
    return {"recorded": inserted.rowcount == 1}


def _compile_turn_context(
    *, revision, instructions, history, memories, tool_results, current_turn, excluded_ids, pipeline
):
    try:
        return compile_turn_context(
            revision=revision,
            instructions=instructions,
            history=history,
            memories=memories,
            tool_results=tool_results,
            current_turn=current_turn,
            excluded_ids=excluded_ids,
            pipeline=pipeline,
        )
    except ValueError as exc:
        if str(exc) == "CONTEXT_BUDGET_EXCEEDED":
            raise HTTPException(413, detail={"code": "CONTEXT_BUDGET_EXCEEDED"}) from exc
        raise


async def _model_request_memories(db: AsyncSession, owner_id: str, included_ids, candidates) -> list[Memory]:
    """The memory rows behind the compiled prompt: those the candidate read loaded, plus any it did not supply."""
    rows = [candidates.rows[memory_id] for memory_id in included_ids if memory_id in candidates.rows]
    unread = [
        candidate.id
        for candidate in candidates.included
        if candidate.origin == "memory" and candidate.id in included_ids and candidate.id not in candidates.rows
    ]
    if unread:
        rows += (
            await db.scalars(
                select(Memory).where(Memory.owner_id == owner_id, Memory.id.in_(unread), Memory.status == "active")
            )
        ).all()
    return rows


async def _locked_turn_context(db: AsyncSession, conversation_id: str, request_id: str):
    """The conversation (row-locked), this request's existing turn and the owner's settings, in one statement."""
    locked = (
        await db.execute(
            select(Conversation, Turn, UserSetting)
            .outerjoin(Turn, and_(Turn.conversation_id == Conversation.id, Turn.request_id == request_id))
            .outerjoin(UserSetting, UserSetting.owner_id == Conversation.owner_id)
            .where(Conversation.id == conversation_id)
            .with_for_update(of=Conversation)
        )
    ).one_or_none()
    if locked is None:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return locked


async def _locked_turn_context_for_session(
    db: AsyncSession, session_id: str, request_id: str, *, room_name: str | None
):
    """The locked context plus the active session, in one statement; None when any precondition fails.

    The lock target stays the conversation only, and the session preconditions are
    part of the query so an inactive session never takes the conversation lock.
    """
    statuses, cutoff = _session_window(False)
    return (
        await db.execute(
            select(Conversation, Turn, UserSetting, VoiceSession)
            .join(VoiceSession, VoiceSession.conversation_id == Conversation.id)
            .outerjoin(Turn, and_(Turn.conversation_id == Conversation.id, Turn.request_id == request_id))
            .outerjoin(UserSetting, UserSetting.owner_id == Conversation.owner_id)
            .where(
                VoiceSession.id == session_id,
                VoiceSession.room_name == room_name,
                VoiceSession.status.in_(statuses),
                VoiceSession.expires_at > cutoff,
            )
            .with_for_update(of=Conversation)
        )
    ).one_or_none()


async def _turn_start_context(db: AsyncSession, request: Request, request_id: str, *, guard, config: Settings):
    """Resolve the locked turn context and its session in one statement when the session is live.

    A missing or inactive session falls back to the guard so its 401/403 codes and
    the conversation-not-found 404 are unchanged.
    """
    _require_agent_secret(request, config)
    room_name = request.headers.get("x-room-name")
    locked = await _locked_turn_context_for_session(
        db, request.headers.get("x-session-id", ""), request_id, room_name=room_name
    )
    if locked is not None and _session_is_active(locked[3], room_name, allow_ended=False):
        return locked
    voice = await guard(request, db)
    conversation, existing, settings_row = await _locked_turn_context(db, voice.conversation_id, request_id)
    return conversation, existing, settings_row, voice


def _register_turn_routes(app, *, config: Settings, database, guard, sessions):
    @app.post("/internal/agent/turn/start")
    async def turn_start(body: AgentInput, request: Request, db: AsyncSession = Depends(database)):
        # Serializes worker retries and preserves the conversation's authoritative order.
        conversation, existing, settings_row, voice = await _turn_start_context(
            db, request, body.request_id, guard=guard, config=config
        )
        if existing:
            return await _replayed_turn_response(
                db,
                owner_id=conversation.owner_id,
                turn=existing,
            )
        turn = Turn(
            id=str(uuid4()),
            conversation_id=voice.conversation_id,
            request_id=body.request_id,
            role="user",
            text=body.text,
            status="completed",
        )
        pipeline = normalize_pipeline(voice.pipeline)
        instructions, proposal = _context_instructions(
            config,
            pipeline,
            memory_mode=settings_row.memory_mode if settings_row is not None else "auto",
            owner_id=conversation.owner_id,
            conversation_id=voice.conversation_id,
            turn=turn,
            # With the model classifier available, regexes only keep the exact "remember that ..." fast path.
            strict_capture=getattr(app.state, "ui_classifier", None) is not None,
            wordle_open=body.wordle_open,
        )
        inputs = await read_context_inputs(
            db, owner_id=conversation.owner_id, conversation_id=voice.conversation_id, combined_read=True
        )
        compiled = _compile_turn_context(
            revision=conversation.revision,
            instructions=instructions,
            history=inputs.history,
            memories=(*inputs.memories.included, *inputs.retractions),
            tool_results=inputs.tool_results,
            current_turn=ContextCandidate(id=turn.id, role="user", text=turn.text, origin="history"),
            excluded_ids=inputs.excluded_ids,
            pipeline=pipeline,
        )
        # The weather decision is frozen with the snapshot: the tool call later
        # authorizes against this revision, never against live or stale state.
        turn.snapshot = {
            **compiled.to_snapshot(),
            "weather_policy": inputs.weather_policy(),
            # Lookup key only. The proposal row is the authoritative state.
            "memory_proposal": (
                {
                    "confirmation_id": proposal["confirmation_id"],
                    "confirmation_revision": proposal["confirmation_revision"],
                }
                if proposal is not None
                else None
            ),
        }
        settings_value = (
            UserSettings()
            if settings_row is None
            else UserSettings(
                speaking_speed=settings_row.speaking_speed,
                captions_visible=settings_row.captions_visible,
                approved_voice_id=settings_row.approved_voice_id,
                response_preference=settings_row.response_preference,
                memory_mode=settings_row.memory_mode,
                revision=settings_row.revision,
            )
        )
        memory_rows = await _model_request_memories(db, conversation.owner_id, compiled.included_ids, inputs.memories)
        model_request = ModelRequest(
            turn_id=turn.id,
            attempt_id=str(uuid4()),
            instructions=instructions,
            selection=compiled.selection,
            messages=compiled.messages,
            response_policy=resolve_response_policy(pipeline["response_preference"], pipeline["response_tier"]),
            settings=settings_value,
            settings_revision=settings_value.revision,
            language=pipeline.get("llm", {}).get("language", pipeline.get("language", "en")),
            memories=tuple(
                MemoryRecord(
                    id=row.id,
                    owner_id=row.owner_id,
                    content=row.content,
                    source=MemorySource(
                        turn_id=row.source_turn_id,
                        conversation_id=row.source_conversation_id,
                        created_at=row.created_at,
                    ),
                    version=row.version,
                )
                for row in memory_rows
            ),
        )
        turn.snapshot["model_request"] = model_request.model_dump(mode="json")

        await record_proposal(
            db,
            owner_id=conversation.owner_id,
            conversation_id=voice.conversation_id,
            proposal=proposal,
        )
        validate_assembly(conversation.revision, compiled.manifest, turn.snapshot)
        conversation_id = voice.conversation_id
        owner_id = conversation.owner_id
        db.add(turn)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(Turn).where(Turn.conversation_id == conversation_id, Turn.request_id == body.request_id)
            )
            if not existing:
                raise
            return await _replayed_turn_response(
                db,
                owner_id=owner_id,
                turn=existing,
            )
        return {"turn_id": turn.id, "context": turn.snapshot, "memory_proposal": proposal}

    @app.post("/internal/agent/turn/finalize")
    async def finalize(
        body: AgentOutput,
        request: Request,
        background: BackgroundTasks,
        db: AsyncSession = Depends(database),
    ):
        voice = await guard(request, db, allow_ended=True)
        user_turn = await db.get(Turn, body.turn_id)
        if not user_turn or user_turn.conversation_id != voice.conversation_id:
            raise HTTPException(404)
        key = "reply:" + body.turn_id
        existing = await db.scalar(
            select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == key)
        )
        if existing:
            return {"id": existing.id}
        turn = Turn(
            id=str(uuid4()),
            conversation_id=voice.conversation_id,
            request_id=key,
            role="assistant",
            text=body.text,
            status=body.status,
        )
        record_ids = (user_turn.snapshot or {}).get("included_record_ids")
        if isinstance(record_ids, list) and all(isinstance(value, str) for value in record_ids):
            turn.snapshot = {"derivation": {"turn_id": user_turn.id, "record_ids": record_ids}}
        conversation_id = voice.conversation_id
        db.add(turn)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(Turn).where(Turn.conversation_id == conversation_id, Turn.request_id == key)
            )
            if not existing:
                raise
            return {"id": existing.id}
        # The exchange is durable now; title generation cannot affect this reply.
        background.add_task(
            generate_conversation_title,
            sessions,
            conversation_id,
            api_key=config.openai_api_key
            if config.default_llm_provider == "openai_direct"
            else config.deepseek_api_key,
            provider=config.default_llm_provider,
            reserve_budget=config.deepseek_budget_enabled,
            budget_units=config.deepseek_request_micro_usd,
            budget_limit=config.deepseek_daily_micro_usd,
        )
        return {"id": turn.id}

    @app.post("/internal/agent/turn/metrics")
    async def turn_metrics(body: TurnMetricsInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db, allow_ended=True)
        return await _record_turn_metrics(db, voice, body)


def _register_command_routes(app, *, config, database, guard, catalog, approved_voice_ids=None, secret=""):
    @app.post("/internal/agent/command", response_model=CommandResponse)
    async def finalized_command(body: CommandInput, request: Request, db: AsyncSession = Depends(database)):
        """The one worker entry for a finalized user utterance.

        Reads the already-persisted source turn and its frozen revisions; it
        never accepts a client-supplied revision or transcript.  Returns
        ``{"command": null}`` when the utterance is ordinary conversation.
        """
        # The common case is one joined read instead of the guard, turn and conversation reads.
        row = (
            await db.execute(
                select(VoiceSession, Turn, Conversation)
                .join(Turn, Turn.conversation_id == VoiceSession.conversation_id)
                .join(Conversation, Conversation.id == Turn.conversation_id)
                .where(VoiceSession.id == request.headers.get("x-session-id", ""), Turn.id == body.turn_id)
            )
        ).first()
        if (
            row is None
            or not _session_is_active(row[0], request.headers.get("x-room-name"), allow_ended=False)
            or row[1].role != "user"
            or not secret_matches(request.headers.get("x-agent-secret", ""), config.agent_service_secret)
        ):
            # Fall back to the shared guard so 401/403/404 stay exactly as before.
            voice = await guard(request, db)
            turn = await db.get(Turn, body.turn_id)
            if not turn or turn.conversation_id != voice.conversation_id or turn.role != "user":
                raise HTTPException(404)
            conversation = await db.get(Conversation, turn.conversation_id)
            if conversation is None:
                raise HTTPException(404)
        else:
            _, turn, conversation = row
        snapshot = turn.snapshot or {}
        expected_revision = snapshot.get("revision")
        if not isinstance(expected_revision, int):
            expected_revision = conversation.revision
        model_request = snapshot.get("model_request")
        settings_revision = model_request.get("settings_revision") if isinstance(model_request, dict) else None
        if not isinstance(settings_revision, int):
            settings_row = await db.get(UserSetting, conversation.owner_id)
            settings_revision = settings_row.revision if settings_row is not None else 0
        classifier = _RecordingClassifier(getattr(app.state, "ui_classifier", None))
        result = await handle_finalized_utterance(
            db,
            owner_id=conversation.owner_id,
            conversation_id=turn.conversation_id,
            request_id=command_request_id(turn.id),
            expected_revision=expected_revision,
            settings_revision=settings_revision,
            text=turn.text,
            catalog=catalog,
            approved_voice_ids=approved_voice_ids,
            handlers=app.state.action_handlers,
            secret=secret,
            ui_classifier=classifier if classifier.inner is not None else None,
            wordle_enabled=config.wordle_enabled,
        )
        return {
            "command": result.model_dump(mode="json") if result is not None else None,
            # The one classification of this turn, shared with the worker so it can force a memory tool.
            "memory_op": classifier.memory_op() if result is None else None,
        }


class _RecordingClassifier:
    """Wraps the UI classifier so one Jev call per turn also yields ``memory_op``."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.result = None

    async def classify(self, *, request_id: str, instruction: str):
        self.result = await self.inner.classify(request_id=request_id, instruction=instruction)
        return self.result

    def memory_op(self) -> dict | None:
        answer = None if self.result is None else self.result.memory_op
        if answer is None:
            return None
        return {"choice": answer.choice, "probability": answer.probability}


def _claim_replay(claim):
    if claim.kind == "completed":
        return claim.result or {}
    if claim.kind == "pending":
        raise HTTPException(409, detail={"code": "TOOL_OPERATION_IN_PROGRESS"})
    if claim.kind == "failed":
        return claim.result or {"error": claim.error_code or "TOOL_OPERATION_FAILED"}
    if claim.kind == "unknown":
        raise HTTPException(503, detail={"code": claim.error_code or "TOOL_OPERATION_UNKNOWN"})
    return None


def _register_tool_routes(app, *, config, database, guard):
    @app.post("/internal/agent/tool/weather")
    async def agent_weather(body: WeatherInput, request: Request, db: AsyncSession = Depends(database)):
        # The common case is one joined read instead of the guard and turn reads.
        row = (
            await db.execute(
                select(VoiceSession, Turn)
                .join(Turn, Turn.conversation_id == VoiceSession.conversation_id)
                .where(VoiceSession.id == request.headers.get("x-session-id", ""), Turn.id == body.turn_id)
            )
        ).first()
        if (
            row is None
            or not _session_is_active(row[0], request.headers.get("x-room-name"), allow_ended=False)
            or not secret_matches(request.headers.get("x-agent-secret", ""), config.agent_service_secret)
        ):
            # Fall back to the shared guard so 401/403/404 stay exactly as before.
            voice = await guard(request, db)
            turn = await db.get(Turn, body.turn_id)
            if not turn or turn.conversation_id != voice.conversation_id:
                raise HTTPException(404)
        else:
            _, turn = row
        policy = (turn.snapshot or {}).get("weather_policy") or {}
        decision = decide_weather_request(
            city=body.city,
            country=body.country,
            user_text=turn.text,
            included=location_evidence(policy.get("included_locations", [])),
            excluded=location_evidence(policy.get("excluded_locations", [])),
        )
        if not decision.allowed:
            return {"error": decision.reason or "LOCATION_EXCLUDED"}
        try:
            claim = await claim_tool_operation(
                db,
                turn_id=body.turn_id,
                operation_key=body.request_id,
                name="get_current_weather",
                arguments={"city": body.city, "country": body.country},
            )
        except ToolOperationError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from exc

        replay = _claim_replay(claim)
        if replay is not None:
            return replay

        # The claim is durable before any external effect.
        await db.commit()
        try:
            result = await get_current_weather(body.city, body.country)
        except WeatherError as exc:
            code = str(exc)
            state = terminal_state(code)
            if state is ToolOperationState.UNKNOWN:
                await unknown_tool_operation(db, operation_key=body.request_id, error_code=code)
            else:
                await fail_tool_operation(db, operation_key=body.request_id, error_code=code)
            await db.commit()
            return {"error": code}
        except Exception:
            await unknown_tool_operation(db, operation_key=body.request_id, error_code="TOOL_DELIVERY_UNKNOWN")
            await db.commit()
            raise HTTPException(503, detail={"code": "TOOL_DELIVERY_UNKNOWN"}) from None

        try:
            await complete_tool_operation(db, operation_key=body.request_id, result=result)
            await db.commit()
        except ToolOperationError as exc:
            await unknown_tool_operation(db, operation_key=body.request_id, error_code="TOOL_DELIVERY_UNKNOWN")
            await db.commit()
            raise HTTPException(503, detail={"code": "TOOL_DELIVERY_UNKNOWN"}) from exc
        return result


async def _finish_tool_operation(db: AsyncSession, key: str, effect) -> dict:
    """Run the effect after the durable claim; record exactly one terminal state."""
    try:
        result = await effect()
    except MemoryCaptureError as exc:
        await db.rollback()
        await fail_tool_operation(db, operation_key=key, error_code=exc.code)
        await db.commit()
        return {"error": exc.code}
    except Exception:
        await db.rollback()
        await unknown_tool_operation(db, operation_key=key, error_code="TOOL_DELIVERY_UNKNOWN")
        await db.commit()
        raise HTTPException(503, detail={"code": "TOOL_DELIVERY_UNKNOWN"}) from None
    try:
        await complete_tool_operation(db, operation_key=key, result=result)
        await db.commit()
    except ToolOperationError as exc:
        await unknown_tool_operation(db, operation_key=key, error_code="TOOL_DELIVERY_UNKNOWN")
        await db.commit()
        raise HTTPException(503, detail={"code": "TOOL_DELIVERY_UNKNOWN"}) from exc
    return result


async def _run_memory_tool(guard, db: AsyncSession, request: Request, body, name: str, arguments: dict, effect):
    voice = await guard(request, db)
    conversation = await db.get(Conversation, voice.conversation_id)
    turn = await db.get(Turn, body.turn_id)
    if conversation is None or not turn or turn.conversation_id != voice.conversation_id or turn.role != "user":
        raise HTTPException(404)
    try:
        claim = await claim_tool_operation(
            db, turn_id=body.turn_id, operation_key=body.request_id, name=name, arguments=arguments
        )
    except ToolOperationError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    replay = _claim_replay(claim)
    if replay is not None:
        return replay
    await db.commit()
    return await _finish_tool_operation(
        db, body.request_id, lambda: effect(conversation.owner_id, voice.conversation_id)
    )


def _register_memory_tool_routes(app, *, config: Settings, database, guard):
    """Model-facing memory tools. Every route is propose-only or read-only."""
    secret = _memory_secret(config)

    @app.post("/internal/agent/tool/memory-propose")
    async def agent_memory_propose(body: MemoryProposeInput, request: Request, db: AsyncSession = Depends(database)):
        async def effect(owner_id, conversation_id):
            row = await db.get(UserSetting, owner_id)
            if row is not None and row.memory_mode == "off":
                return {
                    "status": "rejected",
                    "error": "MEMORY_OFF",
                    "message": "Memory is turned off, so nothing was saved. Tell the user memory is off; "
                    "do not claim it was saved.",
                }
            if classify_sensitivity(body.fact) is Sensitivity.CREDENTIAL:
                # Never record it as a proposal: that would store the text we promise not to keep.
                return {"status": "rejected", "error": "MEMORY_SENSITIVE", "message": SENSITIVE_TOOL_NOTE}
            result = await memory_propose_tool(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                turn_id=body.turn_id,
                fact=body.fact,
                secret=secret,
            )
            return result

        return await _run_memory_tool(guard, db, request, body, "memory_propose", {"fact": body.fact}, effect)

    @app.post("/internal/agent/tool/memory-forget")
    async def agent_memory_forget(body: MemoryQueryInput, request: Request, db: AsyncSession = Depends(database)):
        async def effect(owner_id, conversation_id):
            return await memory_forget_tool(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                request_id=body.request_id,
                query=body.query,
            )

        return await _run_memory_tool(guard, db, request, body, "memory_forget", {"query": body.query}, effect)

    @app.post("/internal/agent/tool/memory-recall")
    async def agent_memory_recall(body: MemoryQueryInput, request: Request, db: AsyncSession = Depends(database)):
        async def effect(owner_id, conversation_id):
            return await memory_recall_tool(db, owner_id=owner_id, conversation_id=conversation_id, query=body.query)

        return await _run_memory_tool(guard, db, request, body, "memory_recall", {"query": body.query}, effect)
