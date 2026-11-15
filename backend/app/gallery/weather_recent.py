"""Capture-time weather for watched photos (design.md §9, Req 7.1–7.6).

Open-Meteo's Archive (reanalysis) API fills the last few days late, so a photo
taken inside the Recent_Weather_Window (capture date in APP_TIMEZONE between
7 days ago and tomorrow) is looked up in the Forecast API's ``hourly`` block
with ``past_days``. Everything else, and every Forecast failure, goes to the
Phase 1 ``OpenMeteoWeatherProvider`` (Archive, which itself falls back to the
fixture). The provider that actually gave the value is the ``provider`` of the
reading.

Only the gallery import uses this provider; the Phase 1 demo scan keeps its
own weather provider (Req 7.5). ``WEATHER_PROVIDER=fixture`` uses the Phase 1
fixture provider instead (Req 7.4, ``build_recent_weather_provider``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx

from app.config import AppConfig
from app.domain import WeatherReading
from app.providers.weather.base import WeatherProvider
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.forecast import ForecastClient, ForecastError
from app.providers.weather.normalize import map_archive_response
from app.providers.weather.open_meteo import OpenMeteoWeatherProvider

logger = logging.getLogger(__name__)

RECENT_DAYS_BACK = 7
RECENT_DAYS_AHEAD = 1
FORECAST_DAYS = 2


def choose_weather_endpoint(
    captured_at: datetime, today: date, tz: ZoneInfo
) -> tuple[Literal["forecast"], int] | tuple[Literal["archive"], None]:
    """``("forecast", past_days)`` inside the Recent_Weather_Window, else ``("archive", None)``.

    ``d = today - capture date (APP_TIMEZONE)``; ``-1 <= d <= 7`` is recent and
    ``past_days = max(d, 0)`` (Req 7.1, 7.6).
    """
    days = (today - captured_at.astimezone(tz).date()).days
    if -RECENT_DAYS_AHEAD <= days <= RECENT_DAYS_BACK:
        return "forecast", max(days, 0)
    return "archive", None


class RecentWeatherProvider:
    """WeatherProvider: Forecast ``past_days`` for recent photos, Phase 1 Archive otherwise."""

    name: Literal["open_meteo"] = "open_meteo"

    def __init__(
        self,
        forecast: ForecastClient,
        archive: OpenMeteoWeatherProvider,
        tz: ZoneInfo,
        today: Callable[[], date],
    ) -> None:
        self._forecast = forecast
        self._archive = archive
        self._tz = tz
        self._today = today

    def get_weather(self, at: datetime, latitude: float, longitude: float) -> WeatherReading:
        endpoint, past_days = choose_weather_endpoint(at, self._today(), self._tz)
        if endpoint == "forecast":
            reading = self._from_forecast(at, latitude, longitude, past_days)
            if reading is not None:
                return reading
        return self._archive.get_weather(at, latitude, longitude)

    def _from_forecast(
        self, at: datetime, latitude: float, longitude: float, past_days: int
    ) -> WeatherReading | None:
        try:
            payload = self._forecast.get_hourly(latitude, longitude, self._tz, past_days, FORECAST_DAYS)
            reading = map_archive_response(payload, at, self._tz)
        except ForecastError as exc:
            reason = str(exc)
            reading = None
        except (ValueError, TypeError) as exc:  # pydantic ValidationError is a ValueError
            reason = f"unmappable payload: {type(exc).__name__}"
            reading = None
        else:
            reason = "no temperature at selected hour"
        if reading is None:
            # Coordinates are never logged (Req 9.4); only the reason.
            logger.warning("Forecast 최근 날씨 조회 실패(%s), Archive로 대체합니다.", reason)
        return reading


def build_recent_weather_provider(
    config: AppConfig,
    fixture: FixtureWeatherProvider,
    *,
    client: httpx.Client | None = None,
    today: Callable[[], date] | None = None,
) -> WeatherProvider:
    """Weather provider for the gallery import (design.md §6, §9).

    ``WEATHER_PROVIDER=fixture`` → the Phase 1 fixture provider itself (Req 7.4).
    ``open_meteo`` → ``RecentWeatherProvider`` over a ``ForecastClient`` and a
    Phase 1 ``OpenMeteoWeatherProvider`` that falls back to ``fixture``.
    ``client`` lets tests inject an ``httpx.Client`` (respx).
    """
    if config.weather_provider != "open_meteo":
        return fixture
    tz = config.app_timezone
    return RecentWeatherProvider(
        forecast=ForecastClient(http=client),
        archive=OpenMeteoWeatherProvider(tz=tz, fallback=fixture, client=client),
        tz=tz,
        today=today or (lambda: datetime.now(tz).date()),
    )
