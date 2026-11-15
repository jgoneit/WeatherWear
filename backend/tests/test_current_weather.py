"""Current_Weather_Service (Phase 2 tasks 4.3, Req 10.1~10.5).

Order: selected scenario > Forecast ``current`` (Live mode) > fixture
``today_default``. Forecast traffic is mocked with respx.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.location import LatLon
from app.main import create_app
from app.notifications.current_weather import CurrentWeatherService
from app.providers.weather.fixture import DEFAULT_WEATHER_FIXTURE_PATH, FixtureWeatherProvider
from app.providers.weather.forecast import FORECAST_ENDPOINT, ForecastClient
from app.today_weather import DEFAULT_SCENARIOS_PATH, ScenarioStore, TodayWeatherService, load_scenarios
from tests.notifications_support import FakeClock, make_config

SEOUL = ZoneInfo("Asia/Seoul")
LOCATION = LatLon(35.18, 129.08)
TODAY_DEFAULT = json.loads(DEFAULT_WEATHER_FIXTURE_PATH.read_text(encoding="utf-8"))["today_default"]

CURRENT = {
    "current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"},
    "current": {
        "time": "2026-11-14T09:00",
        "interval": 900,
        "temperature_2m": 11.2,
        "relative_humidity_2m": 61,
        "apparent_temperature": 9.8,
        "precipitation": 0.3,
        "wind_speed_10m": 14.0,
        "weather_code": 61,
    },
}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 14, 0, 5, tzinfo=UTC))  # 09:05 KST


@pytest.fixture
def store() -> ScenarioStore:
    scenarios, errors = load_scenarios(DEFAULT_SCENARIOS_PATH)
    assert errors == [] and scenarios
    return ScenarioStore(scenarios)


def service(tmp_path: Path, clock: FakeClock, store: ScenarioStore, *, live: bool, http=None):
    config = make_config(tmp_path, weather_provider="open_meteo" if live else "fixture")
    fixture = FixtureWeatherProvider(tz=config.app_timezone, now=clock)
    forecast = ForecastClient(http=http) if live else None
    return CurrentWeatherService(config, forecast, store, fixture, clock)


def assert_today_default(weather) -> None:
    assert weather.provider == "fixture"
    assert weather.is_fixture is True
    assert weather.actual_temperature_c == TODAY_DEFAULT["actual_temperature_c"]
    assert weather.relative_humidity_pct == TODAY_DEFAULT["relative_humidity_pct"]
    assert weather.scenario_id is None


def test_scenario_overrides_current_weather(tmp_path: Path, clock: FakeClock, store: ScenarioStore) -> None:
    scenario = store.list()[0]
    store.select(scenario.id)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
        weather = service(tmp_path, clock, store, live=True).get_current(LOCATION)
    assert route.call_count == 0
    assert weather.provider == "scenario"
    assert weather.is_fixture is True
    assert weather.scenario_id == scenario.id
    assert weather.actual_temperature_c == scenario.actual_temperature_c
    assert weather.observed_at == datetime(2026, 11, 14, 9, 0, tzinfo=SEOUL)


def test_scenario_matches_phase1_today_service(tmp_path: Path, clock: FakeClock, store: ScenarioStore) -> None:
    store.select(store.list()[-1].id)
    config = make_config(tmp_path)
    fixture = FixtureWeatherProvider(tz=config.app_timezone, now=clock)
    phase1 = TodayWeatherService(config, fixture, store, now=clock)
    assert service(tmp_path, clock, store, live=False).get_current(LOCATION) == phase1.get_today()


@respx.mock
def test_live_current_weather_mapping(tmp_path: Path, clock: FakeClock, store: ScenarioStore) -> None:
    route = respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
    weather = service(tmp_path, clock, store, live=True).get_current(LOCATION)
    assert route.call_count == 1
    params = route.calls.last.request.url.params
    assert params["latitude"] == "35.18" and params["longitude"] == "129.08"
    assert params["timezone"] == "Asia/Seoul"
    assert weather.provider == "open_meteo"
    assert weather.is_fixture is False
    assert weather.actual_temperature_c == 11.2
    assert weather.relative_humidity_pct == 61.0
    assert weather.apparent_temperature_c == 9.8
    assert weather.precipitation_mm == 0.3
    assert weather.wind_speed_kmh == 14.0
    assert weather.observed_at == datetime(2026, 11, 14, 9, 0, tzinfo=SEOUL)


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"current": {"time": "2026-11-14T09:00", "temperature_2m": None}}),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
    ],
)
def test_live_failure_uses_today_default(
    tmp_path: Path, clock: FakeClock, store: ScenarioStore, response, caplog: pytest.LogCaptureFixture
) -> None:
    if isinstance(response, Exception):
        respx.get(FORECAST_ENDPOINT).mock(side_effect=response)
    else:
        respx.get(FORECAST_ENDPOINT).mock(return_value=response)
    caplog.set_level(logging.WARNING, logger="app.notifications.current_weather")
    weather = service(tmp_path, clock, store, live=True).get_current(LOCATION)
    assert_today_default(weather)
    warnings = [r for r in caplog.records if r.name == "app.notifications.current_weather"]
    assert warnings and all(r.levelno == logging.WARNING for r in warnings)
    assert "35.18" not in caplog.text and "129.08" not in caplog.text


def test_fixture_mode_no_network(
    tmp_path: Path, clock: FakeClock, store: ScenarioStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_connect(self, address):  # noqa: ANN001
        raise AssertionError(f"fixture mode must not connect: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
        weather = service(tmp_path, clock, store, live=False).get_current(LOCATION)
    assert route.call_count == 0
    assert_today_default(weather)


def test_phase1_weather_today_unchanged(tmp_path: Path) -> None:
    """Fixture mode: GET /api/weather/today is still Phase 1's today_default, and the
    brief's current weather at the default location has the same values."""
    config = make_config(tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
        with TestClient(create_app(config)) as client:
            body = client.get("/api/weather/today").json()
            container = client.app.state.container
            current = CurrentWeatherService(
                config, None, container.scenario_store, container.fixture_weather
            ).get_current(container.location.default)
    assert route.call_count == 0
    assert set(body) == {
        "actual_temperature_c", "apparent_temperature_c", "relative_humidity_pct",
        "wind_speed_kmh", "precipitation_mm", "observed_at", "provider", "is_fixture",
        "scenario_id", "scenario_name",
    }
    assert body["provider"] == "fixture" and body["is_fixture"] is True
    assert body["actual_temperature_c"] == TODAY_DEFAULT["actual_temperature_c"]
    assert body["actual_temperature_c"] == current.actual_temperature_c
    assert body["relative_humidity_pct"] == current.relative_humidity_pct
