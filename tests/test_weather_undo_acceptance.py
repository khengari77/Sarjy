import app.main as main_module
from fastapi.testclient import TestClient
from test_context_assembly import start_turn
from test_context_workflow import configured, create_app, seed


def test_weather_eligibility_returns_after_signed_exclusion_and_undo(tmp_path, monkeypatch):
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
        base = f"/v1/conversations/{chat}/context"

        def request_weather(turn, key):
            return client.post(
                "/internal/agent/tool/weather",
                headers=headers,
                json={"turn_id": turn["turn_id"], "request_id": key, "city": "Tripoli"},
            )

        assert request_weather(first, "before").json()["city"] == "Tripoli"
        preview = client.post(base + "/proposals", json={"query": "vacation"}).json()
        applied = client.post(
            base + "/confirm",
            json={"proposal": preview["proposal"], "expected_revision": 0, "idempotency_key": "exclude"},
        ).json()
        excluded = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "excluded", "text": "Weather?"}
        ).json()
        assert request_weather(excluded, "blocked").json() == {"error": "LOCATION_EXCLUDED"}
        assert calls == ["Tripoli"]
        response = client.post(
            base + "/undo",
            json={"operation_id": applied["operation_id"], "expected_revision": 1, "idempotency_key": "undo"},
        )
        assert response.status_code == 200, response.text
        restored = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "restored", "text": "Weather?"}
        ).json()
        assert request_weather(restored, "after").json()["city"] == "Tripoli"
        assert calls == ["Tripoli", "Tripoli"]
