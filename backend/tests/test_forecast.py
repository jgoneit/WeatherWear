"""ForecastClient and the Forecast ``current`` mapping (Phase 2 tasks 4.1, Req 10.1, 10.6, 7.1).

All HTTP traffic is mocked with respx; the conftest socket guard blocks real
connections.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.providers.weather.forecast import (
    CURRENT_VARIABLES,
    FORECAST_ENDPOINT,
    HOURLY_VARIABLES,
    ForecastClient,
    ForecastError,
    build_current_params,
    build_hourly_params,
    map_current_response,
)
from app.providers.weather.open_meteo import HOURLY_VARIABLES as ARCHIVE_HOURLY_VARIABLES

SEOUL = ZoneInfo("Asia/Seoul")
LAT, LON = 37.57, 126.98

# Shape of a Forecast ``current`` response for Seoul (fixed, recorded by hand).
SEOUL_CURRENT = {
    "latitude": 37.55,
    "longitude": 127.0,
    "generationtime_ms": 0.05,
    "utc_offset_seconds": 32400,
    "timezone": "Asia/Seoul",
    "timezone_abbreviation": "GMT+9",
    "elevation": 38.0,
    "current_units": {
        "time": "iso8601",
        "interval": "seconds",
        "temperature_2m": "°C",
        "relative_humidity_2m": "%",
        "apparent_temperature": "°C",
        "precipitation": "mm",
        "wind_speed_10m": "km/h",
        "weather_code": "wmo code",
    },
    "current": {
        "time": "2026-11-14T07:30",
        "interval": 900,
        "temperature_2m": 14.5,
        "relative_humidity_2m": 55,
        "apparent_temperature": 12.9,
        "precipitation": 0.0,
        "wind_speed_10m": 7.2,
        "weather_code": 3,
    },
}


@pytest.fixture
def client():
    with httpx.Client(timeout=10.0) as http:
        yield ForecastClient(http=http)


# --- parameters ---------------------------------------------------------------


def test_current_params_match_requirement() -> None:
    params = build_current_params(LAT, LON, SEOUL)
    assert params == {
        "latitude": "37.57",
        "longitude": "126.98",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
        "precipitation,wind_speed_10m,weather_code",
        "timezone": "Asia/Seoul",
    }
    assert params["current"].split(",") == list(CURRENT_VARIABLES)


def test_hourly_params_use_phase1_variables() -> None:
    params = build_hourly_params(LAT, LON, SEOUL, past_days=3, forecast_days=2)
    assert params["hourly"].split(",") == list(HOURLY_VARIABLES)
    assert params["hourly"] == ARCHIVE_HOURLY_VARIABLES
    assert params["past_days"] == "3"
    assert params["forecast_days"] == "2"
    assert params["timezone"] == "Asia/Seoul"
    assert "current" not in params


@pytest.mark.parametrize(
    ("past_days", "forecast_days"), [(-1, 2), (93, 2), (0, -1), (0, 17), (True, 2), (1.0, 2)]
)
def test_hourly_params_reject_out_of_range_days(past_days, forecast_days) -> None:
    with pytest.raises(ValueError):
        build_hourly_params(LAT, LON, SEOUL, past_days, forecast_days)


def test_hourly_params_accept_range_edges() -> None:
    assert build_hourly_params(LAT, LON, SEOUL, 0, 0)["past_days"] == "0"
    assert build_hourly_params(LAT, LON, SEOUL, 92, 16)["forecast_days"] == "16"


# --- map_current_response -------------------------------------------------------


def test_seoul_current_response_mapping() -> None:
    weather = map_current_response(SEOUL_CURRENT, SEOUL)
    assert weather is not None
    assert weather.actual_temperature_c == 14.5
    assert weather.relative_humidity_pct == 55.0
    assert weather.apparent_temperature_c == 12.9
    assert weather.precipitation_mm == 0.0
    assert weather.wind_speed_kmh == 7.2
    assert weather.observed_at == datetime(2026, 11, 14, 7, 30, tzinfo=SEOUL)
    assert weather.provider == "open_meteo"
    assert weather.is_fixture is False
    assert weather.scenario_id is None and weather.scenario_name is None


def test_missing_variables_become_null() -> None:
    payload = {
        "current": {"time": "2026-11-14T07:30", "temperature_2m": 3.0, "precipitation": None},
    }
    weather = map_current_response(payload, SEOUL)
    assert weather is not None
    assert weather.actual_temperature_c == 3.0
    assert weather.relative_humidity_pct is None
    assert weather.apparent_temperature_c is None
    assert weather.precipitation_mm is None
    assert weather.wind_speed_kmh is None


def test_units_are_normalized() -> None:
    payload = {
        "current_units": {
            "temperature_2m": "°F",
            "apparent_temperature": "°F",
            "precipitation": "inch",
            "wind_speed_10m": "m/s",
            "relative_humidity_2m": "%",
        },
        "current": {
            "time": "2026-11-14T07:30",
            "temperature_2m": 50.0,
            "apparent_temperature": 32.0,
            "precipitation": 1.0,
            "wind_speed_10m": 10.0,
            "relative_humidity_2m": 120,  # outside 0..100 -> null
        },
    }
    weather = map_current_response(payload, SEOUL)
    assert weather is not None
    assert weather.actual_temperature_c == pytest.approx(10.0)
    assert weather.apparent_temperature_c == pytest.approx(0.0)
    assert weather.precipitation_mm == pytest.approx(25.4)
    assert weather.wind_speed_kmh == pytest.approx(36.0)
    assert weather.relative_humidity_pct is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        None,
        {"current": None},
        {"current": {"time": "2026-11-14T07:30"}},
        {"current": {"time": "2026-11-14T07:30", "temperature_2m": None}},
        {"current": {"time": "2026-11-14T07:30", "temperature_2m": "14.5"}},
        {"current": {"time": "2026-11-14T07:30", "temperature_2m": True}},
        {"current_units": {"temperature_2m": "K"}, "current": {"temperature_2m": 280.0}},
    ],
)
def test_no_usable_temperature_maps_to_none(payload) -> None:
    assert map_current_response(payload, SEOUL) is None


def test_offset_time_and_bad_time() -> None:
    with_offset = {"current": {"time": "2026-11-13T22:30+00:00", "temperature_2m": 1.0}}
    weather = map_current_response(with_offset, SEOUL)
    assert weather is not None
    assert weather.observed_at == datetime(2026, 11, 14, 7, 30, tzinfo=SEOUL)
    bad_time = {"current": {"time": "not a time", "temperature_2m": 1.0}}
    weather = map_current_response(bad_time, SEOUL)
    assert weather is not None and weather.observed_at is None


# --- ForecastClient ----------------------------------------------------------------


@respx.mock
def test_get_current_sends_one_request(client: ForecastClient) -> None:
    route = respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=SEOUL_CURRENT))
    payload = client.get_current(LAT, LON, SEOUL)
    assert payload == SEOUL_CURRENT
    assert route.call_count == 1
    query = dict(route.calls.last.request.url.params)
    assert query == build_current_params(LAT, LON, SEOUL)
    assert "apikey" not in {k.lower() for k in query}


@respx.mock
def test_get_hourly_sends_past_days(client: ForecastClient) -> None:
    body = {"hourly": {"time": [], "temperature_2m": []}}
    route = respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=body))
    assert client.get_hourly(LAT, LON, SEOUL, past_days=5, forecast_days=2) == body
    query = dict(route.calls.last.request.url.params)
    assert query["past_days"] == "5" and query["forecast_days"] == "2"


@respx.mock
@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
def test_non_2xx_raises_forecast_error(client: ForecastClient, status: int) -> None:
    respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(status, json={"error": True}))
    with pytest.raises(ForecastError, match=f"HTTP {status}"):
        client.get_current(LAT, LON, SEOUL)


@respx.mock
def test_invalid_json_raises_forecast_error(client: ForecastClient) -> None:
    respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, text="<html>oops"))
    with pytest.raises(ForecastError, match="invalid JSON"):
        client.get_current(LAT, LON, SEOUL)


@respx.mock
def test_non_object_json_raises_forecast_error(client: ForecastClient) -> None:
    respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(ForecastError):
        client.get_current(LAT, LON, SEOUL)


@respx.mock
def test_timeout_raises_forecast_error(client: ForecastClient) -> None:
    respx.get(FORECAST_ENDPOINT).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ForecastError, match="timeout"):
        client.get_current(LAT, LON, SEOUL)


@respx.mock
def test_connection_error_raises_forecast_error(client: ForecastClient) -> None:
    respx.get(FORECAST_ENDPOINT).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ForecastError, match="network error"):
        client.get_hourly(LAT, LON, SEOUL, 1, 2)


@respx.mock
def test_errors_and_logs_never_contain_coordinates(
    client: ForecastClient, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(500))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ForecastError) as excinfo:
        client.get_current(LAT, LON, SEOUL)
    assert "37.57" not in str(excinfo.value) and "126.98" not in str(excinfo.value)

    respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=SEOUL_CURRENT))
    client.get_current(LAT, LON, SEOUL)
    text = caplog.text
    assert "api.open-meteo.com/v1/forecast" in text  # httpx still logs the request
    assert "37.57" not in text and "126.98" not in text


def test_default_client_uses_ten_second_timeout() -> None:
    forecast = ForecastClient()
    try:
        assert forecast._http.timeout.read == 10.0  # noqa: SLF001
    finally:
        forecast.close()


def test_httpx_log_filter_strips_query_of_every_open_meteo_host() -> None:
    """Archive requests carry coordinates too (an estimated capture uses the last
    browser location), so the filter covers every Open-Meteo host (tasks 7.1)."""
    import logging

    import httpx

    from app.providers.weather.forecast import _ForecastQueryFilter

    flt = _ForecastQueryFilter()
    for url in (
        "https://api.open-meteo.com/v1/forecast?latitude=35.18&longitude=129.08",
        "https://archive-api.open-meteo.com/v1/archive?latitude=35.18&longitude=129.08",
    ):
        record = logging.LogRecord(
            "httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s %d %s"',
            ("GET", httpx.URL(url), "HTTP/1.1", 200, "OK"), None,
        )
        assert flt.filter(record) is True
        message = record.getMessage()
        assert "35.18" not in message and "129.08" not in message
        assert "open-meteo.com" in message
    other = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "HTTP Request: %s %s",
        ("GET", httpx.URL("https://example.test/x?q=1")), None,
    )
    flt.filter(other)
    assert "q=1" in other.getMessage()
