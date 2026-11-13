"""Malformed external responses are typed failures, never leaked payloads."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from app.services import weather
from app.services.voices import ElevenLabsVoices, VoiceCatalogError


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"voices": None},
        {"voices": [None]},
        {"voices": [{"voice_id": "v", "name": "n", "labels": "PRIVATE"}]},
        {"voices": [{"voice_id": "v", "name": "n", "verified_languages": [None]}]},
    ],
)
def test_malformed_catalog_is_typed(payload):
    async def run():
        service = ElevenLabsVoices(
            "PRIVATE", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        )
        with pytest.raises(VoiceCatalogError) as caught:
            await service.list()
        assert caught.value.code == "VOICE_CATALOG_UNAVAILABLE"
        assert "PRIVATE" not in str(caught.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [None, [], {"results": None}, {"results": [None]}, {"results": [{"name": "x", "latitude": None, "longitude": 0}]}],
)
def test_malformed_weather_is_typed(monkeypatch, payload):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        weather.httpx,
        "AsyncClient",
        lambda **kw: client(**kw, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
    )
    with pytest.raises(weather.WeatherError, match="WEATHER_UNAVAILABLE"):
        asyncio.run(weather.get_current_weather("Tripoli"))


@pytest.mark.parametrize(
    "forecast",
    [
        None,
        [],
        {"current": None},
        {"current": {"temperature_2m": True}},
        {"current": {"temperature_2m": 20, "wind_speed_10m": 2, "weather_code": 0}, "timezone": []},
    ],
)
def test_malformed_forecast_is_typed(monkeypatch, forecast):
    client = httpx.AsyncClient

    def transport(request):
        body = (
            {"results": [{"name": "Tripoli", "latitude": 32, "longitude": 13}]}
            if "geocoding" in request.url.host
            else forecast
        )
        return httpx.Response(200, json=body)

    monkeypatch.setattr(
        weather.httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(transport))
    )
    with pytest.raises(weather.WeatherError, match="WEATHER_UNAVAILABLE"):
        asyncio.run(weather.get_current_weather("Tripoli"))


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("http", "PROVIDER_HTTP_FAILED"),
        ("invalid", "PROVIDER_RESPONSE_INVALID"),
        ("connection", "PROVIDER_CONNECTION_FAILED"),
        ("denied", "DAILY_BUDGET_EXHAUSTED"),
        ("valid", None),
        ("valid-zero", None),
        ("empty", "PROVIDER_RESPONSE_INVALID"),
        ("usage-null", "PROVIDER_RESPONSE_INVALID"),
        ("usage-empty", "PROVIDER_RESPONSE_INVALID"),
        ("missing-counter", "PROVIDER_RESPONSE_INVALID"),
        ("null-counter", "PROVIDER_RESPONSE_INVALID"),
        ("bool-counter", "PROVIDER_RESPONSE_INVALID"),
        ("string-counter", "PROVIDER_RESPONSE_INVALID"),
        ("negative-counter", "PROVIDER_RESPONSE_INVALID"),
        ("invalid-json", "PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_diagnostic_classifies_without_paid_calls(monkeypatch, capsys, mode, reason):
    spec = importlib.util.spec_from_file_location(
        "diagnostic101", Path(__file__).parents[1] / "scripts/check_openai.py"
    )
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    events = []

    class Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Engine:
        async def dispose(self):
            events.append("disposed")

    async def reserve(db, provider):
        events.append("reserve")
        if mode == "denied":
            raise ValueError("DAILY_BUDGET_EXHAUSTED")
        return {"reserved_units": 1, "remaining_units": 1, "day": "test"}

    def transport(request):
        events.append("send")
        return _diagnostic_response(mode, request)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        diagnostic, "Settings", lambda: SimpleNamespace(openai_api_key="PRIVATE", database_url="unused")
    )
    monkeypatch.setattr(diagnostic, "create_async_engine", lambda _: Engine())
    monkeypatch.setattr(diagnostic, "async_sessionmaker", lambda _: lambda: Context())
    monkeypatch.setattr(diagnostic, "reserve", reserve)
    monkeypatch.setattr(
        diagnostic.httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(transport))
    )
    asyncio.run(diagnostic.main())
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    if reason:
        assert reason in output
        assert "'success': True" not in output
    else:
        assert "input_tokens" in output and "stopped" not in output
        assert "'success': True" in output
    assert events[0] == "reserve" and events[-1] == "disposed"
    if mode == "denied":
        assert "send" not in events
    else:
        assert events.count("send") == 1


def test_valid_weather_and_empty_search_keep_existing_semantics(monkeypatch):
    client = httpx.AsyncClient

    def transport(request):
        if "geocoding" in request.url.host:
            if request.url.params["name"] == "missing":
                return httpx.Response(200, json={"results": []})
            return httpx.Response(
                200, json={"results": [{"name": "Tripoli", "latitude": 32, "longitude": 13, "country": "Libya"}]}
            )
        return httpx.Response(
            200,
            json={
                "current": {"temperature_2m": 0, "wind_speed_10m": 0, "weather_code": 0},
                "timezone": "Africa/Tripoli",
            },
        )

    monkeypatch.setattr(
        weather.httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(transport))
    )
    result = asyncio.run(weather.get_current_weather("Tripoli"))
    assert result["current"]["temperature_2m"] == 0
    assert result["city"] == "Tripoli"
    with pytest.raises(weather.WeatherError, match="LOCATION_NOT_FOUND"):
        asyncio.run(weather.get_current_weather("missing"))


def _diagnostic_response(mode, request):
    if mode == "connection":
        raise httpx.ConnectError("PRIVATE", request=request)
    if mode == "http":
        return httpx.Response(502, text="PRIVATE HTML")
    if mode == "invalid":
        return httpx.Response(200, json=None)
    if mode == "invalid-json":
        return httpx.Response(200, text="PRIVATE HTML")
    valid_usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    payloads = {
        "empty": {},
        "usage-null": {"usage": None},
        "usage-empty": {"usage": {}},
        "missing-counter": {"usage": {"input_tokens": 1, "total_tokens": 2}},
        "null-counter": {"usage": {**valid_usage, "output_tokens": None}},
        "bool-counter": {"usage": {**valid_usage, "output_tokens": True}},
        "string-counter": {"usage": {**valid_usage, "output_tokens": "PRIVATE"}},
        "negative-counter": {"usage": {**valid_usage, "output_tokens": -1}},
        "valid-zero": {"usage": dict.fromkeys(valid_usage, 0)},
    }
    return httpx.Response(200, json=payloads.get(mode, {"usage": valid_usage}))
