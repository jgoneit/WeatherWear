"""Open-Meteo Forecast API client (design.md §12, Req 10.1, 10.6, 7.1).

Two kinds of request, both ``GET https://api.open-meteo.com/v1/forecast`` with
no API key:

- ``current``: today's current weather for the daily brief and the Live home
  screen (``CurrentWeatherService``, Req 10.1).
- ``hourly`` + ``past_days``: hourly values of the last few days for photos
  taken inside the Recent_Weather_Window (flow A's ``RecentWeatherProvider``,
  Req 7.1). The ``hourly`` block has the same ``time[]`` + variable-array shape
  as the Archive API, so flow A maps it with Phase 1 ``map_archive_response``.

Parameter names, ranges and default units (design.md "근거와 가정"):
``latitude``, ``longitude``, ``current`` / ``hourly`` (comma-separated variable
names), ``timezone`` (IANA name, so ``time`` values are local wall-clock times
without an offset), ``past_days`` (0..92) and ``forecast_days`` (0..16). The
default units are °C, %, mm and km/h; ``current_units`` / ``hourly_units``
name the unit of each variable and ``normalize_value()`` converts anything
else. These could not be re-checked against the official documentation while
implementing (no network access outside package installs); they match the
design's assumption table, and the constants below are the single place to
change if the documentation differs.

``ForecastClient`` raises ``ForecastError`` for every upstream problem
(timeout, network error, non-2xx status, invalid JSON, a JSON value that is
not an object). Callers decide the fallback. Error messages and logs never
contain the request URL, because its query string carries coordinates
(Req 9.4); for the same reason a filter on the ``httpx`` logger drops the
query string of every Open-Meteo request URL (Forecast and the Phase 1
Archive API, whose coordinates can be an estimated capture location, i.e.
the last browser location) from httpx's own INFO line. The app always imports
this module (tasks 7.1 builds the brief runtime), so the filter is installed
for the whole process.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.domain import WeatherContext
from app.providers.weather.normalize import NormalizeTarget, normalize_value

logger = logging.getLogger(__name__)

FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
CURRENT_VARIABLES: tuple[str, ...] = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "weather_code",
)
# Same three variables as the Phase 1 Archive request (open_meteo.HOURLY_VARIABLES).
HOURLY_VARIABLES: tuple[str, ...] = ("temperature_2m", "precipitation", "relative_humidity_2m")

PAST_DAYS_MAX = 92
FORECAST_DAYS_MAX = 16
REQUEST_TIMEOUT_S = 10.0

# Units assumed when ``current_units`` has no entry for a variable (metric defaults).
_DEFAULT_UNITS: dict[NormalizeTarget, str] = {"C": "°C", "pct": "%", "kmh": "km/h", "mm": "mm"}

# WeatherContext field <- (current variable, unit target). weather_code is
# requested (Req 10.1) but WeatherContext has no field for it.
_CURRENT_FIELDS: tuple[tuple[str, str, NormalizeTarget], ...] = (
    ("apparent_temperature_c", "apparent_temperature", "C"),
    ("relative_humidity_pct", "relative_humidity_2m", "pct"),
    ("precipitation_mm", "precipitation", "mm"),
    ("wind_speed_kmh", "wind_speed_10m", "kmh"),
)


class ForecastError(Exception):
    """The Forecast API could not be used. The message never contains coordinates."""


def _coordinate(value: float) -> str:
    return str(float(value))


def build_current_params(lat: float, lon: float, tz: ZoneInfo) -> dict[str, str]:
    """Query parameters of one ``current`` request (Req 10.1)."""
    return {
        "latitude": _coordinate(lat),
        "longitude": _coordinate(lon),
        "current": ",".join(CURRENT_VARIABLES),
        "timezone": tz.key,
    }


def build_hourly_params(
    lat: float, lon: float, tz: ZoneInfo, past_days: int, forecast_days: int
) -> dict[str, str]:
    """Query parameters of one ``hourly`` request with ``past_days`` (Req 7.1, 7.6).

    Raises ``ValueError`` when ``past_days`` is outside 0..92 or
    ``forecast_days`` outside 0..16.
    """
    if isinstance(past_days, bool) or not isinstance(past_days, int) or not 0 <= past_days <= PAST_DAYS_MAX:
        raise ValueError(f"past_days must be an integer in 0..{PAST_DAYS_MAX}")
    if (
        isinstance(forecast_days, bool)
        or not isinstance(forecast_days, int)
        or not 0 <= forecast_days <= FORECAST_DAYS_MAX
    ):
        raise ValueError(f"forecast_days must be an integer in 0..{FORECAST_DAYS_MAX}")
    return {
        "latitude": _coordinate(lat),
        "longitude": _coordinate(lon),
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": tz.key,
        "past_days": str(past_days),
        "forecast_days": str(forecast_days),
    }


def _local_time(text: Any, tz: ZoneInfo) -> datetime | None:
    """``current.time`` is a local wall-clock time without an offset (``timezone`` was
    sent). A value that carries an offset is converted to ``tz``."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _unit(units: Mapping[str, Any], name: str, target: NormalizeTarget) -> str:
    unit = units.get(name)
    return unit if isinstance(unit, str) else _DEFAULT_UNITS[target]


def map_current_response(payload: Any, tz: ZoneInfo) -> WeatherContext | None:
    """Map a Forecast ``current`` response to today's WeatherContext (Req 10.1, 10.6).

    ``current.temperature_2m`` → actual_temperature_c, ``relative_humidity_2m`` →
    relative_humidity_pct, ``apparent_temperature`` → apparent_temperature_c,
    ``precipitation`` → precipitation_mm, ``wind_speed_10m`` → wind_speed_kmh,
    each through Phase 1 ``normalize_value()`` with the unit from
    ``current_units``. A missing, null or unusable variable becomes None.
    ``observed_at`` is ``current.time`` in ``tz`` (None when absent or unparsable).

    Returns None when the payload is malformed or has no usable
    ``temperature_2m``; the caller then falls back to fixture weather (Req 10.2).
    Never raises.
    """
    if not isinstance(payload, Mapping):
        return None
    current = payload.get("current")
    if not isinstance(current, Mapping):
        return None
    units = payload.get("current_units")
    if not isinstance(units, Mapping):
        units = {}

    actual = normalize_value(
        current.get("temperature_2m"), _unit(units, "temperature_2m", "C"), "C"
    )
    if actual is None:
        return None

    values: dict[str, float | None] = {
        field: normalize_value(current.get(name), _unit(units, name, target), target)
        for field, name, target in _CURRENT_FIELDS
    }
    return WeatherContext(
        actual_temperature_c=actual,
        apparent_temperature_c=values["apparent_temperature_c"],
        relative_humidity_pct=values["relative_humidity_pct"],
        wind_speed_kmh=values["wind_speed_kmh"],
        precipitation_mm=values["precipitation_mm"],
        observed_at=_local_time(current.get("time"), tz),
        provider="open_meteo",
        is_fixture=False,
        scenario_id=None,
        scenario_name=None,
    )


class ForecastClient:
    """Thin HTTP client for the Forecast API. One request per call, 10 s timeout."""

    def __init__(self, http: httpx.Client | None = None, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self._owns_client = http is None
        self._http = http or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def get_current(self, lat: float, lon: float, tz: ZoneInfo) -> dict:
        """Raw ``current`` response. Raises ``ForecastError`` on any failure."""
        return self._get(build_current_params(lat, lon, tz))

    def get_hourly(
        self, lat: float, lon: float, tz: ZoneInfo, past_days: int, forecast_days: int
    ) -> dict:
        """Raw ``hourly`` response. Raises ``ForecastError`` on any failure
        (``ValueError`` for out-of-range day counts, before any request)."""
        return self._get(build_hourly_params(lat, lon, tz, past_days, forecast_days))

    def _get(self, params: dict[str, str]) -> dict:
        try:
            response = self._http.get(FORECAST_ENDPOINT, params=params, timeout=self._timeout_s)
        except httpx.TimeoutException:
            raise ForecastError("timeout") from None
        except httpx.HTTPError as exc:
            raise ForecastError(f"network error: {type(exc).__name__}") from None
        if not response.is_success:
            raise ForecastError(f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise ForecastError("invalid JSON") from None
        if not isinstance(payload, dict):
            raise ForecastError("JSON is not an object")
        return payload


OPEN_METEO_HOST_SUFFIX = "open-meteo.com"


def _is_open_meteo(value: object) -> bool:
    if not isinstance(value, httpx.URL):
        return False
    host = value.host or ""
    return host == OPEN_METEO_HOST_SUFFIX or host.endswith("." + OPEN_METEO_HOST_SUFFIX)


class _ForecastQueryFilter(logging.Filter):
    """Drop the query string (coordinates) of Open-Meteo URLs from httpx's request log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and any(_is_open_meteo(a) for a in args):
            record.args = tuple(
                a.copy_with(query=None) if isinstance(a, httpx.URL) else a for a in args
            )
        return True


_HTTPX_LOGGER = logging.getLogger("httpx")
if not any(isinstance(f, _ForecastQueryFilter) for f in _HTTPX_LOGGER.filters):
    _HTTPX_LOGGER.addFilter(_ForecastQueryFilter())
