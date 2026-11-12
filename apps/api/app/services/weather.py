import httpx


class WeatherError(Exception):
    pass

def select_location(results: list[dict], country: str | None = None) -> dict:
    matches = [r for r in results if not country or country.casefold() in {str(r.get('country', '')).casefold(), str(r.get('country_code', '')).casefold()}]
    if not matches:
        raise WeatherError('LOCATION_NOT_FOUND')
    if len(matches) != 1:
        raise WeatherError('AMBIGUOUS_LOCATION')
    return matches[0]

async def get_current_weather(city: str, country: str | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            geocoding = await client.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': city, 'count': 5})
            geocoding.raise_for_status()
            location = select_location(geocoding.json().get('results', []), country)
            response = await client.get('https://api.open-meteo.com/v1/forecast', params={'latitude': location['latitude'], 'longitude': location['longitude'], 'current': 'temperature_2m,wind_speed_10m,weather_code', 'timezone': 'auto'})
            response.raise_for_status()
            data = response.json()
            return {'city': location['name'], 'country': location.get('country'), 'current': data['current'], 'units': data.get('current_units', {}), 'timezone': data.get('timezone'), 'source': 'Open-Meteo', 'source_url': 'https://open-meteo.com/'}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise WeatherError('WEATHER_UNAVAILABLE') from exc
