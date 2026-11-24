"""The worker records what the API reports and what its own reply path measures."""

import asyncio
from types import SimpleNamespace

import httpx
from main import SarjyAgent

TIMING = {"x-server-ms": "812", "x-db-ms": "455", "x-db-statements": "9"}


def _agent(client):
    return SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")


def test_turn_start_and_command_record_the_servers_own_timing():
    def backend(request):
        if request.url.path.endswith("/command"):
            return httpx.Response(200, headers={"x-server-ms": "640", "x-db-ms": "90"}, json={"command": None})
        return httpx.Response(200, headers=TIMING, json={"turn_id": "turn-1", "context": {}})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            message = SimpleNamespace(id="m1", text_content="Hi")
            await agent.compile(message)
            await agent.command_for(message)
            return agent.turn_stages["turn-1"]

    stages = asyncio.run(run())
    assert stages["turn_start_server_ms"] == 812 and stages["turn_start_db_ms"] == 455
    assert stages["command_server_ms"] == 640 and stages["command_db_ms"] == 90


def test_turn_start_and_command_api_phases_are_traced_on_a_real_connection():
    import socket
    import threading

    from main import backend_client

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(2)
    port = server.getsockname()[1]

    def serve():
        for _ in range(2):
            connection, _ = server.accept()
            request = connection.recv(65536)
            body = (
                b'{"turn_id": "turn-1", "context": {}}'
                if request.split(b" ")[1].endswith(b"/turn/start")
                else b'{"command": null}'
            )
            connection.sendall(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                + str(len(body)).encode()
                + b"\r\nconnection: close\r\n\r\n"
                + body
            )
            connection.close()
        server.close()

    threading.Thread(target=serve, daemon=True).start()

    async def run():
        async with backend_client(f"http://127.0.0.1:{port}", {}) as client:
            agent = _agent(client)
            message = SimpleNamespace(id="m1", text_content="Hi")
            await agent.compile(message)
            await agent.command_for(message)
            return agent.turn_stages["turn-1"]

    stages = asyncio.run(run())
    assert stages["api_connect_ms"] >= 0
    assert stages["api_headers_ms"] >= 0
    assert stages["api_body_ms"] >= 0
    assert stages["command_api_headers_ms"] >= 0
    assert "command_api_body_ms" not in stages


def test_phase_trace_records_body_time_and_a_reused_connection_as_zero():
    from stream_completion import CompletionState, _PhaseTrace

    state = CompletionState()
    trace = _PhaseTrace(state)
    asyncio.run(trace("http11.send_request_headers.started", {}))  # no connect event: a kept-alive socket
    assert state.provider_connect_ms == 0
    asyncio.run(trace("http11.receive_response_headers.complete", {}))
    assert state.provider_headers_ms is not None
    asyncio.run(trace("http11.receive_response_body.started", {}))
    asyncio.run(trace("http11.receive_response_body.complete", {}))
    assert state.provider_body_ms is not None


def test_missing_or_malformed_timing_headers_record_nothing():
    def backend(request):
        return httpx.Response(200, headers={"x-server-ms": "slow", "x-db-ms": ""}, json={"turn_id": "t", "context": {}})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            await agent.compile(SimpleNamespace(id="m1", text_content="Hi"))
            return agent.turn_stages["t"]

    stages = asyncio.run(run())
    assert "turn_start_server_ms" not in stages and "turn_start_db_ms" not in stages


def test_request_bytes_and_cache_tokens_are_recorded_for_the_turn():
    from main import _record_value

    agent = SimpleNamespace(turn_stages={"t": {}})
    _record_value(agent, "t", "request_bytes", 10_240, first_only=True)
    _record_value(agent, "t", "request_bytes", 99, first_only=True)  # the first request wins
    _record_value(agent, "t", "prompt_cache_hit_tokens", None)  # an absent value records nothing
    _record_value(agent, None, "request_bytes", 1)  # no turn, no record
    assert agent.turn_stages == {"t": {"request_bytes": 10_240}}


def test_provider_phases_are_traced_on_a_real_connection():
    import socket
    import threading

    from stream_completion import CompletionState, ObservedTransport, completion_state

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    port = server.getsockname()[1]

    def serve():
        connection, _ = server.accept()
        connection.recv(65536)
        connection.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok")
        connection.close()
        server.close()

    threading.Thread(target=serve, daemon=True).start()

    async def call():
        state = CompletionState()
        completion_state.set(state)
        async with httpx.AsyncClient(transport=ObservedTransport()) as client:
            await client.get(f"http://127.0.0.1:{port}/")
        return state

    port_state = asyncio.run(call())
    state = port_state
    assert state.provider_connect_ms is not None and state.provider_connect_ms >= 0
    assert state.provider_headers_ms is not None and state.provider_headers_ms >= 0


def test_loop_lag_monitor_sees_a_blocked_loop_and_resets():
    import time

    from loop_lag import LoopLagMonitor

    async def run():
        monitor = LoopLagMonitor()
        monitor.start()
        await asyncio.sleep(0.05)
        time.sleep(0.12)  # block the loop
        await asyncio.sleep(0.05)
        first = monitor.take()
        monitor.stop()
        return first, monitor.take()

    first, second = asyncio.run(run())
    assert first >= 80
    assert second < first


def test_a_second_inference_records_its_own_cache_stage_names():
    import time

    from main import _record_value

    agent = _agent(None)
    agent.turn_stages["t"] = {}
    agent._record_stage("t", "model_first_chunk_ms", time.perf_counter(), first_only=True)
    first = agent.turn_stages["t"]["model_first_chunk_ms"]
    # A later chunk of the same call must never publish a second-inference time.
    agent._record_stage("t", "model_first_chunk_ms", time.perf_counter(), first_only=True)
    assert "model_first_chunk_2_ms" not in agent.turn_stages["t"]
    _record_value(
        agent,
        "t",
        "prompt_cache_hit_tokens",
        10,
        first_only=True,
        second_name="prompt_cache_hit_tokens_2",
    )
    _record_value(
        agent,
        "t",
        "prompt_cache_hit_tokens",
        20,
        first_only=True,
        second_name="prompt_cache_hit_tokens_2",
    )
    _record_value(
        agent,
        "t",
        "prompt_cache_miss_tokens",
        30,
        first_only=True,
        second_name="prompt_cache_miss_tokens_2",
    )
    assert first is not None
    assert agent.turn_stages["t"] == {
        "model_first_chunk_ms": first,
        "prompt_cache_hit_tokens": 10,
        "prompt_cache_hit_tokens_2": 20,
        "prompt_cache_miss_tokens": 30,
    }


def test_stage_fields_match_the_api_schema_and_a_failing_trace_hook_is_harmless():
    from app.routes.worker import TurnStageMetrics
    from stream_completion import CompletionState, _PhaseTrace
    from turn_metrics import STAGE_FIELDS, TurnStages

    assert set(STAGE_FIELDS) == set(TurnStageMetrics.model_fields) == set(TurnStages.__annotations__)
    assert {
        "prompt_cache_hit_tokens_2",
        "prompt_cache_miss_tokens_2",
        "t_turn_start_begin_ms",
        "t_turn_start_end_ms",
        "t_command_begin_ms",
        "t_command_end_ms",
        "t_model_request_ms",
        "t_model_first_chunk_ms",
        "t_tool_start_ms",
        "t_tool_end_ms",
        "t_tts_first_ms",
    } <= set(STAGE_FIELDS)

    trace = _PhaseTrace(CompletionState())
    trace._observe = lambda event: 1 / 0  # type: ignore[method-assign]
    asyncio.run(trace("connection.connect_tcp.started", {}))  # must not raise


def test_timeline_stages_are_offsets_from_the_turn_hook_anchor_and_recorded_once():
    agent = _agent(None)
    agent.turn_anchor["t"] = 100.0
    agent.turn_stages["t"] = {}

    agent._record_offset("t", "t_command_begin_ms", 100.25, first_only=True)
    agent._record_offset("t", "t_command_begin_ms", 100.75, first_only=True)
    agent._record_offset("t", "t_command_end_ms", 101.0, first_only=True)
    agent._record_offset("no-anchor", "t_tool_start_ms", 100.5, first_only=True)

    assert agent.turn_stages["t"] == {"t_command_begin_ms": 250, "t_command_end_ms": 1000}


def test_only_tools_that_return_to_the_model_prepay_a_continuation():
    from main import MODEL_CONTINUATION_TOOLS

    assert {"get_current_weather", "recall"} == MODEL_CONTINUATION_TOOLS
