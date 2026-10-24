"""Weather lookup via Open-Meteo (no API key required).

Flow:
  1. Geocode the city name -> lat/lon.
  2. If the date is within the forecast horizon (~16 days), use the forecast API.
  3. Otherwise fall back to a historical-average "estimate" for the same calendar
     date (ERA5 archive, previous year) and clearly flag it as an estimate.
"""
from __future__ import annotations

import datetime as dt

import httpx

from .schemas import DayWeather

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

FORECAST_HORIZON_DAYS = 16

# Open-Meteo WMO weather codes -> coarse condition buckets.
_WMO = {
    0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy",
    45: "fog", 48: "fog",
    51: "rain", 53: "rain", 55: "rain", 56: "rain", 57: "rain",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain", 82: "rain",
    85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}


def _condition(code: int | None) -> str:
    return _WMO.get(int(code), "unknown") if code is not None else "unknown"


async def _geocode(client: httpx.AsyncClient, city: str) -> tuple[float, float] | None:
    r = await client.get(GEOCODE_URL, params={"name": city, "count": 1})
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"]


async def get_weather(city: str, date_str: str) -> DayWeather:
    """Return weather for a single city/date. Raises ValueError if the city is
    unknown."""
    target = dt.date.fromisoformat(date_str)
    today = dt.date.today()
    days_ahead = (target - today).days

    async with httpx.AsyncClient(timeout=20.0) as client:
        coords = await _geocode(client, city)
        if coords is None:
            raise ValueError(f"Could not geocode city: {city!r}")
        lat, lon = coords

        within_forecast = -1 <= days_ahead <= FORECAST_HORIZON_DAYS
        if within_forecast:
            return await _forecast(client, lat, lon, city, target)
        return await _estimate(client, lat, lon, city, target)


async def _forecast(
    client: httpx.AsyncClient, lat: float, lon: float, city: str, target: dt.date
) -> DayWeather:
    r = await client.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join(
                [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "apparent_temperature_max",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                    "weather_code",
                ]
            ),
            "timezone": "auto",
            "start_date": target.isoformat(),
            "end_date": target.isoformat(),
        },
    )
    r.raise_for_status()
    daily = r.json().get("daily", {})
    if not daily.get("time"):
        # Edge of horizon: no data returned -> fall back to estimate.
        return await _estimate(client, lat, lon, city, target)

    tmax = daily["temperature_2m_max"][0]
    tmin = daily["temperature_2m_min"][0]
    feels = (daily.get("apparent_temperature_max") or [None])[0]
    return DayWeather(
        date=target.isoformat(),
        city=city,
        temp_min_c=tmin,
        temp_max_c=tmax,
        feels_like_c=feels if feels is not None else (tmax + tmin) / 2,
        precipitation_probability=int((daily.get("precipitation_probability_max") or [0])[0] or 0),
        wind_kph=float((daily.get("wind_speed_10m_max") or [0])[0] or 0.0),
        condition=_condition((daily.get("weather_code") or [None])[0]),
        is_estimate=False,
    )


async def _estimate(
    client: httpx.AsyncClient, lat: float, lon: float, city: str, target: dt.date
) -> DayWeather:
    """Seasonal estimate using the same calendar date one year ago (ERA5 archive)."""
    try:
        past = target.replace(year=target.year - 1)
    except ValueError:  # Feb 29
        past = target.replace(year=target.year - 1, day=28)

    try:
        r = await client.get(
            ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": ",".join(
                    [
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "precipitation_sum",
                        "wind_speed_10m_max",
                        "weather_code",
                    ]
                ),
                "timezone": "auto",
                "start_date": past.isoformat(),
                "end_date": past.isoformat(),
            },
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if daily.get("time"):
            tmax = daily["temperature_2m_max"][0]
            tmin = daily["temperature_2m_min"][0]
            precip = (daily.get("precipitation_sum") or [0])[0] or 0
            return DayWeather(
                date=target.isoformat(),
                city=city,
                temp_min_c=tmin,
                temp_max_c=tmax,
                feels_like_c=(tmax + tmin) / 2,
                precipitation_probability=min(100, int(precip * 10)),
                wind_kph=float((daily.get("wind_speed_10m_max") or [0])[0] or 0.0),
                condition=_condition((daily.get("weather_code") or [None])[0]),
                is_estimate=True,
            )
    except httpx.HTTPError:
        pass

    # Last-resort placeholder so the UI still has something to show.
    return DayWeather(
        date=target.isoformat(),
        city=city,
        temp_min_c=12.0,
        temp_max_c=20.0,
        feels_like_c=16.0,
        precipitation_probability=20,
        wind_kph=10.0,
        condition="unknown",
        is_estimate=True,
    )
