"""Immutable request boundary around native provider streaming plugins."""

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from app.domain.contracts import ModelRequest
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, APITimeoutError, llm
from livekit.agents.llm.utils import build_legacy_openai_schema
from stream_completion import CompletionState, completion_state


@dataclass(frozen=True)
class GenerationEvent:
    kind: Literal["text", "tool", "usage", "completed", "error", "cancelled"]
    turn_id: str
    attempt_id: str
    text: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    usage: tuple[tuple[str, int | str | None], ...] = ()
    partial: bool = False
    error_code: str = ""
    status_code: int | None = None


class _Identity(TypedDict):
    turn_id: str
    attempt_id: str


class GenerationFailure(Exception):
    def __init__(self, event: GenerationEvent) -> None:
        super().__init__("GENERATION_FAILED")
        self.event = event


class GenerationCancelled(asyncio.CancelledError):
    def __init__(self, event: GenerationEvent) -> None:
        super().__init__("GENERATION_CANCELLED")
        self.event = event


class ProviderAdapter:
    cap_field = "max_output_tokens"

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    async def stream(
        self, request: ModelRequest, tools: Sequence[Any] = (), tool_choice: Any = None
    ) -> AsyncIterator[GenerationEvent]:
        _validate_request(request, tools)
        context = _build_context(request)
        identity: _Identity = {"turn_id": request.turn_id, "attempt_id": request.attempt_id}
        partial = False
        received = False
        kwargs = {} if tool_choice is None else {"tool_choice": tool_choice}
        state = CompletionState()
        token = completion_state.set(state) if self.cap_field == "max_tokens" else None
        buffered_tools: list[GenerationEvent] = []
        try:
            async with self.provider.chat(
                chat_ctx=context,
                tools=list(tools),
                conn_options=APIConnectOptions(max_retry=0),
                extra_kwargs={self.cap_field: request.response_policy.max_output_tokens},
                **kwargs,
            ) as stream:
                async for chunk in stream:
                    received = True
                    for event in _chunk_events(chunk, identity, buffered_tools):
                        partial = partial or event.kind == "text"
                        yield event
            if _incomplete_response(token, state):
                raise GenerationFailure(
                    GenerationEvent("error", **identity, partial=partial, error_code="incomplete_response")
                )
            for event in buffered_tools:
                partial = True
                yield event
            if not received:
                raise ValueError("EMPTY_GENERATION_STREAM")
            yield GenerationEvent("completed", **identity)
        except GenerationFailure:
            raise
        except asyncio.CancelledError as exc:
            raise GenerationCancelled(GenerationEvent("cancelled", **identity, partial=partial)) from exc
        except Exception as exc:
            code, status = _error_code(exc)
            raise GenerationFailure(
                GenerationEvent("error", **identity, partial=partial, error_code=code, status_code=status)
            ) from exc
        finally:
            if token is not None:
                completion_state.reset(token)


def _chunk_events(chunk: Any, identity: _Identity, buffered_tools: list[GenerationEvent]) -> Iterator[GenerationEvent]:
    if chunk.delta:
        if chunk.delta.content:
            yield GenerationEvent("text", **identity, text=chunk.delta.content)
        for call in chunk.delta.tool_calls:
            buffered_tools.append(
                GenerationEvent("tool", **identity, call_id=call.call_id, name=call.name, arguments=call.arguments)
            )
    if chunk.usage:
        yield GenerationEvent("usage", **identity, usage=tuple(chunk.usage.model_dump().items()))


def _incomplete_response(token: Any, state: CompletionState) -> bool:
    return token is not None and (not state.done or state.reason not in {"stop", "tool_calls"})


def _validate_request(request: ModelRequest, tools: Sequence[Any]) -> None:
    if not request.messages:
        raise ValueError("MODEL_MESSAGES_REQUIRED")
    actual = [build_legacy_openai_schema(tool)["function"] for tool in tools]
    expected = [tool.model_dump(mode="json") for tool in request.tools]
    if actual != expected:
        raise ValueError("MODEL_TOOL_SCHEMA_MISMATCH")


def _build_context(request: ModelRequest) -> llm.ChatContext:
    context = llm.ChatContext()
    for message in request.messages:
        context.add_message(role=message.role, content=message.content)
    for item in request.tool_history:
        if item.kind == "assistant":
            context.add_message(role="assistant", content=item.content, interrupted=item.interrupted)
        elif item.kind == "call":
            context.items.append(llm.FunctionCall(call_id=item.call_id, name=item.name, arguments=item.content))
        else:
            context.items.append(
                llm.FunctionCallOutput(
                    call_id=item.call_id, name=item.name, output=item.content, is_error=item.is_error
                )
            )
    return context


def _error_code(exc: BaseException) -> tuple[str, int | None]:
    status = exc.status_code if isinstance(exc, APIStatusError) else None
    if isinstance(exc, APITimeoutError):
        return "timeout", status
    if isinstance(exc, APIStatusError):
        code = {
            400: "invalid_request",
            401: "authentication",
            402: "insufficient_balance",
            403: "authentication",
            429: "rate_limit",
        }.get(status if status is not None else -1, "provider_error")
        return code, status
    if isinstance(exc, APIConnectionError):
        return "connection_error", status
    return "invalid_response", status


def livekit_chunk(event: GenerationEvent) -> llm.ChatChunk | None:
    if event.kind == "text":
        return llm.ChatChunk(id=event.attempt_id, delta=llm.ChoiceDelta(role="assistant", content=event.text))
    if event.kind == "tool":
        return llm.ChatChunk(
            id=event.attempt_id,
            delta=llm.ChoiceDelta(
                role="assistant",
                tool_calls=[llm.FunctionToolCall(call_id=event.call_id, name=event.name, arguments=event.arguments)],
            ),
        )
    if event.kind == "usage":
        return llm.ChatChunk(id=event.attempt_id, usage=llm.CompletionUsage(**cast(dict[str, Any], dict(event.usage))))
    return None


class OpenAIResponsesAdapter(ProviderAdapter):
    cap_field = "max_output_tokens"


class DeepSeekAdapter(ProviderAdapter):
    cap_field = "max_tokens"


def adapter_for(provider: Any, kind: str) -> ProviderAdapter:
    if kind == "openai_direct":
        return OpenAIResponsesAdapter(provider)
    if kind == "deepseek_direct":
        return DeepSeekAdapter(provider)
    raise ValueError("UNSUPPORTED_GENERATION_PROVIDER")
