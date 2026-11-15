"""Today's weather: fixed demo scenarios and the provider lookup (design.md §6).

- ``load_scenarios()`` reads ``data/demo/scenarios.json`` once at startup and
  keeps only valid entries (Req 5.2, 5.6).
- ``ScenarioStore`` holds the selected scenario in process memory, so a restart
  clears it (Req 5.3). Selecting an unknown id raises ``UnknownScenario`` and
  leaves the current selection unchanged (Req 5.7).
- ``TodayWeatherService.get_today()`` uses the selected scenario if there is
  one, otherwise asks the injected Weather_Provider for "now" at the configured
  default coordinates (Req 5.1, 5.4, 5.5). ``OpenMeteoWeatherProvider`` already
  falls back to fixture data internally (Req 5.8); if a provider raises anyway,
  the optional ``fallback`` provider is used.
- Phase 2 (decision A8, Req 10.7): with the optional ``live_current`` callable
  (wired in tasks 7.1 only when ``WEATHER_PROVIDER=open_meteo``), today's
  weather without a scenario comes from it instead: the Current_Weather_Service
  at the brief location, which already marks a fixture fallback with
  ``provider='fixture'``. ``live_current=None`` (the default) keeps the Phase 1
  behaviour unchanged.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.config import AppConfig
from app.domain import WeatherContext, WeatherReading
from app.providers.weather.base import WeatherProvider

logger = logging.getLogger(__name__)

# backend/app/today_weather.py -> repository root is parents[2].
DEFAULT_SCENARIOS_PATH = Path(__file__).resolve().parents[2] / "data" / "demo" / "scenarios.json"

_Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]  # int/float, never bool or str
_NonEmpty = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class WeatherScenario(BaseModel):
    """One fixed demo weather scenario (Req 5.2, 15.9)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: _NonEmpty
    name: _NonEmpty
    actual_temperature_c: _Finite
    apparent_temperature_c: _Finite | None = None
    relative_humidity_pct: Annotated[_Finite, Field(ge=0.0, le=100.0)] | None = None
    wind_speed_kmh: _Finite | None = None
    precipitation_mm: _Finite | None = None


class UnknownScenario(LookupError):
    """Raised when a scenario id is not in the loaded list (API maps it to 404)."""

    def __init__(self, scenario_id: str) -> None:
        super().__init__("unknown weather scenario")
        self.scenario_id = scenario_id


def load_scenarios(path: Path = DEFAULT_SCENARIOS_PATH) -> tuple[list[WeatherScenario], list[str]]:
    """Read the scenario file and return (valid scenarios in file order, errors).

    Never raises. A missing or unparsable file returns ``([], [error])``. Each
    entry is validated on its own; invalid entries and entries whose id was
    already seen are dropped with an error message. Errors are also logged.
    """
    errors: list[str] = []

    def fail(message: str) -> None:
        errors.append(message)
        logger.error("Weather scenarios: %s", message)

    try:
        payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{Path(path).name} not found")
        return [], errors
    except (OSError, ValueError) as exc:
        fail(f"{Path(path).name} could not be read as JSON ({type(exc).__name__})")
        return [], errors

    raw_items = payload.get("scenarios") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        fail('expected an object with a "scenarios" list')
        return [], errors

    scenarios: list[WeatherScenario] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        try:
            scenario = WeatherScenario.model_validate(raw)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) or "<entry>" for e in exc.errors()})
            fail(f"entry #{index} is invalid ({', '.join(fields)})")
            continue
        if scenario.id in seen:
            fail(f"entry #{index} has duplicate id {scenario.id!r}; keeping the first one")
            continue
        seen.add(scenario.id)
        scenarios.append(scenario)
    return scenarios, errors


class ScenarioStore:
    """In-memory scenario list and current selection (reset on restart)."""

    def __init__(self, scenarios: list[WeatherScenario]) -> None:
        self._scenarios = list(scenarios)
        self._by_id = {s.id: s for s in self._scenarios}
        self._selected: WeatherScenario | None = None
        self._lock = threading.Lock()

    def list(self) -> list[WeatherScenario]:
        return list(self._scenarios)

    def selected(self) -> WeatherScenario | None:
        with self._lock:
            return self._selected

    def select(self, scenario_id: str) -> WeatherScenario:
        scenario = self._by_id.get(scenario_id)
        if scenario is None:
            raise UnknownScenario(scenario_id)
        with self._lock:
            self._selected = scenario
        return scenario

    def clear(self) -> None:
        with self._lock:
            self._selected = None


def scenario_weather(scenario: WeatherScenario, now_local: datetime) -> WeatherContext:
    """Today's WeatherContext for a selected scenario (Req 5.3; Phase 2 Req 10.4).

    A scenario has no observation time, so ``observed_at`` is the current hour
    of ``now_local`` (already in APP_TIMEZONE). Shared by TodayWeatherService
    and the Phase 2 Current_Weather_Service so both give the same value.
    """
    return WeatherContext(
        actual_temperature_c=scenario.actual_temperature_c,
        apparent_temperature_c=scenario.apparent_temperature_c,
        relative_humidity_pct=scenario.relative_humidity_pct,
        wind_speed_kmh=scenario.wind_speed_kmh,
        precipitation_mm=scenario.precipitation_mm,
        observed_at=now_local.replace(minute=0, second=0, microsecond=0),
        provider="scenario",
        is_fixture=True,
        scenario_id=scenario.id,
        scenario_name=scenario.name,
    )


def _from_reading(reading: WeatherReading) -> WeatherContext:
    return WeatherContext(
        actual_temperature_c=reading.actual_temperature_c,
        apparent_temperature_c=reading.apparent_temperature_c,
        relative_humidity_pct=reading.relative_humidity_pct,
        wind_speed_kmh=reading.wind_speed_kmh,
        precipitation_mm=reading.precipitation_mm,
        observed_at=reading.observed_at,
        provider=reading.provider,
        is_fixture=reading.is_fixture,
        scenario_id=None,
        scenario_name=None,
    )


class TodayWeatherService:
    """Builds today's WeatherContext: selected scenario > live current (Phase 2,
    Live mode only) > provider > fallback."""

    def __init__(
        self,
        config: AppConfig,
        provider: WeatherProvider,
        store: ScenarioStore,
        *,
        fallback: WeatherProvider | None = None,
        now: Callable[[], datetime] | None = None,
        live_current: Callable[[], WeatherContext] | None = None,
    ) -> None:
        self._tz: ZoneInfo = config.app_timezone
        self._latitude = config.default_latitude
        self._longitude = config.default_longitude
        self._provider = provider
        self._fallback = fallback
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))
        self._live_current = live_current

    @property
    def store(self) -> ScenarioStore:
        return self._store

    def get_today(self) -> WeatherContext:
        now = self._now().astimezone(self._tz)
        scenario = self._store.selected()
        if scenario is not None:
            return scenario_weather(scenario, now)

        if self._live_current is not None:
            # Current_Weather_Service at the brief location (Req 10.7). It never
            # raises for Forecast problems; anything unexpected falls through
            # to the Phase 1 provider path below.
            try:
                return self._live_current()
            except Exception as exc:
                logger.warning(
                    "Live current weather failed (%s); using the Phase 1 weather provider",
                    type(exc).__name__,
                )

        try:
            reading = self._provider.get_weather(now, self._latitude, self._longitude)
        except Exception as exc:
            if self._fallback is None:
                raise
            logger.warning(
                "Today's weather from %s failed (%s); using fixture weather",
                getattr(self._provider, "name", "provider"),
                type(exc).__name__,
            )
            reading = self._fallback.get_weather(now, self._latitude, self._longitude)
        return _from_reading(reading)
