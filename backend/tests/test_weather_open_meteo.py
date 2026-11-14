"""Unit tests for OpenMeteoWeatherProvider and build_weather_provider (task 5.6).

All HTTP traffic is mocked with respx; the conftest socket guard blocks real
connections.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.config import resolve_config
from app.providers.weather.factory import build_weather_provider
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.open_meteo import (
    ARCHIVE_URL,
    HOURLY_VARIABLES,
    OpenMeteoWeatherProvider,
)

SEOUL = ZoneInfo("Asia/Seoul")
LAT, LON = 37.5665, 126.978
# 2025-01-07T23:30Z is 2025-01-08 08:30 in Seoul: the request date must be the local date.
AT = datetime(2025, 1, 7, 23, 30, tzinfo=timezone.utc)


def _payload(temps: list[float | None]) -> dict:
    return {
        "latitude": 37.55,
        "longitude": 127.0,
        "timezone": "Asia/Seoul",
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "°C",
            "precipitation": "mm",
            "relative_humidity_2m": "%",
        },
        "hourly": {
            "time": [f"2025-01-08T{h:02d}:00" for h in range(24)],
            "temperature_2m": temps,
            "precipitation": [0.2 if h == 9 else 0.0 for h in range(24)],
            "relative_humidity_2m": [50 + h for h in range(24)],
        },
    }


@pytest.fixture
def fixture_provider(tmp_path: Path) -> FixtureWeatherProvider:
    # Missing file -> deterministic seasonal formula only.
    return FixtureWeatherProvider(path=tmp_path / "missing.json", tz=SEOUL)


@pytest.fixture
def provider(fixture_provider: FixtureWeatherProvider):
    with httpx.Client(timeout=10.0) as client:
        yield OpenMeteoWeatherProvider(tz=SEOUL, fallback=fixture_provider, client=client)


@respx.mock
def test_sends_one_archive_request_with_expected_params(provider: OpenMeteoWeatherProvider) -> None:
    temps = [-5.0 + 0.5 * h for h in range(24)]
    route = respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, json=_payload(temps)))

    reading = provider.get_weather(AT, LAT, LON)

    assert route.call_count == 1
    params = dict(route.calls.last.request.url.params)
    assert params == {
        "latitude": str(LAT),
        "longitude": str(LON),
        "start_date": "2025-01-08",
        "end_date": "2025-01-08",
        "hourly": HOURLY_VARIABLES,
        "timezone": "Asia/Seoul",
    }
    assert not any("key" in name.lower() for name in params)

    # 08:30 local: 08:00 and 09:00 tie, the earlier hour wins.
    assert reading.observed_at == datetime(2025, 1, 8, 8, 0, tzinfo=SEOUL)
    assert reading.actual_temperature_c == pytest.approx(-1.0)
    assert reading.relative_humidity_pct == pytest.approx(58)
    assert reading.precipitation_mm == pytest.approx(0.0)
    assert reading.apparent_temperature_c is None
    assert reading.wind_speed_kmh is None
    assert reading.weather_code is None
    assert reading.provider == "open_meteo"
    assert reading.is_fixture is False


@pytest.mark.parametrize(
    "side_effect",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection refused"),
        httpx.Response(500, json={"error": True}),
        httpx.Response(400, json={"error": True, "reason": "bad"}),
        httpx.Response(200, content=b"not json{"),
        httpx.Response(200, json=_payload([None] * 24)),
        httpx.Response(200, json={"hourly": "broken"}),
    ],
    ids=["timeout", "network", "http500", "http400", "bad_json", "no_temperature", "malformed"],
)
@respx.mock
def test_failures_fall_back_to_fixture(
    side_effect: object,
    provider: OpenMeteoWeatherProvider,
    fixture_provider: FixtureWeatherProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    if isinstance(side_effect, httpx.Response):
        respx.get(ARCHIVE_URL).mock(return_value=side_effect)
    else:
        respx.get(ARCHIVE_URL).mock(side_effect=side_effect)

    with caplog.at_level("WARNING", logger="app.providers.weather.open_meteo"):
        reading = provider.get_weather(AT, LAT, LON)

    assert reading == fixture_provider.get_weather(AT, LAT, LON)
    assert reading.provider == "fixture"
    assert reading.is_fixture is True
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_default_client_uses_ten_second_timeout(fixture_provider: FixtureWeatherProvider) -> None:
    provider = OpenMeteoWeatherProvider(tz=SEOUL, fallback=fixture_provider)
    try:
        assert provider._client.timeout == httpx.Timeout(10.0)
    finally:
        provider.close()


def test_naive_datetime_rejected(provider: OpenMeteoWeatherProvider) -> None:
    with pytest.raises(ValueError):
        provider.get_weather(datetime(2025, 1, 8, 8, 30), LAT, LON)


def test_factory_selects_provider(fixture_provider: FixtureWeatherProvider) -> None:
    fixture_cfg, _ = resolve_config({"WEATHER_PROVIDER": "fixture"})
    assert build_weather_provider(fixture_cfg, fixture_provider) is fixture_provider

    live_cfg, _ = resolve_config({"WEATHER_PROVIDER": "open_meteo", "APP_TIMEZONE": "Asia/Seoul"})
    with httpx.Client() as client:
        live = build_weather_provider(live_cfg, fixture_provider, client=client)
        assert isinstance(live, OpenMeteoWeatherProvider)
        assert live.name == "open_meteo"
        # Fallback goes to the same fixture instance.
        with respx.mock:
            respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(503))
            at = datetime(2025, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=9)))
            assert live.get_weather(at, LAT, LON) == fixture_provider.get_weather(at, LAT, LON)
