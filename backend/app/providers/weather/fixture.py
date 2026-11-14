"""Deterministic fixture weather provider (design.md §5, Req 4.6, 4.10).

Lookup order for ``get_weather(at, latitude, longitude)``:

1. An entry in ``data/demo/fixtures/weather.json`` whose key
   ``(captured_at UTC instant, round(lat, 4), round(lon, 4))`` matches.
2. ``today_default`` from the same file, when ``at`` falls on today's date in
   ``APP_TIMEZONE``. This is today's weather in fixture mode with no scenario
   selected (design.md "Fixture 파일"), and it is also what Open-Meteo falls
   back to when the Archive API has no value for today (Req 4.7, 5.8).
3. ``seasonal_temp(month, hour)``: a fixed formula with no randomness.

Every result has ``provider='fixture'``, ``is_fixture=True`` and
``observed_at`` = ``at`` in ``APP_TIMEZONE`` truncated to the hour.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ValidationError

from app.domain import WeatherReading

logger = logging.getLogger(__name__)

# backend/app/providers/weather/fixture.py -> repository root is parents[4].
DEFAULT_WEATHER_FIXTURE_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "demo" / "fixtures" / "weather.json"
)

# Seoul monthly mean air temperature (°C), January..December, rounded climate normals.
SEOUL_MONTHLY_MEAN_C: tuple[float, ...] = (
    -1.9, 0.7, 6.1, 12.6, 18.2, 22.7, 25.3, 26.1, 21.6, 15.0, 7.5, 0.2,
)
DIURNAL_AMPLITUDE_C = 4.0  # half of the typical daily range
DIURNAL_PEAK_HOUR = 15  # warmest hour of the day (local time)

WeatherKey = tuple[datetime, float, float]


def seasonal_temp(month: int, hour: int) -> float:
    """Deterministic temperature for a local month (1-12) and hour (0-23).

    Monthly mean plus a cosine daily cycle peaking at 15:00 and lowest at 03:00,
    rounded to one decimal place.
    """
    if not 1 <= month <= 12:
        raise ValueError("month must be in 1..12")
    if not 0 <= hour <= 23:
        raise ValueError("hour must be in 0..23")
    offset = DIURNAL_AMPLITUDE_C * math.cos(2 * math.pi * (hour - DIURNAL_PEAK_HOUR) / 24)
    return round(SEOUL_MONTHLY_MEAN_C[month - 1] + offset, 1)


def fixture_key(at: datetime, latitude: float, longitude: float) -> WeatherKey:
    """Lookup key: UTC instant plus coordinates rounded to 4 decimal places."""
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    return (at.astimezone(UTC), round(latitude, 4), round(longitude, 4))


class _WeatherValues(BaseModel):
    actual_temperature_c: float
    apparent_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_speed_kmh: float | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None


class _FixtureEntry(_WeatherValues):
    captured_at: AwareDatetime
    latitude: float
    longitude: float


def _load_fixture_file(path: Path) -> tuple[dict[WeatherKey, _WeatherValues], _WeatherValues | None]:
    """Read weather.json. A missing or broken file yields no entries (seasonal only)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Weather fixture file could not be read (%s): %s", path.name, type(exc).__name__)
        return {}, None
    if not isinstance(payload, dict):
        logger.warning("Weather fixture file %s is not a JSON object", path.name)
        return {}, None

    entries: dict[WeatherKey, _WeatherValues] = {}
    raw_entries = payload.get("entries", [])
    for index, raw in enumerate(raw_entries if isinstance(raw_entries, list) else []):
        try:
            entry = _FixtureEntry.model_validate(raw)
        except ValidationError:
            logger.warning("Skipping invalid weather fixture entry #%d", index)
            continue
        key = fixture_key(entry.captured_at, entry.latitude, entry.longitude)
        if key in entries:
            logger.warning("Skipping duplicate weather fixture entry #%d", index)
            continue
        entries[key] = _WeatherValues.model_validate(entry.model_dump())

    today_default: _WeatherValues | None = None
    raw_today = payload.get("today_default")
    if raw_today is not None:
        try:
            today_default = _WeatherValues.model_validate(raw_today)
        except ValidationError:
            logger.warning("Ignoring invalid weather fixture today_default")
    return entries, today_default


class FixtureWeatherProvider:
    """Weather from fixture data; no network access."""

    name: Literal["fixture"] = "fixture"

    def __init__(
        self,
        path: Path = DEFAULT_WEATHER_FIXTURE_PATH,
        tz: ZoneInfo | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._tz = tz or ZoneInfo("Asia/Seoul")
        self._now = now or (lambda: datetime.now(UTC))
        self._entries, self._today_default = _load_fixture_file(Path(path))

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def _today(self) -> date:
        return self._now().astimezone(self._tz).date()

    def get_weather(self, at: datetime, latitude: float, longitude: float) -> WeatherReading:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        local = at.astimezone(self._tz)
        observed_at = local.replace(minute=0, second=0, microsecond=0)

        values = self._entries.get(fixture_key(at, latitude, longitude))
        if values is None and self._today_default is not None and local.date() == self._today():
            values = self._today_default
        if values is None:
            values = _WeatherValues(actual_temperature_c=seasonal_temp(local.month, local.hour))

        humidity = values.relative_humidity_pct
        if humidity is not None and not 0.0 <= humidity <= 100.0:
            humidity = None
        return WeatherReading(
            observed_at=observed_at,
            actual_temperature_c=values.actual_temperature_c,
            apparent_temperature_c=values.apparent_temperature_c,
            relative_humidity_pct=humidity,
            wind_speed_kmh=values.wind_speed_kmh,
            precipitation_mm=values.precipitation_mm,
            weather_code=values.weather_code,
            provider="fixture",
            is_fixture=True,
        )
