import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from livekit.agents import Agent, llm
from main import SarjyAgent
from turn_metrics import assistant_metrics, build_payload, metric_ms

METRICS_PATH = "/internal/agent/turn/metrics"


@pytest.fixture
def worker(tmp_path):
    database = tmp_path / "worker.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database}", agent_service_secret="test-service-secret", _env_file=None
    )
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        conversation = client.post("/v1/conversations").json()["id"]
        _insert_session(database, "session", conversation, owner, "room")
        headers = {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}
        yield client, database, owner, conversation, headers


def _insert_session(database, session_id, conversation_id, owner_id, room_name):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                conversation_id,
                owner_id,
                room_name,
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({}),
            ),
        )


def _user_turn(client, headers, request_id="turn-1"):
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": "Hi"})
    assert response.status_code == 200
    return response.json()["turn_id"]


def test_turn_metrics_route_records_stages_and_ignores_a_duplicate(worker):
    client, database, _, _, headers = worker
    turn_id = _user_turn(client, headers)
    body = {
        "turn_id": turn_id,
        "stt_ms": 120,
        "eot_ms": 80,
        "llm_ttft_ms": 300,
        "tts_ttfb_ms": 40,
        "ttfa_ms": 700,
        "stages": {"turn_start_ms": 12, "llm_reserve_ms": 3, "command_ms": None},
    }
    response = client.post(METRICS_PATH, headers=headers, json=body)
    assert response.status_code == 200
    assert response.json() == {"recorded": True}

    duplicate = dict(body, stt_ms=999, stages={"turn_start_ms": 500})
    response = client.post(METRICS_PATH, headers=headers, json=duplicate)
    assert response.status_code == 200
    assert response.json() == {"recorded": False}

    with closing(sqlite3.connect(database)) as db:
        row = db.execute(
            "SELECT stt_ms,eot_ms,llm_ttft_ms,tts_ttfb_ms,ttfa_ms,usage FROM turn_metrics WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
        columns = {name for _, name, *_ in db.execute("PRAGMA table_info(turn_metrics)")}
    assert row == (120, 80, 300, 40, 700, json.dumps({"stages": {"turn_start_ms": 12, "llm_reserve_ms": 3}}))
    assert "text" not in columns


def test_turn_metrics_rejects_another_conversation_and_assistant_turns(worker):
    client, database, owner, _, headers = worker
    turn_id = _user_turn(client, headers)
    other = client.post("/v1/conversations").json()["id"]
    _insert_session(database, "other-session", other, owner, "other-room")
    other_headers = {
        "x-agent-secret": "test-service-secret",
        "x-session-id": "other-session",
        "x-room-name": "other-room",
    }
    assert client.post(METRICS_PATH, headers=other_headers, json={"turn_id": turn_id}).status_code == 404

    assistant = client.post(
        "/internal/agent/turn/finalize",
        headers=headers,
        json={"turn_id": turn_id, "text": "Hello", "status": "completed"},
    ).json()["id"]
    assert client.post(METRICS_PATH, headers=headers, json={"turn_id": assistant}).status_code == 404


@pytest.mark.parametrize(
    "override",
    [
        {"stt_ms": 120001},
        {"stt_ms": True},
        {"stt_ms": -1},
        {"stt_ms": 1.5},
        {"ttfa_ms": 120001},
        {"stages": {"turn_start_ms": 120001}},
        {"stages": {"unknown_ms": 1}},
        {"text": "must never be stored"},
    ],
)
def test_turn_metrics_rejects_out_of_range_bool_and_extra_fields(worker, override):
    client, _, _, _, headers = worker
    turn_id = _user_turn(client, headers)
    response = client.post(METRICS_PATH, headers=headers, json={"turn_id": turn_id, **override})
    assert response.status_code == 422


class Handle:
    interrupted = False

    def __init__(self, identifier, chat_items):
        self.id = identifier
        self.chat_items = chat_items

    async def wait_for_playout(self):
        return None

    def exception(self):
        return None


def _assistant_message(metrics):
    return llm.ChatMessage(id="assistant", role="assistant", content=["Hello"], metrics=metrics)


def _agent(client):
    return SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")


def test_payload_uses_stub_sdk_metrics_and_stages():
    payload = build_payload(
        "turn-1",
        user_metrics={"transcription_delay": 0.1234, "end_of_turn_delay": 0.5},
        assistant_metrics={"llm_node_ttft": 0.2, "tts_node_ttfb": 0.3, "e2e_latency": 1.5},
        stages={"turn_start_ms": 12, "command_ms": None, "llm_reserve_ms": 7, "tts_reserve_ms": 4},
    )
    assert payload == {
        "turn_id": "turn-1",
        "stt_ms": 123,
        "eot_ms": 500,
        "llm_ttft_ms": 200,
        "tts_ttfb_ms": 300,
        "ttfa_ms": 1500,
        "stages": {"turn_start_ms": 12, "llm_reserve_ms": 7, "tts_reserve_ms": 4},
    }
    assert metric_ms({"value": True}, "value") is None
    assert metric_ms({"value": float("nan")}, "value") is None
    assert metric_ms({"value": -0.1}, "value") is None
    assert metric_ms({"value": 120.001}, "value") is None
    assert assistant_metrics([_assistant_message({})]) == {}


def test_worker_sends_once_after_finalize_and_cleans_turn_state():
    paths, bodies = [], []

    def backend(request):
        paths.append(request.url.path)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    user = llm.ChatMessage(
        id="user", role="user", content=["Hi"], metrics={"transcription_delay": 0.25, "end_of_turn_delay": 0.1}
    )
    handle = Handle(
        "speech-1",
        [
            _assistant_message(
                {"llm_node_ttft": 0.2, "tts_node_ttfb": 0.3, "e2e_latency": 0.9},
            )
        ],
    )

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            agent.prepared[user.id] = {"turn_id": "turn-1"}
            agent.turn_stages["turn-1"] = {"turn_start_ms": 12, "command_ms": 7}
            agent._schedule_persistence(user, handle)
            agent._schedule_persistence(user, handle)
            await asyncio.gather(*agent.pending)
            assert agent.turn_stages == {}
            assert agent.pending == set()

    asyncio.run(run())
    assert paths[-2:] == ["/internal/agent/turn/finalize", METRICS_PATH]
    assert paths.count(METRICS_PATH) == 1
    assert bodies[-1] == {
        "turn_id": "turn-1",
        "stt_ms": 250,
        "eot_ms": 100,
        "llm_ttft_ms": 200,
        "tts_ttfb_ms": 300,
        "ttfa_ms": 900,
        "stages": {"turn_start_ms": 12, "command_ms": 7},
    }


def test_missing_metrics_are_null_and_a_metrics_failure_does_not_affect_finalize():
    paths = []

    def backend(request):
        paths.append(request.url.path)
        if request.url.path == METRICS_PATH:
            raise httpx.ConnectError("metrics unavailable")
        return httpx.Response(200, json={})

    user = llm.ChatMessage(id="user", role="user", content=["Hi"])
    handle = Handle("speech-1", [_assistant_message({})])

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            agent.prepared[user.id] = {"turn_id": "turn-1"}
            agent._schedule_persistence(user, handle)
            await asyncio.gather(*agent.pending)
            assert agent.turn_stages == {}

    asyncio.run(run())
    assert paths == ["/internal/agent/turn/finalize", METRICS_PATH]


def test_second_turn_never_inherits_first_turn_metrics():
    bodies = []

    def backend(request):
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    first = llm.ChatMessage(id="user-1", role="user", content=["Hi"], metrics={"transcription_delay": 1})
    second = llm.ChatMessage(id="user-2", role="user", content=["Again"])
    first_handle = Handle("speech-1", [_assistant_message({"llm_node_ttft": 1})])
    second_handle = Handle("speech-2", [])

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            agent.prepared[first.id] = {"turn_id": "turn-1"}
            agent.prepared[second.id] = {"turn_id": "turn-2"}
            agent.turn_stages["turn-1"] = {"tts_reserve_ms": 9}
            agent._schedule_persistence(first, first_handle)
            await asyncio.gather(*agent.pending)
            agent._schedule_persistence(second, second_handle)
            await asyncio.gather(*agent.pending)

    asyncio.run(run())
    metrics = [body for path, body in bodies if path == METRICS_PATH]
    assert metrics[0]["stages"] == {"tts_reserve_ms": 9}
    assert metrics[1] == {
        "turn_id": "turn-2",
        "stt_ms": None,
        "eot_ms": None,
        "llm_ttft_ms": None,
        "tts_ttfb_ms": None,
        "ttfa_ms": None,
        "stages": {},
    }


def test_tts_timing_is_bound_to_the_turn_captured_when_tts_starts(monkeypatch):
    async def synthesize(agent, text, model_settings):
        async for _ in text:
            yield "audio"

    monkeypatch.setattr(Agent.default, "tts_node", synthesize)

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            agent = _agent(client)
            agent.current_turn = "turn-1"
            agent.turn_stages["turn-1"] = {}

            async def text():
                agent.current_turn = "turn-2"
                yield "x" * 64

            assert [frame async for frame in agent.tts_node(text(), None)] == ["audio"]
            assert agent.turn_stages["turn-1"]["tts_reserve_ms"] is not None
            assert "turn-2" not in agent.turn_stages

    asyncio.run(run())


def test_first_measured_assistant_message_supplies_time_to_first_audio():
    items = [
        SimpleNamespace(role="assistant", metrics={}),
        SimpleNamespace(role="assistant", metrics={"e2e_latency": 1.0}),
        SimpleNamespace(role="assistant", metrics={"e2e_latency": 9.0}),
    ]
    payload = build_payload("t", user_metrics=None, assistant_metrics=assistant_metrics(items), stages={})
    assert payload["ttfa_ms"] == 1000


def test_rejected_metrics_are_logged_without_text_and_late_timings_do_not_reopen_a_turn(caplog):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(422, json={}))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            agent = _agent(client)
            agent.turn_stages["turn-1"] = {"command_ms": 5}
            user = SimpleNamespace(metrics={}, text_content="private words")
            await agent._send_turn_metrics("turn-1", user, SimpleNamespace(chat_items=[]))
            agent._record_stage("turn-1", "tts_reserve_ms", 0.0)
            assert agent.turn_stages == {}

    with caplog.at_level("WARNING"):
        asyncio.run(run())
    assert [r.getMessage() for r in caplog.records if r.name == "root"] == ["Turn metrics submission failed"]
    assert "private words" not in caplog.text


def test_model_reservation_is_timed_for_its_own_turn_only():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            agent = _agent(client)
            agent.current_turn = "turn-1"
            agent.turn_stages["turn-1"] = {}
            await agent.reserve("deepgram_tts", 5)
            assert agent.turn_stages["turn-1"] == {}
            await agent.reserve("deepseek")
            assert set(agent.turn_stages["turn-1"]) == {"llm_reserve_ms"}

    asyncio.run(run())
