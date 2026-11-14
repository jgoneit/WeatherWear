"""Weather provider interface (design.md §5)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain import WeatherProviderName, WeatherReading


@runtime_checkable
class WeatherProvider(Protocol):
    """Returns the weather at (or nearest to) ``at`` for one location.

    Implementations: ``OpenMeteoWeatherProvider`` (falls back to fixture data
    internally, Req 4.7) and ``FixtureWeatherProvider`` (Req 4.6, 4.10).
    """

    name: WeatherProviderName

    def get_weather(self, at: datetime, latitude: float, longitude: float) -> WeatherReading: ...
