"""Live-mode home weather (Phase 2 tasks 4.16, decision A8, Req 10.5, 10.7, 10.8, 20.3 (n)).

``TodayWeatherService(..., live_current=...)`` is wired here the way tasks 7.1
does it in Live mode: ``live_current`` is the flow B runtime's
Current_Weather_Service call at the brief location. Without it (fixture mode)
the Phase 1 behaviour is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.deps import get_daily_brief_service, get_notification_log, get_today_weather_service
from app.main import create_app
from app.notifications.runtime import build_brief_runtime
from app.providers.weather.fixture import DEFAULT_WEATHER_FIXTURE_PATH
from app.providers.weather.forecast import FORECAST_ENDPOINT
from app.providers.weather.open_meteo import ARCHIVE_URL
from app.today_weather import TodayWeatherService
from tests.notifications_support import add_outfit, make_config

TODAY_DEFAULT = json.loads(DEFAULT_WEATHER_FIXTURE_PATH.read_text(encoding="utf-8"))["today_default"]
WEATHER_FIELDS = {
    "actual_temperature_c", "apparent_temperature_c", "relative_humidity_pct", "wind_speed_kmh",
    "precipitation_mm", "observed_at", "provider", "is_fixture", "scenario_id", "scenario_name",
}
CURRENT = {
    "current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"},
    "current": {
        "time": "2026-11-14T07:30",
        "interval": 900,
        "temperature_2m": 6.4,
        "relative_humidity_2m": 72,
        "apparent_temperature": 3.9,
        "precipitation": 0.0,
        "wind_speed_10m": 18.0,
        "weather_code": 2,
    },
}


def live_app(tmp_path: Path, *, weather_provider: str = "open_meteo"):
    """App with tasks 7.1's A8 wiring: live_current only in Live mode."""
    app = create_app(make_config(tmp_path, weather_provider=weather_provider))
    config, container = app.state.config, app.state.container
    rt = build_brief_runtime(config, container)
    live_current = rt.live_current if config.weather_provider == "open_meteo" else None
    today = TodayWeatherService(
        config, container.weather_provider, container.scenario_store,
        fallback=container.fixture_weather, live_current=live_current,
    )
    container.today_weather = today  # the brief's RecommendationService reads it too
    app.dependency_overrides[get_today_weather_service] = lambda: today
    app.dependency_overrides[get_daily_brief_service] = lambda: rt.daily_brief
    app.dependency_overrides[get_notification_log] = lambda: rt.notification_log
    return app


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        router.get(ARCHIVE_URL).mock(return_value=httpx.Response(500))  # Phase 1 path must not be used
        yield router


def test_live_home_weather_uses_last_location_forecast(tmp_path: Path, mock) -> None:
    forecast = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
    with TestClient(live_app(tmp_path)) as client:
        assert client.post("/api/location", json={"latitude": 35.87136, "longitude": 128.60142}).status_code == 200
        body = client.get("/api/weather/today").json()
        params = forecast.calls.last.request.url.params
        assert (params["latitude"], params["longitude"]) == ("35.87", "128.6")  # rounded Last_Location
        assert set(body) == WEATHER_FIELDS  # WeatherContextOut unchanged (Req 10.8)
        assert body["provider"] == "open_meteo" and body["is_fixture"] is False
        assert body["actual_temperature_c"] == 6.4 and body["relative_humidity_pct"] == 72.0
        assert body["apparent_temperature_c"] == 3.9 and body["wind_speed_kmh"] == 18.0

        recommendations = client.get("/api/recommendations/today").json()
        assert recommendations["today"] == body
    assert mock.routes[0].call_count == 0  # no Archive call for today's weather


def test_live_home_weather_default_location_without_report(tmp_path: Path, mock) -> None:
    forecast = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
    with TestClient(live_app(tmp_path)) as client:
        assert client.get("/api/weather/today").json()["provider"] == "open_meteo"
        params = forecast.calls.last.request.url.params
        assert (params["latitude"], params["longitude"]) == ("37.5665", "126.978")


@pytest.mark.parametrize("failure", [httpx.Response(500), httpx.ReadTimeout("slow")])
def test_live_home_weather_fallback_marks_fixture(tmp_path: Path, mock, failure) -> None:
    route = mock.get(FORECAST_ENDPOINT)
    if isinstance(failure, Exception):
        route.mock(side_effect=failure)
    else:
        route.mock(return_value=failure)
    with TestClient(live_app(tmp_path)) as client:
        body = client.get("/api/weather/today").json()
        assert set(body) == WEATHER_FIELDS
        assert body["provider"] == "fixture" and body["is_fixture"] is True
        assert body["actual_temperature_c"] == TODAY_DEFAULT["actual_temperature_c"]
        assert body["relative_humidity_pct"] == TODAY_DEFAULT["relative_humidity_pct"]
        assert client.get("/api/recommendations/today").json()["today"]["provider"] == "fixture"


def test_fixture_home_weather_unchanged(tmp_path: Path, mock) -> None:
    forecast = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
    plain = create_app(make_config(tmp_path / "plain"))
    with TestClient(plain) as client:
        phase1 = client.get("/api/weather/today").json()
    with TestClient(live_app(tmp_path, weather_provider="fixture")) as client:
        client.post("/api/location", json={"latitude": 35.18, "longitude": 129.08})
        wired = client.get("/api/weather/today").json()
    assert forecast.call_count == 0
    assert wired == phase1
    assert wired["provider"] == "fixture"
    assert wired["actual_temperature_c"] == TODAY_DEFAULT["actual_temperature_c"]


def test_scenario_still_wins_in_live_mode(tmp_path: Path, mock) -> None:
    forecast = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
    with TestClient(live_app(tmp_path)) as client:
        assert client.put("/api/demo/scenarios/selected", json={"scenario_id": "cold-snap"}).status_code == 200
        body = client.get("/api/weather/today").json()
        assert body["provider"] == "scenario" and body["scenario_id"] == "cold-snap"
        assert body["actual_temperature_c"] == -16.0
        assert forecast.call_count == 0
        assert client.delete("/api/demo/scenarios/selected").status_code in (200, 204)
        assert client.get("/api/weather/today").json()["provider"] == "open_meteo"


def test_home_and_brief_same_weather(tmp_path: Path, mock) -> None:
    mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=CURRENT))
    with TestClient(live_app(tmp_path)) as client:
        add_outfit(client.app.state.container.repository, 6.0, vote="comfortable")
        client.post("/api/location", json={"latitude": 37.4563, "longitude": 126.7052})
        home = client.get("/api/weather/today").json()
        recommendation = client.get("/api/recommendations/today").json()
        note = client.post("/api/daily-brief/run").json()["notification"]
    assert note["body"].startswith("오늘 인천 6.4°C, 습도 72%.")
    assert home["actual_temperature_c"] == 6.4 and home["relative_humidity_pct"] == 72.0
    assert note["weather_provider"] == home["provider"] == "open_meteo"
    assert note["outfit_id"] == recommendation["items"][0]["outfit"]["id"]
