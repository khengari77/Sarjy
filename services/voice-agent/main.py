"""LiveKit worker. Run from the repository root with uv run ... dev."""

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from contextlib import aclosing
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import uuid4

import httpx
from app.domain.budget import DEEPSEEK_MAX_REQUEST_BYTES
from app.domain.contracts import AssistantContinuation, ModelRequest, ModelToolItem, ToolSpec
from app.domain.memory_capture import is_referential
from app.domain.tool_operations import operation_key
from app.domain.ui_routing import CONFIDENCE_THRESHOLD, FORCED_MEMORY_TOOLS
from config import provider_credentials, read_worker_environment, require_values
from dotenv import load_dotenv
from fallback import fallback_decision, next_attempt
from generation import GenerationEvent, GenerationFailure, adapter_for, livekit_chunk
from held_stream import EarlyReply
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    JobContext,
    RunContext,
    SessionConnectOptions,
    cli,
    function_tool,
    llm,
    room_io,
)
from livekit.agents.llm.utils import build_legacy_openai_schema
from livekit.plugins import silero
from provider_factory import build_providers, speed_controller_for, tts_budget_provider
from snapshot_request import snapshot_request
from speech_budget import SpeechBudget
from turn_metrics import assistant_metrics, build_payload, elapsed_ms
from wordle_commands import is_wordle_command, parse_say_payload
from wordle_tools import (
    WordleTools,
    claims_game_state,
    extract_guess,
    has_guess_candidate,
    is_close_request,
    publish_wordle,
)

server = AgentServer()

# D2.9 tier 5 ceiling. The worker refuses anything above the published caps even
# if a stale or tampered backend payload asks for it.
MAX_TIER_OUTPUT_TOKENS = 1024
MAX_CONTEXT_BYTES = 64000
MAX_TOOLS = 4
GENERATION_ATTEMPT_SECONDS = 30.0
DECISION_TURN_NOTE = "The user is answering the previous confirmation; do not propose anything this turn."
FAILED_NOTE = "The memory tool failed. Nothing was saved, excluded or changed; do not claim otherwise."


def deepseek_request_within_bounds(serialized: bytes) -> bool:
    return len(serialized) <= DEEPSEEK_MAX_REQUEST_BYTES


def openai_request_within_bounds(serialized: bytes, tools: list, max_output_tokens: int) -> bool:
    """Pure budget guard: 64KB context, fixed tools and at most the tier-5 cap."""
    return (
        len(serialized) <= MAX_CONTEXT_BYTES and len(tools) <= MAX_TOOLS and max_output_tokens <= MAX_TIER_OUTPUT_TOKENS
    )


def _memory_decision(text: str) -> str | None:
    lowered = " ".join(text.casefold().replace(",", " ").split()).strip(" .!?;:")
    affirmative = {
        "yes",
        "yeah",
        "yep",
        "sure",
        "ok",
        "okay",
        "please do",
        "go ahead",
        "remember it",
        "yes save it",
        "yes please save it",
        "save it",
        "please save it",
        "yes remember it",
        "confirm",
    }
    negative = {
        "no",
        "nope",
        "don't",
        "do not",
        "not now",
        "skip",
        "no thanks",
        "don't save",
        "don't save that",
        "do not save that",
        "no thank you",
        "no, wait",
        "no wait",
        "cancel",
        "discard",
        "forget it",
        "forget that",
    }
    if lowered in negative:
        return "discard"
    if lowered in affirmative:
        return "save"
    return None


def _clarify_decision(text: str) -> str | None:
    lowered = " ".join(text.casefold().replace(",", " ").replace("\u2019", "'").split()).strip(" .!?;:")
    if lowered in {
        "yes replace it",
        "yes replace",
        "replace",
        "replace it",
        "replace the old one",
        "replace the old fact",
        "use the new one",
        "update it",
        "yes update it",
        "change it",
    }:
        return "replace"
    if lowered in {
        "no",
        "nope",
        "no thanks",
        "no thank you",
        "cancel",
        "don't",
        "do not",
        "discard",
        "keep",
        "keep it",
        "keep the old one",
        "keep the old fact",
        "no keep it",
        "no keep the old one",
        "decline",
    }:
        return "discard"
    return None


def _user_message(chat_ctx):
    return next(
        (item for item in reversed(chat_ctx.items) if isinstance(item, llm.ChatMessage) and item.role == "user"),
        None,
    )


def _continuation(chat_ctx, user):
    continuation = []
    for item in chat_ctx.items[chat_ctx.items.index(user) + 1 :]:
        if isinstance(item, llm.ChatMessage) and item.role == "assistant":
            continuation.append(AssistantContinuation(content=item.text_content, interrupted=item.interrupted))
        elif isinstance(item, llm.FunctionCall):
            continuation.append(
                ModelToolItem(kind="call", call_id=item.call_id, name=item.name, content=item.arguments)
            )
        elif isinstance(item, llm.FunctionCallOutput):
            continuation.append(
                ModelToolItem(
                    kind="result", call_id=item.call_id, name=item.name, content=item.output, is_error=item.is_error
                )
            )
    return continuation


def _with_continuation(request: ModelRequest, continuation, schemas) -> ModelRequest:
    return ModelRequest.model_validate(
        {
            **request.model_dump(mode="json"),
            "attempt_id": str(uuid4()),
            "tool_history": continuation,
            "tools": [
                ToolSpec(
                    name=schema["name"], description=schema.get("description", ""), parameters=schema["parameters"]
                )
                for schema in schemas
            ],
        }
    )


def _with_memory_note(request: ModelRequest, note: str) -> ModelRequest:
    """Append an outcome note where the provider will actually see it.

    ``instructions`` mirrors ``messages[0]``; adapters build the provider
    request from ``messages``. Updating only ``instructions`` (and bypassing
    validation with ``model_copy``) makes the note dead data.
    """
    payload = request.model_dump(mode="json")
    messages = payload["messages"]
    messages[0]["content"] = f"{messages[0]['content']}\n\n{note}"
    payload["instructions"] = messages[0]["content"]
    return ModelRequest.model_validate(payload)


async def _playout_result(handle) -> tuple[str, str]:
    await handle.wait_for_playout()
    messages = [item for item in handle.chat_items if isinstance(item, llm.ChatMessage) and item.role == "assistant"]
    text = "\n".join(item.text_content or "" for item in messages)
    status = "failed" if handle.exception() else ("interrupted" if handle.interrupted else "completed")
    return text, status


class SarjyAgent(WordleTools, Agent):
    def __init__(
        self,
        backend: httpx.AsyncClient,
        pipeline: Mapping[str, Any],
        instructions: str,
        notify=None,
        fallback_llm=None,
        publish=None,
        is_command=None,
        publish_wordle=None,
        wordle_active=None,
    ):
        super().__init__(instructions=instructions)
        self.publish_wordle = publish_wordle
        self.wordle_active = wordle_active or (lambda: False)
        self.wordle_spoken: dict[str, list[str]] = {}
        self.wordle_touched = False
        self.wordle_calls: set[str] = set()
        self.current_text = ""
        self.backend = backend
        self.pipeline = pipeline
        self.notify = notify
        self.fallback_llm = fallback_llm
        self.publish = publish
        self.is_command = is_command or (lambda _text: False)
        self.prepared: dict[str, dict] = {}
        self.commands: dict[str, dict | None] = {}
        # Jev's memory_op answer per user message, shared from the single command call; forced at most once.
        self.memory_ops: dict[str, dict] = {}
        self.memory_forced: set[str] = set()
        self.current_turn: str | None = None
        self.turn_stages: dict[str, dict[str, int | None]] = {}
        self.pending: set[asyncio.Task] = set()
        self.speeches: set[str] = set()
        self.speaking_speed_control = speed_controller_for(pipeline)
        self.tts_budget_provider = tts_budget_provider(pipeline)
        # Prepayments in flight. Cancelled at shutdown, after which none may start.
        self.speech_stopped = False
        self.speech_refills: set[asyncio.Task] = set()
        self.speech_budget = SpeechBudget(self._reserve_speech, self._report_reservation, self._spawn_refill)
        self.memory_proposal: dict | None = None
        self.memory_outcome: str | None = None
        self.memory_message: str | None = None
        self.memory_clarify = False
        self.memory_question_turn: str | None = None
        # Consent is bound to the exact proposal identity. Playout marking and
        # question delivery are tracked separately: having yielded the question
        # is not evidence that the user heard it.
        self.memory_prompted_key: tuple[Any, Any] | None = None
        self.memory_question_key: tuple[Any, Any] | None = None
        # A consent decision owns its turn: the command path must not speak a
        # second, contradictory outcome for the same utterance.
        self.command_blocked_turn: str | None = None
        # Set when a committed command result could not be published to the UI.
        self.command_resync_required = False
        # Typed memory tools. A tool-armed turn owns the consent state: the turn/start
        # proposal for the same turn must not be re-armed over it. ``forced_speech`` is
        # the deterministic question the next llm_node yields instead of model prose.
        self.tool_armed_turn: str | None = None
        # The turn that answered a pending confirmation may not start another proposal.
        self.decision_turn: str | None = None
        self.forced_speech: tuple[str, str] | None = None
        # Exact Wordle commands are answered by the web client through
        # sarjy.say; the conversational LLM must stay silent for those turns.
        self.suppressed: set[str] = set()

    @staticmethod
    def _proposal_key(proposal: Mapping[str, Any] | None) -> tuple[Any, Any]:
        if proposal is None:
            return (None, None)
        return (proposal.get("confirmation_id"), proposal.get("confirmation_revision"))

    def _record_stage(self, turn_id: str | None, name: str, started: float, *, first_only: bool = False) -> None:
        # Only the turn/start of a turn opens its entry; a late timing must not reopen a sent one.
        stages = self.turn_stages.get(turn_id) if turn_id is not None else None
        if stages is None or (first_only and name in stages):
            return
        stages[name] = elapsed_ms(started, time.perf_counter())

    async def compile(self, message):
        if message.id not in self.prepared:
            started = time.perf_counter()
            response = await self.backend.post(
                "/internal/agent/turn/start", json={"request_id": message.id, "text": message.text_content}
            )
            response.raise_for_status()
            self.prepared[message.id] = response.json()
            turn_id = self.prepared[message.id]["turn_id"]
            self.turn_stages[turn_id] = {}
            self._record_stage(turn_id, "turn_start_ms", started)
        result = self.prepared[message.id]
        self.current_turn = result["turn_id"]
        self.current_text = message.text_content or ""
        await self._publish_resync()
        self._apply_speaking_speed(result["context"])
        decided = False
        # A confirmation/rejection utterance is never itself a capture, with or
        # without a pending proposal.
        decision_utterance = (
            _memory_decision(message.text_content) is not None or _clarify_decision(message.text_content) is not None
        )
        if message.id != self.memory_message:
            # The SDK compiles a user message twice (turn hook + llm_node); decide once.
            self.memory_message = message.id
            self.memory_outcome = None
            if self.memory_proposal is not None:
                if self._proposal_key(self.memory_proposal) != self.memory_prompted_key:
                    # The specific question for this proposal was never
                    # delivered; the utterance cannot be consent for it.
                    self.memory_proposal = None
                    self.memory_clarify = False
                    self.memory_prompted_key = None
                    self.memory_question_key = None
                    self.memory_outcome = "not_saved"
                else:
                    decision = (
                        _clarify_decision(message.text_content)
                        if self.memory_clarify
                        else _memory_decision(message.text_content)
                    )
                    if decision is not None:
                        await self._resolve_memory(decision)
                        # One turn, one decision: the utterance that resolved the
                        # pending proposal must not also be enqueued as a new
                        # capture candidate, nor reclassified as a command.
                        decided = True
                        self.command_blocked_turn = message.id
                        self.decision_turn = result["turn_id"]
                    else:
                        # A proposal is a one-shot for the next user turn; never stay armed
                        # across unrelated acknowledgements.
                        self.memory_proposal = None
                        self.memory_clarify = False
                        self.memory_prompted_key = None
                        self.memory_question_key = None
                        self.memory_outcome = "not_saved"
        proposal = result.get("memory_proposal")
        if (
            not decided
            and not decision_utterance
            and self.tool_armed_turn != result["turn_id"]
            and proposal is not None
            and not is_referential(proposal.get("text") or "")
        ):
            if self._proposal_key(proposal) != self._proposal_key(self.memory_proposal):
                # Only a new proposal identity re-arms the question. Repeated
                # compiles of the same proposal must not re-emit or re-arm it.
                self.memory_prompted_key = None
                self.memory_question_key = None
            self.memory_proposal = proposal
            self.memory_question_turn = result["turn_id"]
        return result["context"]

    def _apply_speaking_speed(self, context):
        """Apply the turn's stored pace before this turn's reply is synthesized.

        ``speaking_speed`` is part of the frozen turn snapshot, so a change made
        during the previous turn is picked up at this turn's synthesis boundary.
        Legacy snapshots without a model request keep the provider default.
        """
        request = context.get("model_request") if isinstance(context, Mapping) else None
        settings = request.get("settings") if isinstance(request, Mapping) else None
        speed = settings.get("speaking_speed") if isinstance(settings, Mapping) else None
        try:
            session = self.session
        except RuntimeError:
            # Unit callers may compile outside a running session; nothing to configure.
            return
        tts = getattr(session, "tts", None)
        if tts is not None:
            self.speaking_speed_control.apply(tts, speed)

    async def _resolve_memory(self, decision: str) -> None:
        proposal = self.memory_proposal
        try:
            response = await self.backend.post(
                "/internal/agent/memory/confirm",
                json={"token": proposal["token"], "decision": decision},
            )
        except httpx.HTTPError:
            self.memory_proposal = None
            self.memory_clarify = False
            self.memory_question_turn = None
            self.memory_outcome = "failed"
            if self.notify:
                await self.notify("MEMORY_SAVE_FAILED")
            return
        detail: dict = {}
        if not response.is_success:
            try:
                raw = response.json().get("detail", {})
                detail = raw if isinstance(raw, dict) else {}
            except (ValueError, AttributeError):
                detail = {}
            if self.notify:
                await self.notify(detail.get("code") or "MEMORY_SAVE_FAILED")
        if response.is_success:
            self.memory_proposal = None
            self.memory_clarify = False
            self.memory_question_turn = None
            self.memory_prompted_key = None
            self.memory_question_key = None
            try:
                status = response.json().get("status", "")
            except (ValueError, AttributeError):
                status = ""
            self.memory_outcome = "replace" if status in {"corrected", "retracted"} else decision
        elif detail.get("code") == "MEMORY_CLARIFICATION_REQUIRED" and isinstance(detail.get("token"), str):
            # Adopt the server-issued clarification revision. The old token can
            # never authorize a replacement, and the replacement question must
            # name the existing fact rather than asking the initial save question.
            self.memory_proposal = {
                **proposal,
                "token": detail["token"],
                "confirmation_id": detail.get("confirmation_id"),
                "confirmation_revision": detail.get("confirmation_revision"),
                "target_id": detail.get("target_id"),
                "target_version": detail.get("target_version"),
                "question": detail.get("question") or proposal.get("question"),
            }
            self.memory_clarify = True
            self.memory_prompted_key = None
            self.memory_question_key = None
            self.memory_outcome = "clarify"
        else:
            self.memory_proposal = None
            self.memory_clarify = False
            self.memory_question_turn = None
            self.memory_prompted_key = None
            self.memory_question_key = None
            self.memory_outcome = "failed"

    async def reserve(self, provider: str, characters: int = 0, report=None):
        if provider in {"elevenlabs", "deepgram_tts"} and characters > 500:
            if self.notify:
                await self.notify("DAILY_BUDGET_EXHAUSTED")
            raise ValueError("DAILY_BUDGET_EXHAUSTED")
        try:
            await self._post_reservation(provider, characters)
        except httpx.HTTPStatusError as exc:
            await (report or self._report_reservation)(exc)
            raise

    async def _post_reservation(self, provider: str, characters: int) -> None:
        turn_id, started = self.current_turn, time.perf_counter()
        response = await self.backend.post(
            "/internal/development/reserve", json={"provider": provider, "characters": characters}
        )
        if provider in {"openai", "deepseek"}:
            self._record_stage(turn_id, "llm_reserve_ms", started, first_only=True)
        response.raise_for_status()

    async def _report_reservation(self, exc: httpx.HTTPStatusError) -> None:
        if self.notify:
            await self.notify(
                "DAILY_BUDGET_EXHAUSTED" if exc.response.status_code == 429 else "BUDGET_SERVICE_UNAVAILABLE"
            )

    async def _reserve_speech(self, characters: int) -> None:
        await self._post_reservation(self.tts_budget_provider, characters)

    def _spawn_refill(self, refill) -> asyncio.Task[None]:
        if self.speech_stopped:
            refill.close()
            raise RuntimeError("SPEECH_STOPPED")
        task = asyncio.create_task(refill)
        self.speech_refills.add(task)

        def settled(done: asyncio.Task[None]) -> None:
            self.speech_refills.discard(done)
            if not done.cancelled():
                # Retrieved here: a refused refill matters only if its text is needed.
                done.exception()

        task.add_done_callback(settled)
        return task

    async def stop_speech_refills(self) -> None:
        self.speech_stopped = True
        refills = list(self.speech_refills)
        for refill in refills:
            refill.cancel()
        await asyncio.gather(*refills, return_exceptions=True)

    async def tts_node(self, text, model_settings):
        turn_id = self.current_turn
        first_take = True

        async def budgeted_text():
            # tts_reserve_ms is how long the first words waited for paid characters.
            nonlocal first_take
            async for part in text:
                while part:
                    # Charge before forwarding text. Interrupted/failed reservations are retained.
                    started = time.perf_counter()
                    covered = await self.speech_budget.take(len(part))
                    if first_take:
                        first_take = False
                        self._record_stage(turn_id, "tts_reserve_ms", started, first_only=True)
                    yield part[:covered]
                    part = part[covered:]

        async for frame in Agent.default.tts_node(self, budgeted_text(), model_settings):
            yield frame

    async def on_user_turn_completed(self, turn_ctx, new_message):
        if self.is_command(new_message.text_content or ""):
            self.suppressed.add(new_message.id)
            return
        await self.compile(new_message)

    async def command_for(self, message) -> dict | None:
        """Ask the API to interpret one finalized turn, once per turn.

        A transport/5xx failure is retried once: the request id derives from the
        source turn and the endpoint replays a committed result, so the retry
        cannot double-apply. If both attempts fail, the turn continues through
        the guarded conversational path; no success is spoken for it.
        """
        if message.id not in self.commands:
            started = time.perf_counter()
            result = None
            for attempt in range(2):
                try:
                    response = await self.backend.post(
                        "/internal/agent/command", json={"turn_id": self.prepared[message.id]["turn_id"]}
                    )
                    response.raise_for_status()
                    body = response.json()
                    result = body.get("command")
                    memory_op = body.get("memory_op")
                    if isinstance(memory_op, dict):
                        self.memory_ops[message.id] = memory_op
                    break
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    retryable = isinstance(exc, httpx.TransportError) or (
                        isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500
                    )
                    if attempt == 0 and retryable:
                        continue
                    logging.error("Finalized command resolution failed; continuing conversationally")
                    break
            self.commands[message.id] = result if isinstance(result, dict) else None
            self._record_stage(self.prepared[message.id]["turn_id"], "command_ms", started, first_only=True)
        return self.commands[message.id]

    async def _publish_command(self, command: Mapping[str, Any]) -> None:
        if self.publish is None:
            return
        for attempt in range(2):
            try:
                await self.publish(dict(command))
                return
            except Exception:
                if attempt == 1:
                    break
        # The effect is committed but the UI did not receive it. Never retry the
        # command; ask the next turn boundary to resync the server snapshot.
        self.command_resync_required = True
        logging.error("Finalized command result publication failed; resync required")

    async def _publish_resync(self) -> None:
        """At most once per turn boundary: tell the UI to refetch settings."""
        if not self.command_resync_required or self.publish is None:
            return
        try:
            await self.publish({"resync": True})
        except Exception:
            logging.error("Command resync publication failed; will retry next turn")
            return
        self.command_resync_required = False

    async def llm_node(self, chat_ctx, tools, model_settings):
        # Also covers typed input and repeated inference after tool calls.
        user = _user_message(chat_ctx)
        if user is None:
            return
        if user.id in self.suppressed:
            self.suppressed.discard(user.id)
            return
        snapshot = await self.compile(user)
        direct = await self._wordle_turn_speech(user)
        if direct is not None:
            # The application owns Wordle speech; an armed memory question must not follow it.
            self._disarm_memory_proposal()
            yield llm.ChatChunk(id=str(uuid4()), delta=llm.ChoiceDelta(content=direct))
            self._schedule_persistence(user, self.session.current_speech)
            return
        early = self._start_reply_early(user, snapshot, chat_ctx, tools, model_settings)
        try:
            command = None if self.command_blocked_turn == user.id else await self.command_for(user)
            reply = self._reply(user, snapshot, chat_ctx, tools, model_settings, command, early)
            async with aclosing(reply):
                async for chunk in reply:
                    yield chunk
        finally:
            if early is not None:
                await early.discard()

    def _start_reply_early(self, user, snapshot, chat_ctx, tools, model_settings) -> EarlyReply | None:
        """Start the reply this turn gets if it turns out not to be a command.

        Only for a turn whose request the command answer cannot change, except by
        forcing a memory tool; the reply is then thrown away and asked for again.
        Nothing it produces, tool calls and failures included, is seen before that.
        """
        if (
            user.id in self.commands
            or self.command_blocked_turn == user.id
            or self.memory_outcome is not None
            or self.forced_speech is not None
            or self._question_due()
            or self.wordle_active()
        ):
            return None
        request, tools, _ = self._reply_request(user, snapshot, chat_ctx, tools)
        choice = model_settings.tool_choice if model_settings else None
        return EarlyReply(lambda report: self._stream_with_fallback(request, tools, choice, report))

    def _question_due(self) -> bool:
        proposal = self.memory_proposal
        return proposal is not None and self.memory_question_key != self._proposal_key(proposal)

    def _reply_request(self, user, snapshot, chat_ctx, tools):
        request = snapshot_request(snapshot, self.current_turn)
        tools = self._wordle_tools(tools)
        schemas = [build_legacy_openai_schema(tool)["function"] for tool in tools]
        return _with_continuation(request, _continuation(chat_ctx, user), schemas), tools, schemas

    async def _reply(self, user, snapshot, chat_ctx, tools, model_settings, command, early):
        if command is not None:
            if early is not None:
                early.cancel()
            await self._publish_command(command)
            # Exact commands bypass conversational generation entirely; TTS
            # remains budgeted and the deterministic spoken line is the whole
            # assistant turn.
            yield llm.ChatChunk(id=str(uuid4()), delta=llm.ChoiceDelta(content=command["spoken"]))
            handle = self.session.current_speech
            self._schedule_persistence(user, handle)
            return
        if self.forced_speech is not None and self.forced_speech[0] == self.current_turn:
            _, forced = self.forced_speech
            self.forced_speech = None
            yield llm.ChatChunk(id=str(uuid4()), delta=llm.ChoiceDelta(content=forced))
            handle = self.session.current_speech
            self._schedule_persistence(user, handle)
            return
        proposal = self.memory_proposal
        key = self._proposal_key(proposal)
        if proposal is not None and self._question_due():
            # Deliver the exact confirmation question deterministically. Merely
            # yielding the text proves nothing: consent is armed only once the
            # resulting speech has fully played out.
            question = proposal.get("question") or f"Should I remember this: {proposal.get('text', '')}?"
            self.memory_question_key = key
            yield llm.ChatChunk(id=str(uuid4()), delta=llm.ChoiceDelta(content=question))
            handle = self.session.current_speech
            if handle is not None:
                self._schedule_prompt_confirmation(key, handle)
                # The deterministic question is the turn's spoken answer, so it
                # must still be persisted like any other assistant turn.
                self._schedule_persistence(user, handle)
            return
        handle = self.session.current_speech
        self._schedule_persistence(user, handle)
        request, tools, schemas = self._reply_request(user, snapshot, chat_ctx, tools)
        if self.memory_outcome is not None:
            outcome, self.memory_outcome = self.memory_outcome, None
            note = {
                "save": "The memory save succeeded; confirm to the user that it is saved.",
                "failed": "The memory save failed; apologise and do not claim it was saved.",
                "discard": "The user declined the memory save; do not claim it was saved.",
                "clarify": (
                    "An existing saved fact conflicts with this update. Ask the user to confirm replacing it; "
                    "do not claim it was saved until they confirm."
                ),
                "replace": "The pending memory was replaced; tell the user the earlier fact was updated.",
                "not_saved": "The pending memory was not saved; do not claim it was saved.",
            }[outcome]
            request = _with_memory_note(request, note)
        tool_choice = model_settings.tool_choice if model_settings else None
        forced = self._forced_memory_choice(user.id, {schema["name"] for schema in schemas})
        if forced is not None:
            tool_choice = forced
        if early is not None and forced is None:
            stream = early.released(self._report_reservation)
        else:
            if early is not None:
                early.cancel()
            stream = self._stream_with_fallback(request, tools, tool_choice)
        async with aclosing(stream), aclosing(self._guard_wordle_prose(stream)) as guarded:
            async for chunk in guarded:
                yield chunk

    async def _guard_wordle_prose(self, stream):
        """While a Wordle game is open, prose may not state marks, attempts or outcomes.

        The reply is buffered; if it holds such a claim and no tool ran, the server's real status is spoken.
        """
        if not self.wordle_active():
            async for chunk in stream:
                yield chunk
            return
        chunks = [chunk async for chunk in stream]
        said = "".join(c.delta.content or "" for c in chunks if c.delta)
        called = any(c.delta and c.delta.tool_calls for c in chunks)
        if not called and claims_game_state(said):
            spoken = await self._wordle_direct("wordle-status")
            if spoken:
                yield llm.ChatChunk(id=str(uuid4()), delta=llm.ChoiceDelta(content=spoken))
                return
        for chunk in chunks:
            yield chunk

    async def _wordle_turn_speech(self, user) -> str | None:
        """Deterministic Wordle speech: the reply after a Wordle tool, or a bare guess while a game is open.

        A whole-utterance guess ("crane", "my word is crane", "uh, C R A N E") is recorded straight away
        (no model call can skip it); every Wordle reply is the server's own line, never model prose.
        """
        turn = self.current_turn or ""
        spoken = self.wordle_spoken.pop(turn, None)
        if spoken:
            return " ".join(spoken)
        if not self.wordle_active():
            return None
        text = user.text_content or ""
        if is_close_request(text):
            return await self._wordle_direct("wordle-close")
        word = extract_guess(text)
        if word is None:
            return None
        return await self._wordle_direct("wordle-guess", word=word)

    def _wordle_tools(self, tools):
        """Offer only the Wordle tools that make sense for this turn.

        play_wordle is withdrawn while a game is open, and submit_wordle_guess is offered only when the
        utterance holds a five-letter word or spelled letters. A tool already called this turn stays listed
        so the continuation request remains valid.
        """
        if self.current_turn in self.wordle_calls:
            return tools
        drop = set()
        if self.wordle_active():
            drop.add("play_wordle")
        if not has_guess_candidate(self.current_text):
            drop.add("submit_wordle_guess")
        return [tool for tool in tools if getattr(getattr(tool, "info", None), "name", "") not in drop]

    def _forced_memory_choice(self, message_id: str, available: set[str]) -> dict | None:
        """Force the memory tool Jev named for the FIRST inference of a turn only.

        The continuation inference after the tool result is never forced, so a
        forced call cannot loop. Anything missing or below threshold is not forced.
        """
        if message_id in self.memory_forced or self.current_turn == self.decision_turn:
            return None
        answer = self.memory_ops.get(message_id)
        if not isinstance(answer, dict):
            return None
        tool = FORCED_MEMORY_TOOLS.get(str(answer.get("choice")))
        probability = answer.get("probability")
        if (
            tool is None
            or tool not in available
            or isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or probability < CONFIDENCE_THRESHOLD
        ):
            return None
        self.memory_forced.add(message_id)
        return {"type": "function", "function": {"name": tool}}

    def _schedule_prompt_confirmation(self, key: tuple[Any, Any], handle) -> None:
        async def mark_after_playout():
            try:
                await handle.wait_for_playout()
            except Exception:
                return
            if handle.interrupted or handle.exception() is not None:
                return
            if self._proposal_key(self.memory_proposal) != key:
                return
            self.memory_prompted_key = key

        task = asyncio.create_task(mark_after_playout())
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _finalize_turn(self, turn_id, text, status) -> None:
        for attempt in range(3):
            try:
                response = await self.backend.post(
                    "/internal/agent/turn/finalize", json={"turn_id": turn_id, "text": text, "status": status}
                )
                response.raise_for_status()
                return
            except httpx.HTTPError:
                if attempt == 2:
                    logging.error("Turn persistence failed after three attempts")
                    return
                await asyncio.sleep(0.25 * (attempt + 1))

    async def _send_turn_metrics(self, turn_id, user, handle) -> None:
        stages = self.turn_stages.pop(turn_id, {})
        try:
            payload = build_payload(
                turn_id,
                user_metrics=getattr(user, "metrics", None),
                assistant_metrics=assistant_metrics(getattr(handle, "chat_items", ())),
                stages=stages,
            )
            response = await self.backend.post("/internal/agent/turn/metrics", json=payload, timeout=2.0)
            response.raise_for_status()
        except Exception:
            logging.warning("Turn metrics submission failed")

    def _schedule_persistence(self, user, handle):
        if handle is None or handle.id in self.speeches:
            # One finalize per spoken handle, even across repeated inference.
            return
        self.speeches.add(handle.id)
        turn_id = self.prepared[user.id]["turn_id"]

        async def persist_playout():
            text, status = await _playout_result(handle)
            await self._finalize_turn(turn_id, text, status)
            await self._send_turn_metrics(turn_id, user, handle)

        task = asyncio.create_task(persist_playout())
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    def _require_deepseek_bounds(self, request: ModelRequest) -> None:
        serialized = json.dumps(
            {
                key: value
                for key, value in request.model_dump(mode="json").items()
                if key in {"messages", "tool_history", "tools"}
            },
            ensure_ascii=False,
        ).encode()
        if not deepseek_request_within_bounds(serialized):
            raise ValueError("DEEPSEEK_REQUEST_EXCEEDS_DEVELOPMENT_BOUNDS")

    def _require_openai_bounds(self, request: ModelRequest, tools) -> None:
        serialized = json.dumps(
            {
                key: value
                for key, value in request.model_dump(mode="json").items()
                if key in {"messages", "tool_history", "tools"}
            },
            ensure_ascii=False,
        ).encode()
        if not openai_request_within_bounds(serialized, tools, request.response_policy.max_output_tokens):
            raise ValueError("OPENAI_REQUEST_EXCEEDS_DEVELOPMENT_BOUNDS")

    async def _stream_attempt(self, provider, provider_kind, request, tools, tool_choice):
        """Stream one attempt; buffer usage so a failed attempt cannot leak it."""
        adapter = adapter_for(provider, provider_kind)
        buffered_usage = None
        emitted = False
        try:
            async with asyncio.timeout(GENERATION_ATTEMPT_SECONDS):
                async for event in adapter.stream(request, tools, tool_choice):
                    if event.kind in {"text", "tool"}:
                        emitted = True
                    if event.kind == "usage":
                        buffered_usage = event
                        continue
                    if event.kind == "completed" and buffered_usage is not None:
                        yield buffered_usage
                    yield event
        except TimeoutError as exc:
            raise GenerationFailure(
                GenerationEvent(
                    "error",
                    turn_id=request.turn_id,
                    attempt_id=request.attempt_id,
                    partial=emitted,
                    error_code="timeout",
                )
            ) from exc

    async def _stream_with_fallback(self, request, tools, tool_choice, report=None):
        """``report`` replaces the user-visible notice of a refused reservation."""
        primary_kind = self.pipeline["llm"]["provider"]
        emitted = False
        if primary_kind == "openai_direct":
            self._require_openai_bounds(request, tools)
            await self.reserve("openai", report=report)
        elif primary_kind == "deepseek_direct":
            self._require_deepseek_bounds(request)
            await self.reserve("deepseek", report=report)
        else:
            raise ValueError("UNSUPPORTED_GENERATION_PROVIDER")
        try:
            async for event in self._stream_attempt(self.session.llm, primary_kind, request, tools, tool_choice):
                if event.kind in {"text", "tool"}:
                    emitted = True
                chunk = livekit_chunk(event)
                if chunk is not None:
                    yield chunk
            return
        except GenerationFailure as failure:
            decision = fallback_decision(
                failure.event,
                emitted_output=emitted,
                fallback_configured=self.fallback_llm is not None,
            )
            if not decision.allowed:
                raise
            fallback_request = next_attempt(request, str(uuid4()))
            self._require_openai_bounds(fallback_request, tools)
            # Reserve before delivery. A denial forwards nothing.
            await self.reserve("openai", report=report)
            async for event in self._stream_attempt(
                self.fallback_llm, "openai_direct", fallback_request, tools, tool_choice
            ):
                chunk = livekit_chunk(event)
                if chunk is not None:
                    yield chunk

    @function_tool()
    async def get_current_weather(self, ctx: RunContext, city: str, country: str | None = None):
        """Get current weather for an explicitly identified city. Use an ISO 3166-1 alpha-2 country code such as LY when a country is known. Ask for city if missing."""
        if not self.current_turn:
            raise ValueError("TOOL_TURN_REQUIRED")
        call_id = getattr(ctx.function_call, "call_id", "")
        key = operation_key(self.current_turn, call_id)
        result = await self.backend.post(
            "/internal/agent/tool/weather",
            json={"city": city, "country": country, "turn_id": self.current_turn, "request_id": key},
        )
        result.raise_for_status()
        return result.json()

    def _disarm_memory_proposal(self) -> None:
        self.memory_proposal = None
        self.memory_clarify = False
        self.memory_question_turn = None
        self.memory_prompted_key = None
        self.memory_question_key = None

    async def _memory_tool(self, path: str, ctx: RunContext, **arguments: str) -> dict:
        """POST one memory tool call; a failure is reported as not-done, never raised as success."""
        if not self.current_turn:
            raise ValueError("TOOL_TURN_REQUIRED")
        call_id = getattr(ctx.function_call, "call_id", "")
        try:
            response = await self.backend.post(
                f"/internal/agent/tool/{path}",
                json={
                    **arguments,
                    "turn_id": self.current_turn,
                    "request_id": operation_key(self.current_turn, call_id),
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return {"error": "MEMORY_TOOL_FAILED", "message": FAILED_NOTE}
        if not isinstance(body, dict) or "error" in body and body.get("status") != "rejected":
            return {"error": "MEMORY_TOOL_FAILED", "message": FAILED_NOTE}
        return body

    @function_tool()
    async def propose_memory(self, ctx: RunContext, fact: str):
        """Propose remembering a lasting fact the user stated about themselves, written in first person (for example "I live in Benghazi"). This only proposes: the user is asked to confirm and nothing is saved until they do."""
        if self.current_turn is not None and self.current_turn == self.decision_turn:
            return DECISION_TURN_NOTE
        body = await self._memory_tool("memory-propose", ctx, fact=fact)
        proposal = body.get("proposal")
        if body.get("status") == "proposed" and isinstance(proposal, dict) and isinstance(proposal.get("token"), str):
            self._disarm_memory_proposal()
            self.memory_proposal = proposal
            self.memory_question_turn = self.current_turn
            self.tool_armed_turn = self.current_turn
            self.forced_speech = None
        return str(body.get("message") or FAILED_NOTE)

    @function_tool()
    async def propose_forget(self, ctx: RunContext, query: str):
        """Propose excluding a stored memory from this conversation. Pass only a short search query describing it (for example "tea"). This only proposes: the user is asked to confirm and nothing is changed until they do."""
        if self.current_turn is not None and self.current_turn == self.decision_turn:
            return DECISION_TURN_NOTE
        body = await self._memory_tool("memory-forget", ctx, query=query)
        spoken = body.get("spoken")
        if (
            isinstance(spoken, str)
            and spoken
            and "error" not in body
            and body.get("status") in {"hidden", "not_hidden"}
        ):
            # A forget request retires any remember proposal; only one may be armed.
            self._disarm_memory_proposal()
            self.tool_armed_turn = self.current_turn
            self.forced_speech = (self.current_turn or "", spoken)
        return str(body.get("message") or FAILED_NOTE)

    @function_tool()
    async def recall(self, ctx: RunContext, query: str):
        """Look up what is saved about the user for a topic (for example "my job"). Read-only."""
        body = await self._memory_tool("memory-recall", ctx, query=query)
        return json.dumps(body, ensure_ascii=False)


async def publish_command(room, command: Mapping[str, Any]) -> None:
    """One authoritative result for the shared UI store (effect already committed)."""
    await room.local_participant.publish_data(json.dumps(dict(command)).encode(), reliable=True, topic="sarjy.command")


class RevealState:
    """Whether the client is waiting for a spoken spoiler confirmation."""

    pending: bool = False
    active: bool = False


def resolve_say_request(data, participants) -> tuple[str | None, str | None]:
    """Authenticate a sarjy.say RPC and validate its payload."""
    caller = participants.get(data.caller_identity)
    if caller is None or caller.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
        return None, "SARJY_SAY_FORBIDDEN"
    try:
        return parse_say_payload(data.payload), None
    except ValueError:
        return None, "SARJY_SAY_PAYLOAD_INVALID"


async def perform_say(session, data, participants) -> str:
    """Speak a validated reply without adding it to conversation history."""
    text, error = resolve_say_request(data, participants)
    if error is not None or text is None:
        return json.dumps({"ok": False, "code": error})
    try:
        await session.say(text, allow_interruptions=True, add_to_chat_ctx=False)
    except RuntimeError:
        return json.dumps({"ok": False, "code": "SARJY_SAY_UNAVAILABLE"})
    return json.dumps({"ok": True})


def register_voice_controls(ctx, session, reveal: RevealState) -> None:
    """Expose the client-owned speech RPC and the reveal-pending control flag."""

    @ctx.room.local_participant.register_rpc_method("sarjy.say")
    async def say(data: rtc.RpcInvocationData):
        # Client-owned, non-LLM speech for exact commands. add_to_chat_ctx is
        # false so game replies never become conversation history.
        return await perform_say(session, data, ctx.room.remote_participants)

    @ctx.room.on("data_received")
    def on_data(packet: rtc.DataPacket):
        apply_control(packet, reveal)


def apply_control(packet, reveal: RevealState) -> None:
    """Accept only human control messages, and only the supported flags."""
    if packet.topic != "sarjy.control" or packet.participant is None:
        return
    if packet.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
        return
    try:
        value = json.loads(packet.data)
    except (TypeError, ValueError):
        return
    if not isinstance(value, dict):
        return
    if value.get("type") == "wordle.reveal_pending":
        reveal.pending = value.get("value") is True
    elif value.get("type") == "wordle.pending":
        # A spoken yes/no is awaited for a pending action ("reveal" or "reset"); null clears it.
        reveal.pending = value.get("value") in {"reveal", "reset"}
    elif value.get("type") == "wordle.active":
        reveal.active = value.get("value") is True
        if not reveal.active:
            reveal.pending = False


INTRO_WAIT_SECONDS = 15


async def play_greeting(session, backend, intro: Mapping[str, Any] | None, wait_for_user) -> str:
    """Speak the deterministic greeting once the user is in the room; no model turn is started.

    Rule: the full introduction is marked seen on the API only when it played to the end. If
    the user interrupts it, or speech fails (budget, TTS), it is not marked and plays again
    next call. The one-line greeting is never marked. Spoken text goes through the agent's
    tts_node, so characters are reserved in the shared speech budget like any other reply, and
    LiveKit transcription shows it in captions. Returns what happened, for tests and logs.
    """
    if not intro or not isinstance(intro.get("text"), str) or not intro["text"]:
        return "skipped"
    try:
        await asyncio.wait_for(wait_for_user(), timeout=INTRO_WAIT_SECONDS)
        handle = session.say(intro["text"], allow_interruptions=True, add_to_chat_ctx=False)
        await handle.wait_for_playout()
    except (TimeoutError, RuntimeError, ValueError, httpx.HTTPError):
        return "not_played"
    if handle.interrupted or handle.exception():
        return "interrupted"
    if intro.get("kind") == "full":
        try:
            (await backend.post("/internal/agent/intro-played")).raise_for_status()
        except httpx.HTTPError:
            return "mark_failed"
    return "played"


@server.rtc_session(agent_name="sarjy")
async def entrypoint(ctx: JobContext):
    values = read_worker_environment()
    require_values(values, "API_INTERNAL_URL", "AGENT_SERVICE_SECRET")
    session_id = json.loads(ctx.job.metadata)["session_id"]
    headers = {
        "x-agent-secret": values["AGENT_SERVICE_SECRET"],
        "x-session-id": session_id,
        "x-room-name": ctx.job.room.name,
    }
    reveal = RevealState()
    async with httpx.AsyncClient(base_url=values["API_INTERNAL_URL"], headers=headers, timeout=15) as backend:
        response = await backend.post("/internal/agent/bootstrap")
        response.raise_for_status()
        bootstrap = response.json()
        config = bootstrap["pipeline"]

        async def notify(code):
            await ctx.room.local_participant.publish_data(
                json.dumps({"code": code}).encode(), reliable=True, topic="sarjy.error"
            )

        providers = build_providers(
            config,
            provider_credentials(
                values, config["llm"]["provider"], (config.get("tts") or {}).get("provider", "elevenlabs_direct")
            ),
        )
        fallback_llm = providers.pop("fallback_llm", None)
        agent = SarjyAgent(
            backend,
            config,
            instructions=bootstrap["instructions"],
            notify=notify,
            fallback_llm=fallback_llm,
            publish=partial(publish_command, ctx.room),
            publish_wordle=partial(publish_wordle, ctx.room),
            wordle_active=lambda: reveal.active,
            is_command=lambda text: is_wordle_command(text, reveal_pending=reveal.pending, wordle_active=reveal.active),
        )
        session = AgentSession(
            **providers,
            vad=silero.VAD.load(),
            preemptive_generation=False,
            conn_options=SessionConnectOptions(
                llm_conn_options=APIConnectOptions(max_retry=0), tts_conn_options=APIConnectOptions(max_retry=0)
            ),
        )
        await ctx.connect()

        @ctx.room.local_participant.register_rpc_method("sarjy.interrupt")
        async def interrupt(data: rtc.RpcInvocationData):
            await session.interrupt()
            return "ok"

        register_voice_controls(ctx, session, reveal)

        gone = asyncio.Event()

        @ctx.room.on("disconnected")
        def on_room_disconnected(*_):
            gone.set()

        empty_since: float | None = None

        async def watchdog():
            nonlocal empty_since
            while True:
                humans = [
                    p
                    for p in ctx.room.remote_participants.values()
                    if p.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                ]
                now = asyncio.get_running_loop().time()
                if humans:
                    empty_since = None
                elif empty_since is None:
                    empty_since = now
                elif now - empty_since >= 60:
                    gone.set()
                    return
                await asyncio.sleep(2)

        await session.start(
            agent, room=ctx.room, record=False, room_options=room_io.RoomOptions(close_on_disconnect=False)
        )
        greeting = asyncio.create_task(
            play_greeting(session, backend, bootstrap.get("intro"), lambda: ctx.wait_for_participant())
        )
        agent.pending.add(greeting)
        greeting.add_done_callback(agent.pending.discard)
        expiry = datetime.fromisoformat(bootstrap["expires_at"])
        remaining = max(0, (expiry - datetime.now(UTC)).total_seconds())
        try:
            monitor = asyncio.create_task(watchdog())
            try:
                await asyncio.wait_for(gone.wait(), timeout=remaining)
            except TimeoutError:
                pass
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
        finally:
            await session.aclose()
            greeting.cancel()
            await agent.stop_speech_refills()
            if agent.pending:
                await asyncio.gather(*agent.pending, return_exceptions=True)
            if fallback_llm is not None and hasattr(fallback_llm, "aclose"):
                await fallback_llm.aclose()
            ctx.shutdown()


if __name__ == "__main__":
    load_dotenv()
    cli.run_app(server)
