"""Unit tests for scripts/record_fixture_weather.py (task 19.19).

All Archive traffic is mocked with respx; the conftest socket guard blocks
real connections. The script's ``main`` runs on temporary folders only.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import Image

from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.open_meteo import ARCHIVE_URL, HOURLY_VARIABLES

REPO_ROOT = Path(__file__).resolve().parents[2]
SEOUL = ZoneInfo("Asia/Seoul")
KST = timezone(timedelta(hours=9))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rec = _load("record_fixture_weather")


def _photo(path: Path, day: str, clock: str, lat: float, lon: float) -> None:
    exif = Image.Exif()
    comment = {"date": day, "time": clock, "utc_offset": "+09:00", "latitude": lat,
               "longitude": lon, "synthetic": True, "outfit": "ignored"}
    exif.get_ifd(0x8769)[0x9286] = b"ASCII\x00\x00\x00" + json.dumps(comment).encode()
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG", exif=exif.tobytes())
    path.write_bytes(buffer.getvalue())


def _payload(day: str, temps: list[float | None]) -> dict:
    return {
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "precipitation": "mm",
                         "relative_humidity_2m": "%"},
        "hourly": {
            "time": [f"{day}T{h:02d}:00" for h in range(24)],
            "temperature_2m": temps,
            "precipitation": [0.0] * 24,
            "relative_humidity_2m": [60] * 24,
        },
    }


def _temps(base: float) -> list[float]:
    return [round(base + h * 0.1, 1) for h in range(24)]


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    # a and b share a date and location (one request); c is another day.
    _photo(folder / "a.png", "2025-01-08", "08:15:00", 37.556, 126.924)
    _photo(folder / "b.png", "2025-01-08", "14:40:00", 37.556, 126.924)
    _photo(folder / "c.png", "2025-07-30", "20:03:00", 37.5665, 126.978)
    (folder / "a.metadata.json").write_text('{"thermal_vote": "cold"}', encoding="utf-8")
    return folder


def _capture(file: str, at: datetime, lat: float = 37.5, lon: float = 127.0):
    return rec.PhotoCapture(file, at, lat, lon)


def test_group_key_uses_local_date_and_rounded_location():
    # 23:30 UTC on Jan 7 is Jan 8 in Seoul.
    late = _capture("x.png", datetime(2025, 1, 7, 23, 30, tzinfo=timezone.utc), 37.556049, 126.92401)
    same = _capture("y.png", datetime(2025, 1, 8, 20, 0, tzinfo=KST), 37.55605, 126.92399)
    other_day = _capture("z.png", datetime(2025, 1, 9, 0, 10, tzinfo=KST), 37.556, 126.924)

    groups = rec.group_captures([other_day, late, same], SEOUL)

    assert list(groups) == [("2025-01-08", 37.556, 126.924), ("2025-01-09", 37.556, 126.924)]
    assert [c.file for c in groups[("2025-01-08", 37.556, 126.924)]] == ["x.png", "y.png"]
    params = rec.params_for_group(groups[("2025-01-08", 37.556, 126.924)], SEOUL)
    assert params == {"latitude": 37.556, "longitude": 126.924, "start_date": "2025-01-08",
                      "end_date": "2025-01-08", "hourly": HOURLY_VARIABLES, "timezone": "Asia/Seoul"}


def test_entries_for_group_maps_nearest_hour_and_reports_missing_temperature():
    temps = _temps(-6.0)
    temps[14] = None
    group = [_capture("a.png", datetime(2025, 1, 8, 8, 15, tzinfo=KST)),
             _capture("b.png", datetime(2025, 1, 8, 14, 20, tzinfo=KST))]

    entries, failures = rec.entries_for_group(_payload("2025-01-08", temps), group, SEOUL)

    assert failures == [rec.PhotoFailure("b.png", "NO_TEMPERATURE")]
    assert entries == [{
        "file": "a.png", "captured_at": "2025-01-08T08:15:00+09:00", "latitude": 37.5,
        "longitude": 127.0, "observed_at": "2025-01-08T08:00:00+09:00",
        "actual_temperature_c": -5.2, "apparent_temperature_c": None, "relative_humidity_pct": 60.0,
        "wind_speed_kmh": None, "precipitation_mm": 0.0, "weather_code": None,
    }]


@respx.mock
def test_fetch_retries_transient_error_once():
    route = respx.get(ARCHIVE_URL).mock(side_effect=[
        httpx.Response(503), httpx.Response(200, json=_payload("2025-01-08", _temps(0.0)))])
    waits: list[float] = []
    with httpx.Client() as client:
        payload = rec.fetch_archive(client, {"latitude": 1.0}, sleep=waits.append)
    assert route.call_count == 2 and len(waits) == 1
    assert payload["hourly"]["temperature_2m"][0] == 0.0


@respx.mock
def test_fetch_gives_up_after_one_retry_and_does_not_retry_client_errors():
    timeouts = respx.get(ARCHIVE_URL, params={"latitude": "1.0"}).mock(
        side_effect=httpx.ReadTimeout("slow"))
    bad = respx.get(ARCHIVE_URL, params={"latitude": "2.0"}).mock(return_value=httpx.Response(400))
    with httpx.Client() as client:
        with pytest.raises(rec.FetchError, match="TIMEOUT"):
            rec.fetch_archive(client, {"latitude": 1.0}, sleep=lambda _: None)
        with pytest.raises(rec.FetchError, match="HTTP_400"):
            rec.fetch_archive(client, {"latitude": 2.0}, sleep=lambda _: None)
    assert timeouts.call_count == 2 and bad.call_count == 1


@respx.mock
def test_main_records_one_request_per_group_and_fixture_provider_reads_it(photos, tmp_path):
    output = tmp_path / "weather.json"
    output.write_text(json.dumps({"entries": [], "today_default": {"actual_temperature_c": 9.0}}),
                      encoding="utf-8")
    route = respx.get(ARCHIVE_URL).mock(side_effect=lambda request: httpx.Response(
        200, json=_payload(request.url.params["start_date"],
                           _temps(-6.0 if request.url.params["start_date"] == "2025-01-08" else 25.0))))
    pauses: list[float] = []

    with httpx.Client() as client:
        code = rec.main(["--photos", str(photos), "--output", str(output)], client=client,
                        sleep=pauses.append)

    assert code == 0
    assert route.call_count == 2 and pauses == [0.2]
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["recorded"]["endpoint"] == ARCHIVE_URL
    assert document["recorded"]["timezone"] == "Asia/Seoul"
    assert document["today_default"] == {"actual_temperature_c": 9.0}
    assert [e["file"] for e in document["entries"]] == ["a.png", "b.png", "c.png"]

    provider = FixtureWeatherProvider(output, tz=SEOUL)
    assert provider.entry_count == 3
    reading = provider.get_weather(datetime(2025, 1, 8, 14, 40, tzinfo=KST), 37.556, 126.924)
    assert reading.actual_temperature_c == -4.5  # 15:00 is nearest to 14:40
    assert reading.observed_at.isoformat() == "2025-01-08T15:00:00+09:00"
    assert reading.provider == "fixture" and reading.is_fixture is True
    assert reading.apparent_temperature_c is None and reading.wind_speed_kmh is None


@respx.mock
def test_main_fails_without_overwriting_when_a_photo_gets_no_temperature(photos, tmp_path, capsys):
    output = tmp_path / "weather.json"
    output.write_text('{"entries": []}', encoding="utf-8")
    respx.get(ARCHIVE_URL).mock(side_effect=lambda request: httpx.Response(
        200, json=_payload(request.url.params["start_date"], [None] * 24)))

    with httpx.Client() as client:
        code = rec.main(["--photos", str(photos), "--output", str(output)], client=client,
                        sleep=lambda _: None)

    assert code == 1
    assert output.read_text(encoding="utf-8") == '{"entries": []}'
    assert "a.png (NO_TEMPERATURE)" in capsys.readouterr().err


def test_main_fails_before_any_request_when_metadata_is_missing(photos, tmp_path, capsys):
    Image.new("RGB", (8, 8)).save(photos / "d.png")  # no EXIF and no sidecar
    output = tmp_path / "weather.json"

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(ARCHIVE_URL)
        with httpx.Client() as client:
            code = rec.main(["--photos", str(photos), "--output", str(output)], client=client)

    assert code == 1 and route.call_count == 0 and not output.exists()
    assert "d.png (METADATA_NOT_FOUND)" in capsys.readouterr().err


def test_temperature_bands():
    assert rec.temperature_bands([-6.1, -0.1, 0.0, 4.9, 25.0]) == {
        "-10.0~-5.0": 1, "-5.0~0.0": 1, "0.0~5.0": 2, "25.0~30.0": 1}
