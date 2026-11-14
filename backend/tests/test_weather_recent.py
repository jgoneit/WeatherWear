"""Capture-time weather endpoint choice for watched photos (tasks 3.9, Req 7.1–7.5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.config import resolve_config
from app.providers.weather.fixture import DEFAULT_WEATHER_FIXTURE_PATH, FixtureWeatherProvider
from app.providers.weather.forecast import FORECAST_ENDPOINT
from app.providers.weather.open_meteo import ARCHIVE_URL
from app.gallery.weather_recent import (
    FORECAST_DAYS,
    RecentWeatherProvider,
    build_recent_weather_provider,
    choose_weather_endpoint,
)

SEOUL = ZoneInfo("Asia/Seoul")
KST = timezone(timedelta(hours=9))
TODAY = date(2026, 11, 14)
LAT, LON = 37.57, 126.98


def hourly_payload(days: list[date], temp_at: float) -> dict:
    times = [f"{d.isoformat()}T{h:02d}:00" for d in days for h in range(24)]
    return {
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm",
                         "relative_humidity_2m": "%"},
        "hourly": {
            "time": times,
            "temperature_2m": [temp_at] * len(times),
            "precipitation": [0.0] * len(times),
            "relative_humidity_2m": [60] * len(times),
        },
    }


@pytest.mark.parametrize(
    ("captured", "expected"),
    [
        (datetime(2026, 11, 14, 8, 0, tzinfo=KST), ("forecast", 0)),
        (datetime(2026, 11, 15, 8, 0, tzinfo=KST), ("forecast", 0)),  # tomorrow
        (datetime(2026, 11, 16, 0, 0, tzinfo=KST), ("archive", None)),
        (datetime(2026, 11, 7, 0, 0, tzinfo=KST), ("forecast", 7)),
        (datetime(2026, 11, 6, 23, 59, tzinfo=KST), ("archive", None)),
        # 2026-11-06T16:00Z is 11-07 01:00 in Seoul: the local date decides.
        (datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc), ("forecast", 7)),
    ],
)
def test_choose_weather_endpoint(captured: datetime, expected: tuple) -> None:
    assert choose_weather_endpoint(captured, TODAY, SEOUL) == expected


@pytest.fixture
def fixture_weather(tmp_path: Path) -> FixtureWeatherProvider:
    return FixtureWeatherProvider(path=tmp_path / "missing.json", tz=SEOUL)


@pytest.fixture
def provider(fixture_weather: FixtureWeatherProvider):
    cfg, _ = resolve_config({"WEATHER_PROVIDER": "open_meteo"})
    with httpx.Client(timeout=10.0) as client:
        built = build_recent_weather_provider(cfg, fixture_weather, client=client, today=lambda: TODAY)
        assert isinstance(built, RecentWeatherProvider)
        yield built


@respx.mock
def test_recent_photo_uses_forecast_past_days(provider: RecentWeatherProvider) -> None:
    at = datetime(2026, 11, 12, 8, 20, tzinfo=KST)
    route = respx.get(FORECAST_ENDPOINT).mock(
        return_value=httpx.Response(200, json=hourly_payload([TODAY - timedelta(days=2), TODAY], 11.5))
    )
    archive = respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(500))

    reading = provider.get_weather(at, LAT, LON)

    assert route.call_count == 1 and archive.call_count == 0
    params = dict(route.calls.last.request.url.params)
    assert params == {
        "latitude": "37.57", "longitude": "126.98",
        "hourly": "temperature_2m,precipitation,relative_humidity_2m",
        "timezone": "Asia/Seoul", "past_days": "2", "forecast_days": str(FORECAST_DAYS),
    }
    assert reading.provider == "open_meteo" and reading.is_fixture is False
    assert reading.actual_temperature_c == 11.5 and reading.relative_humidity_pct == 60
    assert reading.observed_at == datetime(2026, 11, 12, 8, 0, tzinfo=SEOUL)


@respx.mock
def test_old_photo_uses_archive(provider: RecentWeatherProvider) -> None:
    at = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
    forecast = respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json={}))
    archive = respx.get(ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json=hourly_payload([date(2026, 10, 1)], 18.0))
    )
    reading = provider.get_weather(at, LAT, LON)
    assert forecast.call_count == 0 and archive.call_count == 1
    assert dict(archive.calls.last.request.url.params)["start_date"] == "2026-10-01"
    assert reading.provider == "open_meteo" and reading.actual_temperature_c == 18.0


@respx.mock
def test_forecast_failure_falls_back_to_archive_then_fixture(
    provider: RecentWeatherProvider, fixture_weather: FixtureWeatherProvider
) -> None:
    at = datetime(2026, 11, 13, 7, 0, tzinfo=KST)
    forecast = respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(503))
    archive = respx.get(ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json=hourly_payload([date(2026, 11, 13)], 9.0))
    )
    reading = provider.get_weather(at, LAT, LON)
    assert forecast.call_count == 1 and archive.call_count == 1
    assert reading.provider == "open_meteo" and reading.actual_temperature_c == 9.0

    # Forecast answers without the selected hour's temperature, Archive times out → fixture.
    empty = hourly_payload([date(2026, 11, 13)], 9.0)
    empty["hourly"]["temperature_2m"] = [None] * 24
    forecast.mock(return_value=httpx.Response(200, json=empty))
    archive.mock(side_effect=httpx.ConnectTimeout("timeout"))
    reading = provider.get_weather(at, LAT, LON)
    assert reading.provider == "fixture" and reading.is_fixture is True
    assert reading == fixture_weather.get_weather(at, LAT, LON)


@respx.mock
def test_forecast_invalid_json_falls_back(provider: RecentWeatherProvider) -> None:
    at = datetime(2026, 11, 14, 7, 0, tzinfo=KST)
    respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, text="not json"))
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, json=hourly_payload([TODAY], 3.0)))
    assert provider.get_weather(at, LAT, LON).actual_temperature_c == 3.0


def test_fixture_mode_today_photo_gets_today_default() -> None:
    cfg, _ = resolve_config({})
    assert cfg.weather_provider == "fixture"
    now = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)
    fixture = FixtureWeatherProvider(path=DEFAULT_WEATHER_FIXTURE_PATH, tz=SEOUL, now=lambda: now)
    built = build_recent_weather_provider(cfg, fixture)
    assert built is fixture  # Phase 1 fixture provider, no network (Req 7.4)
    reading = built.get_weather(datetime(2026, 11, 14, 11, 30, tzinfo=KST), LAT, LON)
    default = FixtureWeatherProvider(path=DEFAULT_WEATHER_FIXTURE_PATH, tz=SEOUL)._today_default
    assert default is not None
    assert reading.actual_temperature_c == default.actual_temperature_c
    assert (reading.provider, reading.is_fixture) == ("fixture", True)
    # Another day uses the deterministic seasonal formula, still fixture.
    other = built.get_weather(datetime(2026, 6, 1, 15, 0, tzinfo=KST), LAT, LON)
    assert (other.provider, other.is_fixture) == ("fixture", True)


def test_default_today_uses_app_timezone(fixture_weather: FixtureWeatherProvider) -> None:
    cfg, _ = resolve_config({"WEATHER_PROVIDER": "open_meteo"})
    cfg = replace(cfg)
    built = build_recent_weather_provider(cfg, fixture_weather, client=httpx.Client())
    assert isinstance(built, RecentWeatherProvider)
    assert built._today() == datetime.now(SEOUL).date()
