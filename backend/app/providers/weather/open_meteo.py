"""Open-Meteo Archive weather provider (design.md §5, Req 4.5, 4.7, 4.11, 4.12, 5.8).

One request per call:
``GET https://archive-api.open-meteo.com/v1/archive`` with ``latitude``,
``longitude``, ``start_date=end_date`` (the date of ``at`` in APP_TIMEZONE),
``hourly=temperature_2m,precipitation,relative_humidity_2m`` and
``timezone=APP_TIMEZONE``. No API key is sent.

Any failure (timeout, network error, non-2xx status, invalid JSON, or no
temperature at the selected hour) is logged as a warning and answered with the
fixture provider's result for the same input, so ``get_weather`` never raises
for upstream problems (Req 4.7, 5.8).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx

from app.domain import WeatherReading
from app.providers.weather.base import WeatherProvider
from app.providers.weather.normalize import map_archive_response

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARIABLES = "temperature_2m,precipitation,relative_humidity_2m"
REQUEST_TIMEOUT_S = 10.0


def build_archive_params(
    at: datetime, latitude: float, longitude: float, tz: ZoneInfo
) -> dict[str, str | float]:
    """Query parameters for one Archive request covering the local date of ``at``."""
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    day = at.astimezone(tz).date().isoformat()
    return {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": day,
        "end_date": day,
        "hourly": HOURLY_VARIABLES,
        "timezone": tz.key,
    }


class OpenMeteoWeatherProvider:
    """Weather from the Open-Meteo Archive API with fixture fallback."""

    name: Literal["open_meteo"] = "open_meteo"

    def __init__(
        self,
        tz: ZoneInfo,
        fallback: WeatherProvider,
        client: httpx.Client | None = None,
    ) -> None:
        self._tz = tz
        self._fallback = fallback
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_S)

    def close(self) -> None:
        self._client.close()

    def _fallback_reading(self, at: datetime, latitude: float, longitude: float, reason: str) -> WeatherReading:
        logger.warning("Open-Meteo 날씨 조회 실패(%s), fixture 날씨로 대체합니다.", reason)
        return self._fallback.get_weather(at, latitude, longitude)

    def get_weather(self, at: datetime, latitude: float, longitude: float) -> WeatherReading:
        params = build_archive_params(at, latitude, longitude, self._tz)
        try:
            response = self._client.get(ARCHIVE_URL, params=params)
        except httpx.TimeoutException:
            return self._fallback_reading(at, latitude, longitude, "timeout")
        except httpx.HTTPError as exc:
            return self._fallback_reading(at, latitude, longitude, f"network error: {type(exc).__name__}")

        if not response.is_success:
            return self._fallback_reading(at, latitude, longitude, f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return self._fallback_reading(at, latitude, longitude, "invalid JSON")

        try:
            reading = map_archive_response(payload, at, self._tz)
        except (ValueError, TypeError) as exc:  # pydantic ValidationError is a ValueError
            return self._fallback_reading(at, latitude, longitude, f"unmappable payload: {type(exc).__name__}")
        if reading is None:
            return self._fallback_reading(at, latitude, longitude, "no temperature at selected hour")
        return reading
