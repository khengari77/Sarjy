"""LiveKit worker. Run from the repository root with uv run ... dev."""
import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentSession, AgentServer, JobContext, cli, llm, function_tool, APIConnectOptions, SessionConnectOptions, room_io
from livekit.plugins import silero
from provider_factory import build_providers
from config import read_worker_environment, require_values

server = AgentServer()

# D2.9 tier 5 ceiling. The worker refuses anything above the published caps even
# if a stale or tampered backend payload asks for it.
MAX_TIER_OUTPUT_TOKENS = 1024
MAX_CONTEXT_BYTES = 64000
MAX_TOOLS = 1


def openai_request_within_bounds(serialized: bytes, tools: list, max_output_tokens: int) -> bool:
    """Pure budget guard: 64KB context, one fixed tool and at most the tier-5 cap."""
    return len(serialized) <= MAX_CONTEXT_BYTES and len(tools) <= MAX_TOOLS and max_output_tokens <= MAX_TIER_OUTPUT_TOKENS


class SarjyAgent(Agent):
    def __init__(self, backend: httpx.AsyncClient, pipeline: Mapping[str, Any], instructions: str, notify=None):
        super().__init__(instructions=instructions)
        self.backend = backend
        self.pipeline = pipeline
        self.notify = notify
        self.prepared: dict[str, dict] = {}
        self.current_turn: str | None = None
        self.pending: set[asyncio.Task] = set()
        self.speeches: set[str] = set()

    async def compile(self, message):
        if message.id not in self.prepared:
            response = await self.backend.post('/internal/agent/turn/start', json={'request_id':message.id, 'text':message.text_content})
            response.raise_for_status()
            self.prepared[message.id] = response.json()
        result = self.prepared[message.id]
        self.current_turn = result['turn_id']
        return result['context']

    async def reserve(self, provider: str, characters: int = 0):
        if provider == 'elevenlabs' and characters > 500:
            if self.notify:
                await self.notify('DAILY_BUDGET_EXHAUSTED')
            raise ValueError('DAILY_BUDGET_EXHAUSTED')
        response = await self.backend.post('/internal/development/reserve', json={'provider':provider,'characters':characters})
        if not response.is_success:
            if self.notify:
                await self.notify('DAILY_BUDGET_EXHAUSTED' if response.status_code == 429 else 'BUDGET_SERVICE_UNAVAILABLE')
            response.raise_for_status()

    async def tts_node(self, text, model_settings):
        async def budgeted_text():
            buffer = ''
            async for part in text:
                buffer += part
                if len(buffer) >= 64:
                    # Charge before forwarding text. Interrupted/failed reservations are retained.
                    await self.reserve('elevenlabs', len(buffer))
                    yield buffer
                    buffer = ''
            if buffer:
                await self.reserve('elevenlabs', len(buffer))
                yield buffer
        async for frame in Agent.default.tts_node(self, budgeted_text(), model_settings):
            yield frame

    async def on_user_turn_completed(self, turn_ctx, new_message):
        await self.compile(new_message)

    async def llm_node(self, chat_ctx, tools, model_settings):
        # Also covers typed input and repeated inference after tool calls.
        user = next((item for item in reversed(chat_ctx.items) if isinstance(item, llm.ChatMessage) and item.role == 'user'), None)
        if user is None:
            return
        snapshot = await self.compile(user)
        handle = self.session.current_speech
        if handle and handle.id not in self.speeches:
            self.speeches.add(handle.id)
            turn_id = self.prepared[user.id]['turn_id']
            async def persist_playout():
                await handle.wait_for_playout()
                messages = [item for item in handle.chat_items if isinstance(item, llm.ChatMessage) and item.role == 'assistant']
                text = '\n'.join(item.text_content or '' for item in messages)
                status = 'failed' if handle.exception() else ('interrupted' if handle.interrupted else 'completed')
                for attempt in range(3):
                    try:
                        response = await self.backend.post('/internal/agent/turn/finalize', json={'turn_id':turn_id,'text':text,'status':status})
                        response.raise_for_status()
                        return
                    except httpx.HTTPError:
                        if attempt == 2:
                            logging.error('Turn persistence failed after three attempts')
                            return
                        await asyncio.sleep(0.25 * (attempt+1))
            task = asyncio.create_task(persist_playout())
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
        fresh = llm.ChatContext()
        for message in snapshot['messages']:
            fresh.add_message(role=message['role'], content=message['content'])
        index = chat_ctx.items.index(user)
        fresh.items.extend(chat_ctx.items[index+1:])
        if self.pipeline['llm']['provider'] == 'openai_direct':
            serialized = json.dumps(fresh.to_dict(), ensure_ascii=False, default=str).encode()
            # $0.02 reservation covers <=64KB context, the one fixed weather tool
            # and at most the tier-5 cap (1024), the widest published policy.
            if not openai_request_within_bounds(serialized, tools, self.pipeline['max_output_tokens']):
                raise ValueError('OPENAI_REQUEST_EXCEEDS_DEVELOPMENT_BOUNDS')
            await self.reserve('openai')
        async for chunk in Agent.default.llm_node(self, fresh, tools, model_settings):
            yield chunk

    @function_tool()
    async def get_current_weather(self, city: str, country: str | None = None):
        """Get current weather for an explicitly identified city. Use an ISO 3166-1 alpha-2 country code such as LY when a country is known. Ask for city if missing."""
        result = await self.backend.post('/internal/agent/tool/weather', json={'city':city,'country':country,'turn_id':self.current_turn,'request_id':str(uuid4())})
        result.raise_for_status()
        return result.json()

@server.rtc_session(agent_name='sarjy')
async def entrypoint(ctx: JobContext):
    values = read_worker_environment()
    require_values(values, 'API_INTERNAL_URL', 'AGENT_SERVICE_SECRET')
    session_id = json.loads(ctx.job.metadata)['session_id']
    headers = {'x-agent-secret':values['AGENT_SERVICE_SECRET'], 'x-session-id':session_id, 'x-room-name':ctx.job.room.name}
    async with httpx.AsyncClient(base_url=values['API_INTERNAL_URL'], headers=headers, timeout=15) as backend:
        response = await backend.post('/internal/agent/bootstrap')
        response.raise_for_status()
        bootstrap = response.json()
        config = bootstrap['pipeline']
        async def notify(code):
            await ctx.room.local_participant.publish_data(json.dumps({'code':code}).encode(), reliable=True, topic='sarjy.error')
        agent = SarjyAgent(backend, config, instructions=bootstrap['instructions'], notify=notify)
        session = AgentSession(
            **build_providers(config, require_values(values, 'DEEPGRAM_API_KEY', 'ELEVEN_API_KEY', 'OPENAI_API_KEY' if config['llm']['provider']=='openai_direct' else 'DEEPSEEK_API_KEY')),
            vad=silero.VAD.load(), preemptive_generation=False,
            conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(max_retry=0), tts_conn_options=APIConnectOptions(max_retry=0)),
        )
        await ctx.connect()
        @ctx.room.local_participant.register_rpc_method('sarjy.interrupt')
        async def interrupt(data: rtc.RpcInvocationData):
            await session.interrupt()
            return 'ok'

        gone = asyncio.Event()
        @ctx.room.on('disconnected')
        def on_room_disconnected(*_):
            gone.set()
        empty_since: float | None = None
        async def watchdog():
            nonlocal empty_since
            while True:
                humans = [p for p in ctx.room.remote_participants.values() if p.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT]
                now = asyncio.get_running_loop().time()
                if humans:
                    empty_since = None
                elif empty_since is None:
                    empty_since = now
                elif now - empty_since >= 60:
                    gone.set()
                    return
                await asyncio.sleep(2)

        await session.start(agent, room=ctx.room, record=False, room_options=room_io.RoomOptions(close_on_disconnect=False))
        expiry = datetime.fromisoformat(bootstrap['expires_at'])
        remaining = max(0,(expiry-datetime.now(timezone.utc)).total_seconds())
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
            ctx.shutdown()

if __name__ == '__main__':
    load_dotenv()
    cli.run_app(server)
