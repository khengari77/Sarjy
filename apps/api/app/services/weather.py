import math
import time
from collections import OrderedDict
from typing import Any

import httpx

GEOCODE_CACHE_MAX_ENTRIES = 256
GEOCODE_CACHE_TTL_SECONDS = 24 * 60 * 60
WEATHER_TIMEOUT_SECONDS = 8
WEATHER_LIMITS = httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60)

_DEFAULT_CLIENT_CLASS = httpx.AsyncClient
_client: httpx.AsyncClient | None = None
_client_factory: Any = _DEFAULT_CLIENT_CLASS
_geocode_cache: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()


class WeatherError(Exception):
    pass


def _geocode_key(city: str, country: str | None) -> tuple[str, str]:
    return (" ".join(city.casefold().split()), " ".join((country or "").casefold().split()))


def _cached_location(key: tuple[str, str]) -> dict | None:
    cached = _geocode_cache.get(key)
    if cached is None:
        return None
    expires_at, location = cached
    if expires_at <= time.monotonic():
        _geocode_cache.pop(key, None)
        return None
    _geocode_cache.move_to_end(key)
    return location


def _cache_location(key: tuple[str, str], location: dict) -> None:
    _geocode_cache[key] = (time.monotonic() + GEOCODE_CACHE_TTL_SECONDS, location)
    _geocode_cache.move_to_end(key)
    while len(_geocode_cache) > GEOCODE_CACHE_MAX_ENTRIES:
        _geocode_cache.popitem(last=False)


def clear_weather_cache() -> None:
    _geocode_cache.clear()


def _weather_client() -> httpx.AsyncClient:
    """One connection pool per process, except while a test replaces the client class."""
    global _client, _client_factory
    factory = httpx.AsyncClient
    if _client is not None and factory is not _client_factory:
        # A patched constructor owns a different transport; never reuse the old client with it.
        _client = None
    if _client is None or _client.is_closed:
        _client = factory(timeout=WEATHER_TIMEOUT_SECONDS, limits=WEATHER_LIMITS)
        _client_factory = factory
    return _client


async def close_weather_client() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


def select_location(results: list[dict], country: str | None = None) -> dict:
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise WeatherError("WEATHER_UNAVAILABLE")
    matches = [
        r
        for r in results
        if not country
        or country.casefold() in {str(r.get("country", "")).casefold(), str(r.get("country_code", "")).casefold()}
    ]
    if not matches:
        raise WeatherError("LOCATION_NOT_FOUND")
    if len(matches) != 1:
        raise WeatherError("AMBIGUOUS_LOCATION")
    return matches[0]


def _validate_location(location: dict) -> None:
    if (
        (location.get("country") is not None and not isinstance(location["country"], str))
        or not isinstance(location.get("name"), str)
        or any(
            isinstance(location.get(key), bool)
            or not isinstance(location.get(key), (int, float))
            or not math.isfinite(location[key])
            for key in ("latitude", "longitude")
        )
    ):
        raise WeatherError("WEATHER_UNAVAILABLE")


async def get_current_weather(city: str, country: str | None = None) -> dict:
    key = _geocode_key(city, country)
    try:
        client = _weather_client()
        location = _cached_location(key)
        if location is None:
            geocoding = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 5}
            )
            geocoding.raise_for_status()
            places = geocoding.json()
            if not isinstance(places, dict):
                raise WeatherError("WEATHER_UNAVAILABLE")
            location = select_location(places.get("results", []), country)
            _validate_location(location)
            location = {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "name": location["name"],
                "country": location.get("country"),
            }
            _cache_location(key, location)
        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": "temperature_2m,wind_speed_10m,weather_code",
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("current"), dict):
            raise WeatherError("WEATHER_UNAVAILABLE")
        current = data["current"]
        for field in ("temperature_2m", "wind_speed_10m", "weather_code"):
            value = current.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise WeatherError("WEATHER_UNAVAILABLE")
        if not isinstance(data.get("current_units", {}), dict) or not isinstance(data.get("timezone"), str):
            raise WeatherError("WEATHER_UNAVAILABLE")
        return {
            "city": location["name"],
            "country": location.get("country"),
            "current": data["current"],
            "units": data.get("current_units", {}),
            "timezone": data.get("timezone"),
            "source": "Open-Meteo",
            "source_url": "https://open-meteo.com/",
        }
    except (httpx.HTTPError, KeyError, ValueError, OverflowError) as exc:
        raise WeatherError("WEATHER_UNAVAILABLE") from exc
