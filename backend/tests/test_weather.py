"""Weather normalization example tests, area (b) of Req 17.4 (task 5.9).

- ``test_archive_mapping_example``: a fixed Archive API response shaped like a
  real Seoul reply is mapped to a WeatherReading (Req 4.12).
- ``test_open_meteo_no_api_key_param``: the Archive request carries no API key
  (Req 4.5).
- ``test_fixture_weather_marks_fixture``: fixture results are labelled
  ``provider='fixture'`` and ``is_fixture=True`` (Req 4.6).

HTTP is mocked with respx; the conftest socket guard blocks real connections.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.open_meteo import (
    ARCHIVE_URL,
    OpenMeteoWeatherProvider,
    build_archive_params,
)

SEOUL = ZoneInfo("Asia/Seoul")
KST = timezone(timedelta(hours=9))
LAT, LON = 37.5665, 126.978
# Same instant as the demo photo 2025-01-08_081500_KST.jpg.
AT = datetime(2025, 1, 8, 8, 15, tzinfo=KST)

# Fixed reply modelled on a real Archive response for Seoul on 2025-01-08
# (08:00 KST: −5.8°C, 65%, 0.0 mm). Times are local and carry no offset.
SEOUL_ARCHIVE_RESPONSE: dict = {
    "latitude": 37.55,
    "longitude": 127.0,
    "generationtime_ms": 0.12,
    "utc_offset_seconds": 32400,
    "timezone": "Asia/Seoul",
    "timezone_abbreviation": "GMT+9",
    "elevation": 38.0,
    "hourly_units": {
        "time": "iso8601",
        "temperature_2m": "°C",
        "precipitation": "mm",
        "relative_humidity_2m": "%",
    },
    "hourly": {
        "time": [f"2025-01-08T{h:02d}:00" for h in range(24)],
        "temperature_2m": [
            -7.9, -8.1, -8.3, -8.4, -8.5, -8.4, -8.1, -7.2,
            -5.8, -4.6, -3.5, -2.7, -2.1, -1.8, -1.7, -2.0,
            -2.8, -3.9, -4.7, -5.3, -5.8, -6.2, -6.6, -6.9,
        ],
        "precipitation": [0.0] * 24,
        "relative_humidity_2m": [
            72, 73, 74, 74, 75, 75, 74, 70,
            65, 59, 54, 50, 47, 45, 45, 47,
            51, 56, 60, 63, 65, 67, 69, 70,
        ],
    },
}


@pytest.fixture
def fixture_provider(tmp_path: Path) -> FixtureWeatherProvider:
    return FixtureWeatherProvider(path=tmp_path / "missing.json", tz=SEOUL)


@pytest.fixture
def provider(fixture_provider: FixtureWeatherProvider):
    with httpx.Client(timeout=10.0) as client:
        yield OpenMeteoWeatherProvider(tz=SEOUL, fallback=fixture_provider, client=client)


@respx.mock
def test_archive_mapping_example(provider: OpenMeteoWeatherProvider) -> None:
    route = respx.get(ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json=SEOUL_ARCHIVE_RESPONSE)
    )

    reading = provider.get_weather(AT, LAT, LON)

    assert route.call_count == 1
    # 08:15 KST is closest to the 08:00 local hour.
    assert reading.observed_at == datetime(2025, 1, 8, 8, 0, tzinfo=SEOUL)
    assert reading.observed_at.utcoffset() == timedelta(hours=9)
    assert reading.actual_temperature_c == pytest.approx(-5.8)
    assert reading.relative_humidity_pct == pytest.approx(65)
    assert reading.precipitation_mm == pytest.approx(0.0)
    # Not requested from the Archive API, so stored as null.
    assert reading.apparent_temperature_c is None
    assert reading.wind_speed_kmh is None
    assert reading.weather_code is None
    assert reading.provider == "open_meteo"
    assert reading.is_fixture is False


@respx.mock
def test_open_meteo_no_api_key_param(provider: OpenMeteoWeatherProvider) -> None:
    route = respx.get(ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json=SEOUL_ARCHIVE_RESPONSE)
    )

    provider.get_weather(AT, LAT, LON)

    request = route.calls.last.request
    sent = dict(request.url.params)
    assert set(sent) == {"latitude", "longitude", "start_date", "end_date", "hourly", "timezone"}
    assert sent == {k: str(v) for k, v in build_archive_params(AT, LAT, LON, SEOUL).items()}
    assert not any("key" in name.lower() or "token" in name.lower() for name in sent)
    assert "authorization" not in {name.lower() for name in request.headers}
    assert "x-goog-api-key" not in {name.lower() for name in request.headers}


def test_fixture_weather_marks_fixture(tmp_path: Path) -> None:
    fixture_file = tmp_path / "weather.json"
    fixture_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "captured_at": "2025-01-08T08:15:00+09:00",
                        "latitude": LAT,
                        "longitude": LON,
                        "actual_temperature_c": -6.5,
                        "apparent_temperature_c": -10.0,
                        "relative_humidity_pct": 60,
                        "wind_speed_kmh": 9,
                        "precipitation_mm": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    fixture = FixtureWeatherProvider(path=fixture_file, tz=SEOUL)

    # Entry hit: values come from the file.
    hit = fixture.get_weather(AT, LAT, LON)
    assert hit.actual_temperature_c == pytest.approx(-6.5)
    assert hit.apparent_temperature_c == pytest.approx(-10.0)
    assert hit.observed_at == datetime(2025, 1, 8, 8, 0, tzinfo=SEOUL)

    # Entry miss: deterministic seasonal value.
    miss_at = datetime(2025, 7, 1, 14, 40, tzinfo=KST)
    miss = fixture.get_weather(miss_at, 35.1, 129.0)
    assert miss == fixture.get_weather(miss_at, 35.1, 129.0)

    for reading in (hit, miss):
        assert reading.provider == "fixture"
        assert reading.is_fixture is True
