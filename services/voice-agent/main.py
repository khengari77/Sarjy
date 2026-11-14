"""LiveKit worker. Run from the repository root with uv run ... dev."""

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from app.domain.budget import DEEPSEEK_MAX_REQUEST_BYTES
from app.domain.contracts import AssistantContinuation, ModelRequest, ModelToolItem, ToolSpec
from app.domain.memory_capture import is_referential
from app.domain.tool_operations import operation_key
from config import provider_credentials, read_worker_environment, require_values
from dotenv import load_dotenv
from fallback import fallback_decision, next_attempt
from generation import GenerationEvent, GenerationFailure, adapter_for, livekit_chunk
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
from provider_factory import build_providers
from snapshot_request import snapshot_request

server = AgentServer()

# D2.9 tier 5 ceiling. The worker refuses anything above the published caps even
# if a stale or tampered backend payload asks for it.
MAX_TIER_OUTPUT_TOKENS = 1024
MAX_CONTEXT_BYTES = 64000
MAX_TOOLS = 1
GENERATION_ATTEMPT_SECONDS = 30.0


def deepseek_request_within_bounds(serialized: bytes) -> bool:
    return len(serialized) <= DEEPSEEK_MAX_REQUEST_BYTES


def openai_request_within_bounds(serialized: bytes, tools: list, max_output_tokens: int) -> bool:
    """Pure budget guard: 64KB context, one fixed tool and at most the tier-5 cap."""
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


class SarjyAgent(Agent):
    def __init__(
        self, backend: httpx.AsyncClient, pipeline: Mapping[str, Any], instructions: str, notify=None, fallback_llm=None
    ):
        super().__init__(instructions=instructions)
        self.backend = backend
        self.pipeline = pipeline
        self.notify = notify
        self.fallback_llm = fallback_llm
        self.prepared: dict[str, dict] = {}
        self.current_turn: str | None = None
        self.pending: set[asyncio.Task] = set()
        self.speeches: set[str] = set()
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

    @staticmethod
    def _proposal_key(proposal: Mapping[str, Any] | None) -> tuple[Any, Any]:
        if proposal is None:
            return (None, None)
        return (proposal.get("confirmation_id"), proposal.get("confirmation_revision"))

    async def compile(self, message):
        if message.id not in self.prepared:
            response = await self.backend.post(
                "/internal/agent/turn/start", json={"request_id": message.id, "text": message.text_content}
            )
            response.raise_for_status()
            self.prepared[message.id] = response.json()
        result = self.prepared[message.id]
        self.current_turn = result["turn_id"]
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
                        # capture candidate for the same turn.
                        decided = True
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

    async def reserve(self, provider: str, characters: int = 0):
        if provider == "elevenlabs" and characters > 500:
            if self.notify:
                await self.notify("DAILY_BUDGET_EXHAUSTED")
            raise ValueError("DAILY_BUDGET_EXHAUSTED")
        response = await self.backend.post(
            "/internal/development/reserve", json={"provider": provider, "characters": characters}
        )
        if not response.is_success:
            if self.notify:
                await self.notify(
                    "DAILY_BUDGET_EXHAUSTED" if response.status_code == 429 else "BUDGET_SERVICE_UNAVAILABLE"
                )
            response.raise_for_status()

    async def tts_node(self, text, model_settings):
        async def budgeted_text():
            buffer = ""
            async for part in text:
                buffer += part
                if len(buffer) >= 64:
                    # Charge before forwarding text. Interrupted/failed reservations are retained.
                    await self.reserve("elevenlabs", len(buffer))
                    yield buffer
                    buffer = ""
            if buffer:
                await self.reserve("elevenlabs", len(buffer))
                yield buffer

        async for frame in Agent.default.tts_node(self, budgeted_text(), model_settings):
            yield frame

    async def on_user_turn_completed(self, turn_ctx, new_message):
        await self.compile(new_message)

    async def llm_node(self, chat_ctx, tools, model_settings):
        # Also covers typed input and repeated inference after tool calls.
        user = _user_message(chat_ctx)
        if user is None:
            return
        snapshot = await self.compile(user)
        proposal = self.memory_proposal
        key = self._proposal_key(proposal)
        if proposal is not None and self.memory_question_key != key:
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
        request = snapshot_request(snapshot, self.current_turn)
        schemas = [build_legacy_openai_schema(tool)["function"] for tool in tools]
        request = _with_continuation(request, _continuation(chat_ctx, user), schemas)
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
        async for chunk in self._stream_with_fallback(
            request, tools, model_settings.tool_choice if model_settings else None
        ):
            yield chunk

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

    def _schedule_persistence(self, user, handle):
        if handle is None or handle.id in self.speeches:
            # One finalize per spoken handle, even across repeated inference.
            return
        self.speeches.add(handle.id)
        turn_id = self.prepared[user.id]["turn_id"]

        async def persist_playout():
            text, status = await _playout_result(handle)
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

    async def _stream_with_fallback(self, request, tools, tool_choice):
        primary_kind = self.pipeline["llm"]["provider"]
        emitted = False
        if primary_kind == "openai_direct":
            self._require_openai_bounds(request, tools)
            await self.reserve("openai")
        elif primary_kind == "deepseek_direct":
            self._require_deepseek_bounds(request)
            await self.reserve("deepseek")
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
            await self.reserve("openai")
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
    async with httpx.AsyncClient(base_url=values["API_INTERNAL_URL"], headers=headers, timeout=15) as backend:
        response = await backend.post("/internal/agent/bootstrap")
        response.raise_for_status()
        bootstrap = response.json()
        config = bootstrap["pipeline"]

        async def notify(code):
            await ctx.room.local_participant.publish_data(
                json.dumps({"code": code}).encode(), reliable=True, topic="sarjy.error"
            )

        providers = build_providers(config, provider_credentials(values, config["llm"]["provider"]))
        fallback_llm = providers.pop("fallback_llm", None)
        agent = SarjyAgent(
            backend, config, instructions=bootstrap["instructions"], notify=notify, fallback_llm=fallback_llm
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
            if agent.pending:
                await asyncio.gather(*agent.pending, return_exceptions=True)
            if fallback_llm is not None and hasattr(fallback_llm, "aclose"):
                await fallback_llm.aclose()
            ctx.shutdown()


if __name__ == "__main__":
    load_dotenv()
    cli.run_app(server)
