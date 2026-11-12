import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
import pytest
from livekit.agents import llm
from main import SarjyAgent
from test_generation import request


@pytest.mark.parametrize('provider', ['openai_direct', 'deepseek_direct'])
@pytest.mark.parametrize('denied', [False, True])
def test_worker_replaces_stale_history_and_reserves_before_provider(monkeypatch, denied, provider):
    observed, order = [], []
    class Provider:
        @asynccontextmanager
        async def chat(self, *, chat_ctx, **kwargs):
            observed.extend(item.text_content for item in chat_ctx.items)
            order.append('provider')
            assert order == ['reserve', 'provider']
            async def chunks():
                if provider == 'deepseek_direct':
                    from stream_completion import completion_state
                    state = completion_state.get()
                    if state is not None:
                        state.done, state.reason = True, 'stop'
                yield llm.ChatChunk(id='r', delta=llm.ChoiceDelta(content='output'))
            yield chunks()
    monkeypatch.setattr(SarjyAgent, 'session', property(lambda self: SimpleNamespace(current_speech=None, llm=Provider())))
    async def run():
        def backend(http_request):
            if http_request.url.path.endswith('/reserve'):
                order.append('reserve')
                return httpx.Response(429 if denied else 200, json={})
            return httpx.Response(200, json={'turn_id': 'turn', 'context': {'model_request': request().model_dump(mode='json'), 'revision': 7, 'messages': [item.model_dump() for item in request().messages]}})
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {'llm': {'provider': provider}, 'max_output_tokens': 128}, 'Rules')
            context = llm.ChatContext()
            context.add_message(role='assistant', content='Excluded stale city')
            context.add_message(role='user', content='Weather?')
            if denied:
                with pytest.raises(httpx.HTTPStatusError):
                    [chunk async for chunk in agent.llm_node(context, [], None)]
            else:
                chunks = [chunk async for chunk in agent.llm_node(context, [], None)]
                assert chunks[0].delta.content == 'output'
    asyncio.run(run())
    if denied:
        assert observed == [] and order == ['reserve']
    else:
        assert observed == ['Protected rules', 'Permitted memory: Tripoli; version 2', 'Weather?']
