"""Current_Weather_Service: today's current weather for the daily brief (design.md §13).

Order (Req 10.2~10.4):

1. A selected Weather_Scenario → ``provider='scenario'`` (the same conversion as
   Phase 1 ``TodayWeatherService``, ``today_weather.scenario_weather``).
2. ``WEATHER_PROVIDER=open_meteo`` → one Forecast ``current`` request at the
   given location, mapped by ``map_current_response`` (``provider='open_meteo'``).
3. Otherwise, or when step 2 fails (timeout, error, no ``temperature_2m``):
   the fixture provider for "now", i.e. ``today_default`` of ``weather.json``
   (``provider='fixture'``, ``is_fixture=True``); a Forecast failure is logged
   as a WARNING without coordinates.

Fixture mode makes no network call. ``get_current`` never raises for weather
problems, so a weather failure never fails the brief (Req 12.5). Phase 1
``TodayWeatherService`` and ``/api/weather/today`` are not changed here; in
Live mode tasks 7.1 passes ``get_current`` to it as ``live_current`` (4.16).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.config import AppConfig
from app.domain import WeatherContext
from app.location import LatLon
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.forecast import ForecastClient, map_current_response
from app.today_weather import ScenarioStore, scenario_weather

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CurrentWeatherService:
    def __init__(
        self,
        config: AppConfig,
        forecast: ForecastClient | None,
        store: ScenarioStore,
        fixture: FixtureWeatherProvider,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._tz = config.app_timezone
        self._live = config.weather_provider == "open_meteo"
        self._forecast = forecast
        self._store = store
        self._fixture = fixture
        self._clock = clock

    def get_current(self, location: LatLon) -> WeatherContext:
        now = self._clock().astimezone(self._tz)
        scenario = self._store.selected()
        if scenario is not None:
            return scenario_weather(scenario, now)

        if self._live and self._forecast is not None:
            live = self._live_current(location)
            if live is not None:
                return live

        return self._fixture_today(now, location)

    def _live_current(self, location: LatLon) -> WeatherContext | None:
        try:
            payload = self._forecast.get_current(  # type: ignore[union-attr]
                location.latitude, location.longitude, self._tz
            )
            weather = map_current_response(payload, self._tz)
        except Exception as exc:  # ForecastError, or anything unexpected
            logger.warning(
                "Forecast 현재 날씨 조회 실패(%s), fixture today_default로 대체합니다.",
                type(exc).__name__,
            )
            return None
        if weather is None:
            logger.warning(
                "Forecast 현재 날씨에 temperature_2m이 없어 fixture today_default로 대체합니다."
            )
        return weather

    def _fixture_today(self, now: datetime, location: LatLon) -> WeatherContext:
        reading = self._fixture.get_weather(now, location.latitude, location.longitude)
        return WeatherContext(
            actual_temperature_c=reading.actual_temperature_c,
            apparent_temperature_c=reading.apparent_temperature_c,
            relative_humidity_pct=reading.relative_humidity_pct,
            wind_speed_kmh=reading.wind_speed_kmh,
            precipitation_mm=reading.precipitation_mm,
            observed_at=reading.observed_at,
            provider="fixture",
            is_fixture=True,
            scenario_id=None,
            scenario_name=None,
        )
