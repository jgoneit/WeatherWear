"""Unit tests for FixtureWeatherProvider (task 5.4, Req 4.6, 4.10)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.domain import CaptureMetadata
from app.ingestion.metadata import read_embedded_metadata
from app.providers.weather.base import WeatherProvider
from app.providers.weather.fixture import (
    DEFAULT_WEATHER_FIXTURE_PATH,
    FixtureWeatherProvider,
    seasonal_temp,
)

SEOUL = ZoneInfo("Asia/Seoul")
PHOTOS_DIR = DEFAULT_WEATHER_FIXTURE_PATH.parents[1] / "photos"
FIXED_NOW = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # 12:00 KST


def _provider(path: Path = DEFAULT_WEATHER_FIXTURE_PATH) -> FixtureWeatherProvider:
    return FixtureWeatherProvider(path, tz=SEOUL, now=lambda: FIXED_NOW)


def _embedded(file_name: str) -> tuple[datetime, float, float]:
    meta = read_embedded_metadata((PHOTOS_DIR / file_name).read_bytes())
    assert isinstance(meta, CaptureMetadata)
    return meta.captured_at, meta.latitude, meta.longitude


def _recorded_entries() -> dict[str, dict]:
    data = json.loads(DEFAULT_WEATHER_FIXTURE_PATH.read_text(encoding="utf-8"))
    return {entry["file"]: entry for entry in data["entries"]}


def test_satisfies_weather_provider_protocol() -> None:
    provider = _provider()
    assert isinstance(provider, WeatherProvider)
    assert provider.name == "fixture"
    assert provider.entry_count == len(_recorded_entries())
    assert provider.recorded is not None


@pytest.mark.parametrize(
    "file_name",
    [
        "007_front_natural_laugh_linen.png",
        "076_winter_navy_long_parka.png",
        "140_mustard_mechanic_hoodie.png",
        "2025-01-08_081500_KST.jpg",
        "2025-07-30_190500_KST.jpg",
    ],
)
def test_real_demo_photos_get_recorded_temperature(file_name: str) -> None:
    """Embedded time/location of a real photo looks up its recorded entry (task 19.19)."""
    captured_at, lat, lon = _embedded(file_name)
    entry = _recorded_entries()[file_name]
    reading = _provider().get_weather(captured_at, lat, lon)
    assert reading.actual_temperature_c == entry["actual_temperature_c"]
    assert reading.observed_at == datetime.fromisoformat(entry["observed_at"])
    assert reading.provider == "fixture"
    assert reading.is_fixture is True


def test_lookup_uses_utc_instant_and_rounded_coordinates() -> None:
    file_name = "076_winter_navy_long_parka.png"
    captured_at, lat, lon = _embedded(file_name)
    same_instant_utc = captured_at.astimezone(UTC)
    reading = _provider().get_weather(same_instant_utc, lat + 0.00001, lon - 0.00002)
    assert reading.actual_temperature_c == _recorded_entries()[file_name]["actual_temperature_c"]


def test_entry_fields_are_copied_and_observed_at_floored_to_hour(tmp_path: Path) -> None:
    path = tmp_path / "weather.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "file": "a.png",
                        "captured_at": "2025-07-14T18:26:30+09:00",
                        "latitude": 37.5665,
                        "longitude": 126.978,
                        "actual_temperature_c": 23.5,
                        "apparent_temperature_c": 25.0,
                        "relative_humidity_pct": 88,
                        "wind_speed_kmh": 7,
                        "precipitation_mm": 3.2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    at = datetime(2025, 7, 14, 9, 26, 30, tzinfo=UTC)  # same instant as 18:26:30+09:00
    reading = _provider(path).get_weather(at, 37.5665, 126.978)
    assert reading.actual_temperature_c == 23.5
    assert reading.apparent_temperature_c == 25.0
    assert reading.relative_humidity_pct == 88
    assert reading.wind_speed_kmh == 7
    assert reading.precipitation_mm == 3.2
    assert reading.weather_code is None
    assert reading.observed_at == datetime(2025, 7, 14, 18, 0, tzinfo=SEOUL)
    assert reading.observed_at.utcoffset() == timedelta(hours=9)


def test_today_uses_today_default() -> None:
    now_local = FIXED_NOW.astimezone(SEOUL)
    reading = _provider().get_weather(now_local, 37.5665, 126.978)
    default = json.loads(DEFAULT_WEATHER_FIXTURE_PATH.read_text(encoding="utf-8"))["today_default"]
    assert reading.actual_temperature_c == default["actual_temperature_c"]
    assert reading.apparent_temperature_c is None  # like the recorded weather (Req 4.12)
    assert reading.relative_humidity_pct == default["relative_humidity_pct"]
    assert reading.wind_speed_kmh == default["wind_speed_kmh"]
    assert reading.precipitation_mm == default["precipitation_mm"]
    assert reading.observed_at == datetime(2026, 11, 14, 12, 0, tzinfo=SEOUL)
    assert reading.is_fixture is True


def test_other_unknown_date_uses_seasonal_formula() -> None:
    at = datetime(2024, 3, 2, 15, 40, tzinfo=SEOUL)
    reading = _provider().get_weather(at, 37.5665, 126.978)
    assert reading.actual_temperature_c == seasonal_temp(3, 15)
    assert reading.apparent_temperature_c is None
    assert reading.relative_humidity_pct is None
    assert reading.provider == "fixture"
    assert reading.is_fixture is True


def test_seasonal_uses_local_month_and_hour() -> None:
    # 2024-12-31T23:30Z is 2025-01-01 08:30 in Seoul.
    at = datetime(2024, 12, 31, 23, 30, tzinfo=UTC)
    reading = _provider().get_weather(at, 10.0, 10.0)
    assert reading.actual_temperature_c == seasonal_temp(1, 8)
    assert reading.observed_at == datetime(2025, 1, 1, 8, 0, tzinfo=SEOUL)


def test_seasonal_temp_shape() -> None:
    assert seasonal_temp(8, 15) > seasonal_temp(8, 3)
    assert seasonal_temp(1, 15) < seasonal_temp(7, 15)
    assert seasonal_temp(7, 15) == round(25.3 + 4.0, 1)
    with pytest.raises(ValueError):
        seasonal_temp(13, 0)
    with pytest.raises(ValueError):
        seasonal_temp(1, 24)


def test_missing_or_broken_file_falls_back_to_seasonal(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    at = datetime(2025, 5, 1, 9, 0, tzinfo=SEOUL)
    for path in (tmp_path / "missing.json", broken):
        provider = _provider(path)
        assert provider.entry_count == 0
        assert provider.get_weather(at, 37.5, 127.0).actual_temperature_c == seasonal_temp(5, 9)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        _provider().get_weather(datetime(2025, 1, 1, 9, 0), 37.5, 127.0)


def _write_recorded_file(tmp_path: Path) -> Path:
    path = tmp_path / "weather.json"
    path.write_text(
        json.dumps(
            {
                "recorded": {
                    "source": "Open-Meteo Historical Weather API (reanalysis)",
                    "endpoint": "https://archive-api.open-meteo.com/v1/archive",
                    "hourly": ["temperature_2m", "precipitation", "relative_humidity_2m"],
                    "timezone": "Asia/Seoul",
                    "recorded_on": "2026-11-14",
                    "note": "test",
                },
                "entries": [
                    {
                        "file": "2025-01-08_081500_KST.jpg",
                        "captured_at": "2025-01-08T08:15:00+09:00",
                        "latitude": 37.556,
                        "longitude": 126.924,
                        "observed_at": "2025-01-08T08:00:00+09:00",
                        "actual_temperature_c": -6.1,
                        "apparent_temperature_c": None,
                        "relative_humidity_pct": 62.0,
                        "wind_speed_kmh": None,
                        "precipitation_mm": 0.0,
                        "weather_code": None,
                    },
                    {
                        "file": "late.png",
                        "captured_at": "2025-03-01T23:40:00+09:00",
                        "latitude": 37.5665,
                        "longitude": 126.978,
                        "observed_at": "2025-03-01T23:00:00+09:00",
                        "actual_temperature_c": 2.3,
                        "apparent_temperature_c": None,
                        "relative_humidity_pct": None,
                        "wind_speed_kmh": None,
                        "precipitation_mm": None,
                        "weather_code": None,
                    },
                ],
                "today_default": {
                    "actual_temperature_c": 0.0,
                    "apparent_temperature_c": None,
                    "relative_humidity_pct": None,
                    "wind_speed_kmh": None,
                    "precipitation_mm": None,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_recorded_format_entry_lookup_keeps_values_and_nulls(tmp_path: Path) -> None:
    provider = _provider(_write_recorded_file(tmp_path))
    assert provider.entry_count == 2
    assert provider.recorded is not None
    assert provider.recorded["timezone"] == "Asia/Seoul"

    reading = provider.get_weather(datetime(2025, 1, 7, 23, 15, tzinfo=UTC), 37.556, 126.924)
    assert reading.actual_temperature_c == -6.1
    assert reading.relative_humidity_pct == 62.0
    assert reading.precipitation_mm == 0.0
    assert reading.apparent_temperature_c is None
    assert reading.wind_speed_kmh is None
    assert reading.weather_code is None
    assert reading.observed_at == datetime(2025, 1, 8, 8, 0, tzinfo=SEOUL)
    assert reading.observed_at.utcoffset() == timedelta(hours=9)
    assert reading.provider == "fixture"
    assert reading.is_fixture is True


def test_recorded_observed_at_is_returned_not_recomputed(tmp_path: Path) -> None:
    provider = _provider(_write_recorded_file(tmp_path))
    reading = provider.get_weather(datetime(2025, 3, 1, 23, 40, tzinfo=SEOUL), 37.5665, 126.978)
    assert reading.actual_temperature_c == 2.3
    assert reading.relative_humidity_pct is None
    assert reading.precipitation_mm is None
    assert reading.observed_at == datetime(2025, 3, 1, 23, 0, tzinfo=SEOUL)


def test_recorded_format_unknown_input_still_uses_seasonal(tmp_path: Path) -> None:
    provider = _provider(_write_recorded_file(tmp_path))
    at = datetime(2024, 6, 2, 7, 20, tzinfo=SEOUL)
    reading = provider.get_weather(at, 37.556, 126.924)
    assert reading.actual_temperature_c == seasonal_temp(6, 7)
    assert reading.observed_at == datetime(2024, 6, 2, 7, 0, tzinfo=SEOUL)
    assert reading.provider == "fixture"
    assert reading.is_fixture is True


def test_synthetic_entries_are_counted_and_read_like_recorded_ones(tmp_path: Path) -> None:
    """``synthetic: true`` marks authored values (generated outfits); lookups are unchanged."""
    recorded = {"file": "a.png", "captured_at": "2025-01-10T08:00:00+09:00", "latitude": 37.5665,
                "longitude": 126.978, "observed_at": "2025-01-10T08:00:00+09:00",
                "actual_temperature_c": -3.2, "apparent_temperature_c": None, "relative_humidity_pct": 40.0,
                "wind_speed_kmh": None, "precipitation_mm": 0.0, "weather_code": None}
    authored = {"file": "b.png", "captured_at": "2025-01-10T12:17:00+09:00", "latitude": 37.5665,
                "longitude": 126.978, "actual_temperature_c": -12, "apparent_temperature_c": -17,
                "relative_humidity_pct": 35, "wind_speed_kmh": 15, "precipitation_mm": 0,
                "synthetic": True, "source": "data/demo/generated_outfits.json"}
    path = tmp_path / "weather.json"
    path.write_text(json.dumps({"recorded": {"source": "x"}, "entries": [recorded, authored]}), encoding="utf-8")
    provider = _provider(path)

    assert provider.entry_count == 2 and provider.synthetic_entry_count == 1
    at = datetime.fromisoformat(authored["captured_at"])
    assert provider.is_synthetic(at, 37.5665, 126.978)
    assert not provider.is_synthetic(datetime.fromisoformat(recorded["captured_at"]), 37.5665, 126.978)
    assert not provider.is_synthetic(at + timedelta(days=1), 37.5665, 126.978)  # no entry
    reading = provider.get_weather(at, 37.5665, 126.978)
    assert (reading.actual_temperature_c, reading.apparent_temperature_c, reading.wind_speed_kmh) == (-12, -17, 15)
    assert reading.is_fixture is True and reading.provider == "fixture"
    assert reading.observed_at == at.replace(minute=0)  # no recorded hour: the capture hour


def test_real_fixture_marks_exactly_the_generated_outfits_synthetic() -> None:
    catalog = json.loads((DEFAULT_WEATHER_FIXTURE_PATH.parents[1] / "generated_outfits.json").read_text("utf-8"))
    generated = {entry["file"] for entry in catalog["photos"]}
    entries = _recorded_entries()
    assert {file for file, entry in entries.items() if entry.get("synthetic") is True} == generated
    assert _provider().synthetic_entry_count == len(generated)
