"""Chooses the Weather_Provider from config (design.md §5, Req 4.5, 4.6, 4.7)."""

from __future__ import annotations

import httpx

from app.config import AppConfig
from app.providers.weather.base import WeatherProvider
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.open_meteo import OpenMeteoWeatherProvider


def build_weather_provider(
    cfg: AppConfig,
    fixture: FixtureWeatherProvider,
    *,
    client: httpx.Client | None = None,
) -> WeatherProvider:
    """``OpenMeteoWeatherProvider`` (falling back to ``fixture``) for
    ``weather_provider=open_meteo``, otherwise ``fixture`` itself.

    ``client`` lets tests inject an ``httpx.Client``.
    """
    if cfg.weather_provider == "open_meteo":
        return OpenMeteoWeatherProvider(tz=cfg.app_timezone, fallback=fixture, client=client)
    return fixture
