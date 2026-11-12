import app.main as main_module
from app.services.weather import WeatherError
from fastapi.testclient import TestClient
from test_context_assembly import start_turn
from test_context_workflow import configured, create_app, seed


def test_weather_operation_replays_without_second_external_call(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)
    calls = []

    async def weather(city, country=None):
        calls.append(city)
        return {"city": city, "temperature": 21}

    monkeypatch.setattr(main_module, "get_current_weather", weather)
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat, [("city", "Trip to Tripoli")])
        headers, first = start_turn(client, chat, owner, file, memories=())

        payload = {"turn_id": first["turn_id"], "request_id": "turn:call-1", "city": "Tripoli"}
        first_result = client.post("/internal/agent/tool/weather", headers=headers, json=payload)
        replay_result = client.post("/internal/agent/tool/weather", headers=headers, json=payload)
        assert first_result.status_code == 200
        assert replay_result.status_code == 200
        assert replay_result.json() == first_result.json() == {"city": "Tripoli", "temperature": 21}
        mismatch = client.post("/internal/agent/tool/weather", headers=headers, json={**payload, "country": "LY"})
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"]["code"] == "TOOL_OPERATION_CONFLICT"
        assert calls == ["Tripoli"]

        async def unavailable(city, country=None):
            calls.append("unknown")
            raise WeatherError("WEATHER_UNAVAILABLE")

        monkeypatch.setattr(main_module, "get_current_weather", unavailable)
        unknown_payload = {"turn_id": first["turn_id"], "request_id": "turn:call-2", "city": "Tripoli"}
        first_unknown = client.post("/internal/agent/tool/weather", headers=headers, json=unknown_payload)
        replay_unknown = client.post("/internal/agent/tool/weather", headers=headers, json=unknown_payload)
        assert first_unknown.status_code == 200
        assert first_unknown.json() == {"error": "WEATHER_UNAVAILABLE"}
        assert replay_unknown.status_code == 503
        assert replay_unknown.json()["detail"]["code"] == "WEATHER_UNAVAILABLE"
        assert calls == ["Tripoli", "unknown"]


def test_weather_operation_key_cannot_be_reused_for_another_turn(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)
    calls = []

    async def weather(city, country=None):
        calls.append(city)
        return {"city": city}

    monkeypatch.setattr(main_module, "get_current_weather", weather)
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat, [("city", "Trip to Tripoli")])
        headers, first = start_turn(client, chat, owner, file, memories=())
        second = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "request-2", "text": "Weather?"}
        ).json()
        payload = {"turn_id": first["turn_id"], "request_id": "shared-key", "city": "Tripoli"}
        assert client.post("/internal/agent/tool/weather", headers=headers, json=payload).status_code == 200
        conflict = client.post(
            "/internal/agent/tool/weather", headers=headers, json={**payload, "turn_id": second["turn_id"]}
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "TOOL_OPERATION_CONFLICT"
        assert calls == ["Tripoli"]
