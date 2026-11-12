import asyncio
import json
import pytest
import httpx
import openai as sdk
from stream_completion import ObservedTransport
from livekit.agents import llm, function_tool
from livekit.plugins import openai
from app.domain.contracts import ModelRequest, ContextSelection, ResponsePolicy, UserSettings
from generation import adapter_for, GenerationFailure
from provider_factory import build_llm


@function_tool()
async def weather(city: str):
    """Read weather for a city."""
    return {'city': city}


def request(tools=(), history=()):
    from livekit.agents.llm.utils import build_legacy_openai_schema
    return ModelRequest(turn_id='turn', attempt_id='attempt', instructions='Protected rules',
        selection=ContextSelection(revision=7, excluded_ids={'excluded'}),
        response_policy=ResponsePolicy.for_tier(2), settings=UserSettings(revision=3), settings_revision=3,
        messages=({'role': 'system', 'content': 'Protected rules'},
                  {'role': 'system', 'content': 'Permitted memory: Tripoli; version 2'},
                  {'role': 'user', 'content': 'Weather?'}),
        tools=tuple(build_legacy_openai_schema(tool)['function'] for tool in tools), tool_history=history)


def events(kind, tool=False):
    if kind == 'deepseek_direct':
        chunks = [{'id': 'r', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'Hello'}, 'finish_reason': None}]}]
        if tool:
            chunks += [{'id': 'r', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function', 'function': {'name': 'weather', 'arguments': '{"city":'}}]}, 'finish_reason': None}]},
                       {'id': 'r', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"Tripoli"}'}}]}, 'finish_reason': 'tool_calls'}]}]
        else:
            chunks += [{'id': 'r', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
        chunks += [{'id': 'r', 'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}]
        return ''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
    response = {'id': 'r', 'output': [], 'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}
    chunks = [{'type': 'response.created', 'response': {'id': 'r'}}, {'type': 'response.output_text.delta', 'delta': 'Hello'}]
    if tool:
        item = {'type': 'function_call', 'id': 'fc', 'call_id': 'call', 'name': 'weather', 'arguments': '{"city":"Tripoli"}', 'status': 'completed'}
        chunks += [{'type': 'response.function_call_arguments.delta', 'item_id': 'fc', 'delta': '{"city":'}, {'type': 'response.function_call_arguments.delta', 'item_id': 'fc', 'delta': '"Tripoli"}'}, {'type': 'response.output_item.done', 'item': item}]
        response['output'] = [item]
    chunks += [{'type': 'response.completed', 'response': response}]
    return ''.join('event: '+chunk['type']+'\ndata: '+json.dumps(chunk)+'\n\n' for chunk in chunks)


async def run_stream(kind, transport, req, tools=()):
    async with httpx.AsyncClient(transport=ObservedTransport(transport)) as http:
        client = sdk.AsyncOpenAI(api_key='test', base_url='https://provider.test/v1', max_retries=0, http_client=http)
        provider = build_llm({'llm': {'provider': kind,
            'model': 'gpt-6-luna' if kind == 'openai_direct' else 'deepseek-flash'}},
            {'OPENAI_API_KEY': 'test', 'DEEPSEEK_API_KEY': 'test'}, client=client)
        try:
            return [event async for event in adapter_for(provider, kind).stream(req, tools)]
        finally:
            await provider.aclose()


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
@pytest.mark.parametrize('with_tool', [False, True])
def test_native_transport_payload_and_normalized_stream(kind, with_tool):
    captured = []
    tools = [weather] if with_tool else []
    req = request(tools)
    before = req.model_dump_json()
    def transport(http_request):
        captured.append(json.loads(http_request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=events(kind, with_tool))
    result = asyncio.run(run_stream(kind, httpx.MockTransport(transport), req, tools))
    assert req.model_dump_json() == before
    assert len(captured) == 1
    payload = captured[0]
    assert payload['stream'] is True
    assert 'Protected rules' in json.dumps(payload) and 'Permitted memory' in json.dumps(payload)
    assert 'excluded' not in json.dumps(payload)
    assert payload['max_output_tokens' if kind == 'openai_direct' else 'max_tokens'] == 128
    if kind == 'deepseek_direct':
        assert payload['thinking'] == {'type': 'disabled'}
    else:
        assert payload['store'] is False and payload['reasoning']['effort'] == 'none'
        assert 'previous_response_id' not in payload
    assert ''.join(event.text for event in result if event.kind == 'text') == 'Hello'
    assert [event.kind for event in result][-1] == 'completed'
    assert dict(next(event.usage for event in result if event.kind == 'usage'))['total_tokens'] == 12
    assert all(event.turn_id == 'turn' and event.attempt_id == 'attempt' for event in result)
    if with_tool:
        call = next(event for event in result if event.kind == 'tool')
        assert call.call_id == 'call' and call.name == 'weather'
        assert json.loads(call.arguments) == {'city': 'Tripoli'}


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
def test_provider_error_has_no_hidden_retry(kind):
    calls = []
    def transport(http_request):
        calls.append(http_request)
        return httpx.Response(503, json={'error': {'message': 'unavailable'}})
    with pytest.raises(GenerationFailure) as error:
        asyncio.run(run_stream(kind, httpx.MockTransport(transport), request()))
    assert len(calls) == 1 and error.value.event.partial is False
    assert error.value.event.error_code == 'provider_error'
    assert error.value.event.status_code == 503


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
def test_tool_continuation_preserves_call_identity(kind):
    history = ({'kind': 'call', 'call_id': 'original-call', 'name': 'weather', 'content': '{"city":"Tripoli"}'},
               {'kind': 'result', 'call_id': 'original-call', 'name': 'weather', 'content': '{"temperature":20}'})
    captured = []
    def transport(http_request):
        captured.append(json.loads(http_request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=events(kind))
    asyncio.run(run_stream(kind, httpx.MockTransport(transport), request([weather], history), [weather]))
    wire = json.dumps(captured[0])
    assert wire.count('original-call') == 2
    assert 'temperature' in wire and 'Tripoli' in wire


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
@pytest.mark.parametrize('tier', range(1, 6))
def test_every_tier_reaches_wire_exactly(kind, tier):
    req = request().model_copy(update={'response_policy': ResponsePolicy.for_tier(tier)})
    captured = []
    def transport(http_request):
        captured.append(json.loads(http_request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=events(kind))
    asyncio.run(run_stream(kind, httpx.MockTransport(transport), req))
    assert captured[0]['max_output_tokens' if kind == 'openai_direct' else 'max_tokens'] == req.response_policy.max_output_tokens


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
def test_partial_stream_failure_is_not_retried(kind):
    calls, closed = [], []
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            if kind == 'deepseek_direct':
                yield b'data: {"id":"r","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
            else:
                yield b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
            raise httpx.ReadError('connection lost')
        async def aclose(self):
            closed.append(True)
    def transport(http_request):
        calls.append(True)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Broken())
    with pytest.raises(GenerationFailure) as error:
        asyncio.run(run_stream(kind, httpx.MockTransport(transport), request()))
    assert error.value.event.partial is True
    assert len(calls) == 1 and closed


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
def test_cancellation_closes_native_stream_without_retry(kind):
    async def run():
        entered, closed = asyncio.Event(), asyncio.Event()
        calls = []
        class Stalled(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b''
            async def aclose(self):
                closed.set()
        def transport(http_request):
            calls.append(True)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stalled())
        task = asyncio.create_task(run_stream(kind, httpx.MockTransport(transport), request()))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set() and len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
def test_malformed_stream_fails_without_retry(kind):
    calls = []
    def transport(http_request):
        calls.append(True)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text='data: {invalid-json}\n\n')
    with pytest.raises(GenerationFailure):
        asyncio.run(run_stream(kind, httpx.MockTransport(transport), request()))
    assert len(calls) == 1


def test_request_rejects_mixed_revisions_and_fabricated_tool_results():
    from pydantic import ValidationError
    original = request([weather]).model_dump(mode='json')
    with pytest.raises(ValidationError, match='SETTINGS_REVISION_MISMATCH'):
        ModelRequest.model_validate({**original, 'settings_revision': 99})
    with pytest.raises(ValidationError, match='MODEL_INSTRUCTIONS_MISMATCH'):
        ModelRequest.model_validate({**original, 'instructions': 'Different rules'})
    with pytest.raises(ValidationError, match='INVALID_TOOL_RESULT'):
        ModelRequest.model_validate({**original, 'tool_history': [
            {'kind': 'result', 'call_id': 'fabricated', 'name': 'weather', 'content': '{}'}]})
    with pytest.raises(ValidationError, match='UNSUPPORTED_TOOL_CONTINUATION'):
        ModelRequest.model_validate({**original, 'tool_history': [
            {'kind': 'call', 'call_id': 'call', 'name': 'unknown', 'content': '{}'}]})


@pytest.mark.parametrize('mode', ['missing_finish', 'missing_done', 'length'])
def test_deepseek_incomplete_stream_never_reports_completed(mode):
    payload = events('deepseek_direct')
    if mode == 'missing_finish':
        payload = payload.replace('"finish_reason": "stop"', '"finish_reason": null')
    elif mode == 'missing_done':
        payload = payload.replace('data: [DONE]\n\n', '')
    else:
        payload = payload.replace('"finish_reason": "stop"', '"finish_reason": "length"')
    def transport(req):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=payload)
    with pytest.raises(GenerationFailure) as error:
        asyncio.run(run_stream('deepseek_direct', httpx.MockTransport(transport), request()))
    assert error.value.event.error_code == 'incomplete_response'
    assert error.value.event.partial


@pytest.mark.parametrize('kind', ['openai_direct', 'deepseek_direct'])
def test_assistant_text_survives_tool_continuation_in_order(kind):
    history = ({'kind': 'assistant', 'content': 'Already explained the lookup.', 'interrupted': True},
        {'kind': 'call', 'call_id': 'call', 'name': 'weather', 'content': '{"city":"Tripoli"}'},
        {'kind': 'result', 'call_id': 'call', 'name': 'weather', 'content': '{"temperature":20}'})
    captured = []
    def transport(req):
        captured.append(req.content.decode())
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=events(kind))
    asyncio.run(run_stream(kind, httpx.MockTransport(transport), request([weather], history), [weather]))
    wire = captured[0]
    assert wire.index('Already explained') < wire.index('temperature')


def test_fragmented_crlf_terminal_frames_are_observed():
    from stream_completion import CompletionState, ObservedStream
    async def run():
        state = CompletionState()
        class Pieces(httpx.AsyncByteStream):
            async def __aiter__(self):
                for part in (b'data: {"choices":[{"index":0,"finish_', b'reason":"stop"}]}\r',
                             b'\n\r\ndata: [DO', b'NE]\r\n\r\n'):
                    yield part
        observer = ObservedStream(Pieces(), state)
        assert b''.join([chunk async for chunk in observer])
        assert state.reason == 'stop' and state.done
    asyncio.run(run())


def test_incomplete_stream_does_not_emit_buffered_tool_calls():
    from contextlib import asynccontextmanager
    from stream_completion import completion_state
    from generation import DeepSeekAdapter
    class Provider:
        @asynccontextmanager
        async def chat(self, **kwargs):
            async def chunks():
                yield llm.ChatChunk(id='r', delta=llm.ChoiceDelta(tool_calls=[
                    llm.FunctionToolCall(call_id='call', name='weather', arguments='{"city":"Tripoli"}')]))
                completion_state.get().reason = 'length'
                completion_state.get().done = True
            yield chunks()
    async def run():
        emitted = []
        with pytest.raises(GenerationFailure):
            async for event in DeepSeekAdapter(Provider()).stream(request([weather]), [weather]):
                emitted.append(event)
        assert not any(event.kind in ('tool', 'completed') for event in emitted)
    asyncio.run(run())


def test_terminal_state_is_isolated_between_concurrent_attempts():
    async def run():
        count = 0
        async def transport(req):
            nonlocal count
            count += 1
            number = count
            await asyncio.sleep(0)
            body = events('deepseek_direct')
            if number == 2:
                body = body.replace('data: [DONE]\n\n', '')
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=body)
        results = await asyncio.gather(
            run_stream('deepseek_direct', httpx.MockTransport(transport), request()),
            run_stream('deepseek_direct', httpx.MockTransport(transport), request()), return_exceptions=True)
        assert sum(isinstance(result, GenerationFailure) for result in results) == 1
        assert sum(isinstance(result, list) and result[-1].kind == 'completed' for result in results) == 1
    asyncio.run(run())


def test_factory_owned_deepseek_client_is_closed():
    async def run():
        provider = build_llm({'llm': {'provider': 'deepseek_direct', 'model': 'deepseek-flash'}},
            {'DEEPSEEK_API_KEY': 'test'})
        client = provider.application_client
        await provider.aclose()
        assert client.is_closed()
    asyncio.run(run())


@pytest.mark.parametrize('newline', [b'\r', b'\n', b'\r\n'])
def test_terminal_observer_accepts_standard_sse_line_endings(newline):
    from stream_completion import CompletionState, ObservedStream
    async def run():
        state = CompletionState()
        payload = b'data: {"choices":[{"index":0,"finish_reason":"stop"}]}' + newline*2 + b'data: [DONE]' + newline*2
        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                for value in payload:
                    yield bytes([value])
        stream = ObservedStream(Bytes(), state)
        [part async for part in stream]
        assert state.done and state.reason == 'stop'
    asyncio.run(run())


@pytest.mark.parametrize('encoding', ['gzip', 'deflate'])
def test_terminal_observer_handles_compressed_wire_streams(encoding):
    import gzip, zlib
    raw = events('deepseek_direct').encode()
    compressed = gzip.compress(raw) if encoding == 'gzip' else zlib.compress(raw)
    closed = []
    class Wire(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(compressed), 7):
                yield compressed[index:index+7]
        async def aclose(self):
            closed.append(True)
    def transport(req):
        return httpx.Response(200, headers={'content-type': 'text/event-stream', 'content-encoding': encoding}, stream=Wire())
    result = asyncio.run(run_stream('deepseek_direct', httpx.MockTransport(transport), request()))
    assert result[-1].kind == 'completed' and closed
