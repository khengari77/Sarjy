"""Exercise worker fallback through the real SDK adapter and SSE observer."""
import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

import main as worker
from generation import GenerationFailure
from provider_factory import build_llm
from stream_completion import ObservedTransport
from test_generation import events, request


@pytest.mark.parametrize('partial', [False, True])
def test_native_stream_failure_respects_output_boundary(partial):
    async def run():
        order = []
        def primary_transport(req):
            order.append('primary')
            text = 'data: {"id":"r","choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n' if partial else ''
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=text)
        def fallback_transport(req):
            order.append('fallback')
            assert order[-2] == 'reserve:openai'
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=events('openai_direct'))
        async with httpx.AsyncClient(transport=ObservedTransport(httpx.MockTransport(primary_transport))) as primary_http, httpx.AsyncClient(transport=httpx.MockTransport(fallback_transport)) as fallback_http:
            primary_client = openai.AsyncOpenAI(api_key='test', max_retries=0, http_client=primary_http)
            fallback_client = openai.AsyncOpenAI(api_key='test', max_retries=0, http_client=fallback_http)
            keys = {'DEEPSEEK_API_KEY':'test', 'OPENAI_API_KEY':'test'}
            primary = build_llm({'llm':{'provider':'deepseek_direct','model':'deepseek-flash'}}, keys, client=primary_client)
            fallback = build_llm({'llm':{'provider':'openai_direct','model':'gpt-6-luna'}}, keys, client=fallback_client)
            async def reserve(provider):
                order.append('reserve:' + provider)
            agent = SimpleNamespace(pipeline={'llm':{'provider':'deepseek_direct'}}, session=SimpleNamespace(llm=primary), fallback_llm=fallback, reserve=reserve, _require_openai_bounds=lambda req, tools: None, _require_deepseek_bounds=lambda req: None)
            agent._stream_attempt = worker.SarjyAgent._stream_attempt.__get__(agent)
            agent._stream_with_fallback = worker.SarjyAgent._stream_with_fallback.__get__(agent)
            try:
                if partial:
                    with pytest.raises(GenerationFailure):
                        _ = [chunk async for chunk in agent._stream_with_fallback(request(), [], None)]
                    assert order == ['reserve:deepseek', 'primary']
                else:
                    chunks = [chunk async for chunk in agent._stream_with_fallback(request(), [], None)]
                    assert chunks and order == ['reserve:deepseek', 'primary', 'reserve:openai', 'fallback']
            finally:
                await primary.aclose()
                await fallback.aclose()
    asyncio.run(run())
