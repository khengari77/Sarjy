"""Worker-only endpoints for bootstrap, turns, weather and budget reservations."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.commands import CommandResult
from app.domain.context import ContextCandidate, compile_turn_context, validate_assembly
from app.domain.contracts import (
    MemoryRecord,
    MemorySource,
    ModelRequest,
    UserSettings,
    resolve_response_policy,
)
from app.domain.policy import normalize_pipeline, pipeline_instructions
from app.domain.secrets import secret_matches
from app.domain.tool_operations import ToolOperationState, terminal_state
from app.domain.weather_permissions import decide_weather_request
from app.infrastructure.database import Conversation, Memory, Turn, UserSetting, VoiceSession
from app.services.budget import reserve as reserve_development_budget
from app.services.commands import command_request_id, handle_finalized_utterance
from app.services.context_assembly import location_evidence, read_context_inputs
from app.services.memory import MemoryService
from app.services.memory_capture import (
    MemoryCaptureError,
    confirm_memory,
    load_proposal,
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
    provider: Literal["openai", "elevenlabs", "deepgram_tts", "deepseek"]
    characters: int = Field(default=0, ge=0, le=500)


class AgentInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=12000)


class MemoryConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=20000)
    decision: Literal["save", "discard", "replace"]


class AgentOutput(BaseModel):
    turn_id: str
    text: str = Field(max_length=20000)
    status: Literal["completed", "interrupted", "failed"]


class WeatherInput(BaseModel):
    turn_id: str
    request_id: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=120)
    country: str | None = Field(default=None, max_length=120)


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


def _session_guard(config: Settings):
    async def worker_session(request: Request, db: AsyncSession, allow_ended: bool = False):
        secret = request.headers.get("x-agent-secret", "")
        if not secret_matches(secret, config.agent_service_secret):
            raise HTTPException(401, detail={"code": "WORKER_AUTH_REQUIRED"})
        voice = await db.get(VoiceSession, request.headers.get("x-session-id", ""))
        statuses = {"starting", "active", "ended"} if allow_ended else {"starting", "active"}
        cutoff = datetime.now(UTC) - timedelta(seconds=60 if allow_ended else 0)
        if (
            not voice
            or voice.room_name != request.headers.get("x-room-name")
            or voice.status not in statuses
            or voice.expires_at <= cutoff.isoformat()
        ):
            raise HTTPException(403, detail={"code": "SESSION_NOT_ACTIVE"})
        return voice

    return worker_session


PENDING_MEMORY_INSTRUCTION = (
    "A memory candidate is pending for this turn. Ask the user to confirm or decline before it is saved. "
    "Do not claim it is saved until confirmation succeeds."
)


def register_worker_routes(app, *, config: Settings, database, sessions, embedder, catalog, approved_voice_ids=None):
    guard = _session_guard(config)
    _register_budget_routes(app, config=config, database=database)
    _register_session_routes(app, database=database, guard=guard)
    _register_turn_routes(app, config=config, database=database, guard=guard, sessions=sessions)
    _register_command_routes(
        app,
        database=database,
        guard=guard,
        catalog=catalog,
        approved_voice_ids=approved_voice_ids,
        secret=_memory_secret(config),
    )
    _register_tool_routes(app, database=database, guard=guard)
    _register_memory_tool_routes(app, config=config, database=database, guard=guard)
    _register_memory_routes(app, config=config, database=database, guard=guard, embedder=embedder)


def _memory_secret(config: Settings) -> str:
    return config.context_proposal_secret or config.agent_service_secret


def _memory_proposal(
    config: Settings, *, owner_id: str, conversation_id: str, source_turn_id: str, text: str, strict: bool = False
):
    secret = _memory_secret(config)
    if not secret:
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
    config: Settings, pipeline, *, owner_id: str, conversation_id: str, turn, strict_capture: bool = False
) -> tuple[str, dict | None]:
    proposal = _memory_proposal(
        config,
        owner_id=owner_id,
        conversation_id=conversation_id,
        source_turn_id=turn.id,
        text=turn.text,
        strict=strict_capture,
    )
    instructions = pipeline_instructions(pipeline)
    if proposal is not None:
        instructions = f"{instructions}\n\n{PENDING_MEMORY_INSTRUCTION}"
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


async def _replayed_turn_response(db: AsyncSession, *, owner_id: str, conversation_id: str, turn):
    return {
        "turn_id": turn.id,
        "context": turn.snapshot,
        "memory_proposal": await _replayed_proposal(db, owner_id=owner_id, turn=turn),
    }


def _register_memory_routes(app, *, config: Settings, database, guard, embedder):
    @app.post("/internal/agent/memory/confirm")
    async def confirm_memory_route(body: MemoryConfirm, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db, allow_ended=True)
        conversation = await db.get(Conversation, voice.conversation_id)
        if conversation is None:
            raise HTTPException(404)
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
            else {"limit": config.elevenlabs_daily_characters}
            if body.provider == "elevenlabs"
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
        return {"pipeline": pipeline, "instructions": pipeline_instructions(pipeline), "expires_at": voice.expires_at}


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


def _register_turn_routes(app, *, config: Settings, database, guard, sessions):
    @app.post("/internal/agent/turn/start")
    async def turn_start(body: AgentInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db)
        # Serializes worker retries and preserves the conversation's authoritative order.
        conversation = await db.scalar(
            select(Conversation).where(Conversation.id == voice.conversation_id).with_for_update()
        )
        existing = await db.scalar(
            select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == body.request_id)
        )
        if existing:
            return await _replayed_turn_response(
                db,
                owner_id=conversation.owner_id,
                conversation_id=voice.conversation_id,
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
            owner_id=conversation.owner_id,
            conversation_id=voice.conversation_id,
            turn=turn,
            # With the model classifier available, regexes only keep the exact "remember that ..." fast path.
            strict_capture=getattr(app.state, "ui_classifier", None) is not None,
        )
        inputs = await read_context_inputs(db, owner_id=conversation.owner_id, conversation_id=voice.conversation_id)
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
        settings_row = await db.get(UserSetting, conversation.owner_id)
        settings_value = (
            UserSettings()
            if settings_row is None
            else UserSettings(
                speaking_speed=settings_row.speaking_speed,
                captions_visible=settings_row.captions_visible,
                approved_voice_id=settings_row.approved_voice_id,
                response_preference=settings_row.response_preference,
                revision=settings_row.revision,
            )
        )
        memory_rows = (
            await db.scalars(
                select(Memory).where(
                    Memory.owner_id == conversation.owner_id,
                    Memory.id.in_(compiled.included_ids),
                    Memory.status == "active",
                )
            )
        ).all()
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
                conversation_id=conversation_id,
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


def _register_command_routes(app, *, database, guard, catalog, approved_voice_ids=None, secret=""):
    @app.post("/internal/agent/command", response_model=CommandResponse)
    async def finalized_command(body: CommandInput, request: Request, db: AsyncSession = Depends(database)):
        """The one worker entry for a finalized user utterance.

        Reads the already-persisted source turn and its frozen revisions; it
        never accepts a client-supplied revision or transcript.  Returns
        ``{"command": null}`` when the utterance is ordinary conversation.
        """
        voice = await guard(request, db)
        turn = await db.get(Turn, body.turn_id)
        if not turn or turn.conversation_id != voice.conversation_id or turn.role != "user":
            raise HTTPException(404)
        conversation = await db.get(Conversation, turn.conversation_id)
        if conversation is None:
            raise HTTPException(404)
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


def _register_tool_routes(app, *, database, guard):
    @app.post("/internal/agent/tool/weather")
    async def agent_weather(body: WeatherInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db)
        turn = await db.get(Turn, body.turn_id)
        if not turn or turn.conversation_id != voice.conversation_id:
            raise HTTPException(404)
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
            return await memory_propose_tool(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                turn_id=body.turn_id,
                fact=body.fact,
                secret=secret,
            )

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
