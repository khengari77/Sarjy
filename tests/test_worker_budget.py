import asyncio
import httpx
import pytest
from livekit.agents import Agent
from main import SarjyAgent


def test_tts_reserves_before_text_reaches_provider(monkeypatch):
    events=[]
    def backend(request):
        assert request.url.path == '/internal/development/reserve'
        events.append('reserved')
        return httpx.Response(200,json={})
    async def synthesize(agent, text, model_settings):
        async for part in text:
            assert events[-1] == 'reserved'
            events.append(part)
            yield 'audio'
    monkeypatch.setattr(Agent.default,'tts_node',synthesize)
    async def text():
        yield 'مرحبا'
    async def run():
        async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(backend)) as client:
            agent=SarjyAgent(client,{'llm':{'provider':'openai_direct'}})
            assert [frame async for frame in agent.tts_node(text(),None)] == ['audio']
    asyncio.run(run())
    assert events == ['reserved','مرحبا']


def test_exhausted_budget_does_not_forward_synthesis_text(monkeypatch):
    forwarded=[]
    notifications=[]
    async def synthesize(agent,text,model_settings):
        async for part in text:
            forwarded.append(part)
            yield 'audio'
    monkeypatch.setattr(Agent.default,'tts_node',synthesize)
    async def text():
        yield 'Hello'
    async def notify(code):
        notifications.append(code)
    async def run():
        async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(lambda request:httpx.Response(429,json={}))) as client:
            agent=SarjyAgent(client,{'llm':{'provider':'openai_direct'}},notify)
            with pytest.raises(httpx.HTTPStatusError):
                [frame async for frame in agent.tts_node(text(),None)]
    asyncio.run(run())
    assert forwarded == []
    assert notifications == ['DAILY_BUDGET_EXHAUSTED']


def test_expanded_pronunciation_is_reserved_before_synthesis(monkeypatch):
    requests=[]
    forwarded=[]
    def backend(request):
        import json
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={})
    async def synthesize(agent,text,model_settings):
        async for part in text:
            assert requests[-1]['characters'] == len(part)
            forwarded.append(part)
            yield 'audio'
    monkeypatch.setattr(Agent.default,'tts_node',synthesize)
    async def text():
        for part in ['It is 20°','C, wind 15 k','m/','h.']:
            yield part
    async def run():
        async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(backend)) as client:
            agent=SarjyAgent(client,{'llm':{'provider':'openai_direct','language':'en'}})
            assert [frame async for frame in agent.tts_node(text(),None)] == ['audio']
    asyncio.run(run())
    spoken=''.join(forwarded)
    assert spoken == 'It is 20 degrees Celsius, wind 15 kilometres per hour.'
    assert sum(r['characters'] for r in requests) == len(spoken)


def test_expanded_text_cannot_bypass_denied_budget(monkeypatch):
    forwarded=[]
    async def synthesize(agent,text,model_settings):
        async for part in text:
            forwarded.append(part)
            yield 'audio'
    monkeypatch.setattr(Agent.default,'tts_node',synthesize)
    async def text():
        yield '20°C'
    async def run():
        async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(lambda request:httpx.Response(429,json={}))) as client:
            agent=SarjyAgent(client,{'llm':{'provider':'openai_direct'}})
            with pytest.raises(httpx.HTTPStatusError):
                [frame async for frame in agent.tts_node(text(),None)]
    asyncio.run(run())
    assert forwarded == []
