import asyncio
from types import SimpleNamespace
import httpx
from livekit.agents import Agent, llm
from main import SarjyAgent


def test_worker_replaces_stale_history_with_authoritative_context(monkeypatch):
    observed = []
    async def inference(agent, context, tools, settings):
        observed.extend(item.text_content for item in context.items)
        yield 'output'
    monkeypatch.setattr(Agent.default, 'llm_node', inference)
    monkeypatch.setattr(SarjyAgent, 'session', property(lambda self: SimpleNamespace(current_speech=None)))
    async def run():
        def backend(request):
            if request.url.path.endswith('/reserve'):
                return httpx.Response(200, json={})
            return httpx.Response(200, json={'turn_id': 'turn', 'context': {
                'revision': 2, 'messages': [
                    {'role': 'system', 'content': 'Protected rules'},
                    {'role': 'user', 'content': 'Current request'}]}})
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {'llm': {'provider': 'openai_direct'}, 'max_output_tokens': 128}, 'Rules')
            context = llm.ChatContext()
            context.add_message(role='assistant', content='Excluded stale city')
            context.add_message(role='user', content='Current request')
            assert [chunk async for chunk in agent.llm_node(context, [], None)] == ['output']
    asyncio.run(run())
    assert observed == ['Protected rules', 'Current request']
