import math

import httpx


class WeatherError(Exception):
    pass


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


async def get_current_weather(city: str, country: str | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            geocoding = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 5}
            )
            geocoding.raise_for_status()
            places = geocoding.json()
            if not isinstance(places, dict):
                raise WeatherError("WEATHER_UNAVAILABLE")
            location = select_location(places.get("results", []), country)
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
            for key in ("temperature_2m", "wind_speed_10m", "weather_code"):
                value = current.get(key)
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
