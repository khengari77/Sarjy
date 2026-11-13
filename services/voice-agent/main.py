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

    async def compile(self, message):
        if message.id not in self.prepared:
            response = await self.backend.post(
                "/internal/agent/turn/start", json={"request_id": message.id, "text": message.text_content}
            )
            response.raise_for_status()
            self.prepared[message.id] = response.json()
        result = self.prepared[message.id]
        self.current_turn = result["turn_id"]
        return result["context"]

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
        handle = self.session.current_speech
        if handle and handle.id not in self.speeches:
            self.speeches.add(handle.id)
            self._schedule_persistence(user, handle)
        request = snapshot_request(snapshot, self.current_turn)
        schemas = [build_legacy_openai_schema(tool)["function"] for tool in tools]
        request = _with_continuation(request, _continuation(chat_ctx, user), schemas)
        async for chunk in self._stream_with_fallback(
            request, tools, model_settings.tool_choice if model_settings else None
        ):
            yield chunk

    def _schedule_persistence(self, user, handle):
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
