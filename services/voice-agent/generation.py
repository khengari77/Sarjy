"""Immutable request boundary around native provider streaming plugins."""
import asyncio
from dataclasses import dataclass
from typing import Literal

from app.domain.contracts import ModelRequest
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, APITimeoutError, llm
from livekit.agents.llm.utils import build_legacy_openai_schema


@dataclass(frozen=True)
class GenerationEvent:
    kind: Literal['text', 'tool', 'usage', 'completed', 'error', 'cancelled']
    turn_id: str
    attempt_id: str
    text: str = ''
    call_id: str = ''
    name: str = ''
    arguments: str = ''
    usage: tuple[tuple[str, int | str | None], ...] = ()
    partial: bool = False
    error_code: str = ""
    status_code: int | None = None


class GenerationFailure(Exception):
    def __init__(self, event):
        super().__init__('GENERATION_FAILED')
        self.event = event


class GenerationCancelled(asyncio.CancelledError):
    def __init__(self, event):
        super().__init__('GENERATION_CANCELLED')
        self.event = event


class ProviderAdapter:
    cap_field = "max_output_tokens"

    def __init__(self, provider):
        self.provider = provider

    async def stream(self, request: ModelRequest, tools=(), tool_choice=None):
        if not request.messages:
            raise ValueError('MODEL_MESSAGES_REQUIRED')
        actual = [build_legacy_openai_schema(tool)['function'] for tool in tools]
        expected = [tool.model_dump(mode='json') for tool in request.tools]
        if actual != expected:
            raise ValueError('MODEL_TOOL_SCHEMA_MISMATCH')
        context = llm.ChatContext()
        for message in request.messages:
            context.add_message(role=message.role, content=message.content)
        for item in request.tool_history:
            if item.kind == 'call':
                context.items.append(llm.FunctionCall(call_id=item.call_id, name=item.name, arguments=item.content))
            else:
                context.items.append(llm.FunctionCallOutput(call_id=item.call_id, name=item.name,
                    output=item.content, is_error=item.is_error))
        identity = dict(turn_id=request.turn_id, attempt_id=request.attempt_id)
        partial = False
        received = False
        kwargs = {} if tool_choice is None else {'tool_choice': tool_choice}
        try:
            async with self.provider.chat(chat_ctx=context, tools=list(tools),
                    conn_options=APIConnectOptions(max_retry=0),
                    extra_kwargs={self.cap_field: request.response_policy.max_output_tokens}, **kwargs) as stream:
                async for chunk in stream:
                    received = True
                    if chunk.delta:
                        if chunk.delta.content:
                            partial = True
                            yield GenerationEvent('text', **identity, text=chunk.delta.content)
                        for call in chunk.delta.tool_calls:
                            partial = True
                            yield GenerationEvent('tool', **identity, call_id=call.call_id,
                                name=call.name, arguments=call.arguments)
                    if chunk.usage:
                        yield GenerationEvent('usage', **identity, usage=tuple(chunk.usage.model_dump().items()))
            if not received:
                raise ValueError('EMPTY_GENERATION_STREAM')
            yield GenerationEvent('completed', **identity)
        except asyncio.CancelledError as exc:
            raise GenerationCancelled(GenerationEvent('cancelled', **identity, partial=partial)) from exc
        except Exception as exc:
            status = exc.status_code if isinstance(exc, APIStatusError) else None
            if isinstance(exc, APITimeoutError):
                code = 'timeout'
            elif isinstance(exc, APIStatusError):
                code = {400: 'invalid_request', 401: 'authentication', 402: 'insufficient_balance',
                    403: 'authentication', 429: 'rate_limit'}.get(status, 'provider_error')
            elif isinstance(exc, APIConnectionError):
                code = 'connection_error'
            else:
                code = 'invalid_response'
            raise GenerationFailure(GenerationEvent('error', **identity, partial=partial,
                error_code=code, status_code=status)) from exc


def livekit_chunk(event):
    if event.kind == 'text':
        return llm.ChatChunk(id=event.attempt_id, delta=llm.ChoiceDelta(role='assistant', content=event.text))
    if event.kind == 'tool':
        return llm.ChatChunk(id=event.attempt_id, delta=llm.ChoiceDelta(role='assistant', tool_calls=[
            llm.FunctionToolCall(call_id=event.call_id, name=event.name, arguments=event.arguments)]))
    if event.kind == 'usage':
        return llm.ChatChunk(id=event.attempt_id, usage=llm.CompletionUsage(**dict(event.usage)))
    return None


class OpenAIResponsesAdapter(ProviderAdapter):
    cap_field = 'max_output_tokens'


class DeepSeekAdapter(ProviderAdapter):
    cap_field = 'max_tokens'


def adapter_for(provider, kind):
    if kind == 'openai_direct':
        return OpenAIResponsesAdapter(provider)
    if kind == 'deepseek_direct':
        return DeepSeekAdapter(provider)
    raise ValueError('UNSUPPORTED_GENERATION_PROVIDER')
