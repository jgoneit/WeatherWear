"""Unit tests for weather normalization pure functions (task 5.1, Req 4.2, 4.8, 4.12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.providers.weather.base import WeatherProvider
from app.providers.weather.normalize import (
    map_archive_response,
    normalize_value,
    select_nearest_hour,
)

SEOUL = ZoneInfo("Asia/Seoul")
KST = timezone(timedelta(hours=9))


def _payload(temps, precip=None, humidity=None, units=None, day="2025-11-20"):
    hourly = {
        "time": [f"{day}T{h:02d}:00" for h in range(len(temps))],
        "temperature_2m": temps,
        "precipitation": precip if precip is not None else [0.0] * len(temps),
        "relative_humidity_2m": humidity if humidity is not None else [50] * len(temps),
    }
    return {
        "latitude": 37.55,
        "longitude": 126.98,
        "utc_offset_seconds": 32400,
        "timezone": "Asia/Seoul",
        "hourly_units": units
        or {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm", "relative_humidity_2m": "%"},
        "hourly": hourly,
    }


# ------------------------------------------------------------ select_nearest_hour


def test_select_nearest_hour_empty_returns_none():
    assert select_nearest_hour([], datetime(2025, 1, 1, tzinfo=timezone.utc)) is None


def test_select_nearest_hour_picks_closest():
    times = [datetime(2025, 1, 1, h, tzinfo=KST) for h in range(24)]
    assert select_nearest_hour(times, datetime(2025, 1, 1, 8, 20, tzinfo=KST)) == 8
    assert select_nearest_hour(times, datetime(2025, 1, 1, 8, 40, tzinfo=KST)) == 9


def test_select_nearest_hour_tie_prefers_earlier():
    times = [datetime(2025, 1, 1, 9, tzinfo=KST), datetime(2025, 1, 1, 8, tzinfo=KST)]
    assert select_nearest_hour(times, datetime(2025, 1, 1, 8, 30, tzinfo=KST)) == 1


def test_select_nearest_hour_compares_utc_instants():
    # 08:30+09:00 == 23:30Z the previous day; 23:00Z is the same instant as 08:00 KST.
    times = [datetime(2025, 1, 1, 10, tzinfo=KST), datetime(2024, 12, 31, 23, tzinfo=timezone.utc)]
    assert select_nearest_hour(times, datetime(2025, 1, 1, 8, 20, tzinfo=KST)) == 1


# ---------------------------------------------------------------- normalize_value


@pytest.mark.parametrize(
    ("value", "unit", "target", "expected"),
    [
        (12.5, "°C", "C", 12.5),
        (50.0, "°F", "C", 10.0),
        (2.0, "inch", "mm", 50.8),
        (3.0, "mm", "mm", 3.0),
        (10.0, "m/s", "kmh", 36.0),
        (10.0, "mph", "kmh", 16.09344),
        (20.0, "km/h", "kmh", 20.0),
        (55, "%", "pct", 55.0),
    ],
)
def test_normalize_value_converts(value, unit, target, expected):
    assert normalize_value(value, unit, target) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "unit", "target"),
    [
        (None, "°C", "C"),
        (-0.1, "%", "pct"),
        (100.1, "%", "pct"),
        (10.0, "kelvin", "C"),
        (float("nan"), "°C", "C"),
        (True, "°C", "C"),
    ],
)
def test_normalize_value_returns_none(value, unit, target):
    assert normalize_value(value, unit, target) is None


def test_normalize_value_keeps_humidity_bounds():
    assert normalize_value(0, "%", "pct") == 0.0
    assert normalize_value(100, "%", "pct") == 100.0


# ----------------------------------------------------------- map_archive_response


def test_map_archive_response_selects_nearest_local_hour():
    temps = [float(h) for h in range(24)]
    precip = [0.0] * 24
    precip[8] = 1.2
    humidity = [40] * 24
    humidity[8] = 72
    payload = _payload(temps, precip, humidity)
    target = datetime(2025, 11, 20, 8, 29, tzinfo=KST)

    reading = map_archive_response(payload, target, SEOUL)

    assert reading is not None
    assert reading.observed_at == datetime(2025, 11, 20, 8, tzinfo=KST)
    assert reading.observed_at.utcoffset() == timedelta(hours=9)
    assert reading.actual_temperature_c == 8.0
    assert reading.precipitation_mm == 1.2
    assert reading.relative_humidity_pct == 72.0
    assert reading.apparent_temperature_c is None
    assert reading.wind_speed_kmh is None
    assert reading.weather_code is None
    assert reading.provider == "open_meteo"
    assert reading.is_fixture is False


def test_map_archive_response_target_in_other_offset():
    temps = [float(h) for h in range(24)]
    # 2025-11-19T23:00Z == 2025-11-20T08:00+09:00
    target = datetime(2025, 11, 19, 23, 0, tzinfo=timezone.utc)
    reading = map_archive_response(_payload(temps), target, SEOUL)
    assert reading is not None
    assert reading.actual_temperature_c == 8.0


def test_map_archive_response_converts_units():
    temps = [50.0] * 24
    payload = _payload(
        temps,
        precip=[0.1] * 24,
        units={"temperature_2m": "°F", "precipitation": "inch", "relative_humidity_2m": "%"},
    )
    reading = map_archive_response(payload, datetime(2025, 11, 20, 12, tzinfo=KST), SEOUL)
    assert reading is not None
    assert reading.actual_temperature_c == pytest.approx(10.0)
    assert reading.precipitation_mm == pytest.approx(2.54)


def test_map_archive_response_optional_values_null():
    temps = [5.0] * 24
    payload = _payload(temps, precip=[None] * 24, humidity=[None] * 24)
    reading = map_archive_response(payload, datetime(2025, 11, 20, 12, tzinfo=KST), SEOUL)
    assert reading is not None
    assert reading.precipitation_mm is None
    assert reading.relative_humidity_pct is None


def test_map_archive_response_missing_selected_temperature_returns_none():
    temps = [5.0] * 24
    temps[12] = None
    reading = map_archive_response(_payload(temps), datetime(2025, 11, 20, 12, tzinfo=KST), SEOUL)
    assert reading is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hourly": None},
        {"hourly": {"time": "nope"}},
        {"hourly": {"time": [], "temperature_2m": []}},
        {"hourly": {"time": ["bad"], "temperature_2m": [1.0]}},
        {"hourly": {"time": ["2025-11-20T12:00"]}},
    ],
)
def test_map_archive_response_malformed_returns_none(payload):
    assert map_archive_response(payload, datetime(2025, 11, 20, 12, tzinfo=KST), SEOUL) is None


# ----------------------------------------------------------------- Protocol


def test_weather_provider_protocol_runtime_check():
    class Dummy:
        name = "fixture"

        def get_weather(self, at, latitude, longitude):
            raise NotImplementedError

    assert isinstance(Dummy(), WeatherProvider)
