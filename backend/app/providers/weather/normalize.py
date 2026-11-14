"""Pure weather normalization functions (design.md §5, Req 4.2, 4.8, 4.12).

No I/O here: these functions are shared by ``OpenMeteoWeatherProvider`` and
are the targets of the weather property tests.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.domain import WeatherReading

NormalizeTarget = Literal["C", "pct", "kmh", "mm"]

HUMIDITY_MIN_PCT = 0.0
HUMIDITY_MAX_PCT = 100.0

# Unit spellings accepted per target unit, mapped to a converter into that unit.
# Open-Meteo reports units in ``hourly_units`` as "°C"/"°F", "mm"/"inch", "%",
# and "km/h"/"m/s"/"mp/h"/"kn" depending on the request's unit parameters.
_CONVERTERS: dict[str, dict[str, Any]] = {
    "C": {
        "°c": lambda v: v,
        "c": lambda v: v,
        "celsius": lambda v: v,
        "°f": lambda v: (v - 32.0) * 5.0 / 9.0,
        "f": lambda v: (v - 32.0) * 5.0 / 9.0,
        "fahrenheit": lambda v: (v - 32.0) * 5.0 / 9.0,
    },
    "pct": {
        "%": lambda v: v,
        "pct": lambda v: v,
        "percent": lambda v: v,
    },
    "kmh": {
        "km/h": lambda v: v,
        "kmh": lambda v: v,
        "m/s": lambda v: v * 3.6,
        "ms": lambda v: v * 3.6,
        "mph": lambda v: v * 1.609344,
        "mp/h": lambda v: v * 1.609344,
        "kn": lambda v: v * 1.852,
    },
    "mm": {
        "mm": lambda v: v,
        "inch": lambda v: v * 25.4,
        "in": lambda v: v * 25.4,
        "inches": lambda v: v * 25.4,
    },
}

# Units assumed when the payload has no ``hourly_units`` entry for a variable
# (the request does not change Open-Meteo's metric defaults).
_DEFAULT_UNITS: dict[NormalizeTarget, str] = {"C": "°C", "pct": "%", "kmh": "km/h", "mm": "mm"}


def select_nearest_hour(times: Sequence[datetime], target: datetime) -> int | None:
    """Index of the time closest to ``target`` (Req 4.2).

    All times are compared as UTC instants, so mixed offsets are fine. When two
    times are equally close, the earlier one wins; for identical instants the
    lower index wins. Returns None for an empty sequence. Times and target
    must be timezone-aware.
    """
    best_index: int | None = None
    best_key: tuple[timedelta, datetime] | None = None
    for index, moment in enumerate(times):
        key = (abs(moment - target), moment)
        if best_key is None or key < best_key:
            best_index, best_key = index, key
    return best_index


def normalize_value(
    value: float | None, unit: str, target: NormalizeTarget
) -> float | None:
    """Convert ``value`` from ``unit`` into the target unit (Req 4.8).

    °F→°C, inch→mm, m/s·mph·kn→km/h. Values already in the target unit are
    returned unchanged. Returns None for a None, non-numeric or non-finite
    value, an unknown unit, or a humidity outside [0, 100].
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    converter = _CONVERTERS[target].get(unit.strip().lower()) if isinstance(unit, str) else None
    if converter is None:
        return None
    result = float(converter(float(value)))
    if target == "pct" and not (HUMIDITY_MIN_PCT <= result <= HUMIDITY_MAX_PCT):
        return None
    return result


def _localize(text: Any, tz: ZoneInfo) -> datetime | None:
    """Parse an ``hourly.time`` entry. Open-Meteo returns local wall-clock
    times without an offset when ``timezone`` is set, so naive values are
    interpreted in ``tz``; values that already carry an offset are kept."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _value_at(hourly: Mapping[str, Any], name: str, index: int) -> Any:
    values = hourly.get(name)
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _unit_of(units: Mapping[str, Any], name: str, target: NormalizeTarget) -> str:
    unit = units.get(name)
    return unit if isinstance(unit, str) else _DEFAULT_UNITS[target]


def map_archive_response(
    payload: Mapping[str, Any], target: datetime, tz: ZoneInfo
) -> WeatherReading | None:
    """Map an Open-Meteo Archive API response to a WeatherReading (Req 4.2, 4.8, 4.12).

    ``hourly.time`` entries (local times without an offset) are localized in
    ``tz`` (APP_TIMEZONE, the same zone sent as the request's ``timezone``),
    then the hour nearest to ``target`` is selected. ``temperature_2m`` →
    actual_temperature_c, ``precipitation`` → precipitation_mm,
    ``relative_humidity_2m`` → relative_humidity_pct, all normalized via
    ``hourly_units``. apparent_temperature_c, wind_speed_kmh and weather_code
    are None because the request does not ask for them.

    Returns None when the payload is malformed, has no parseable time, or the
    selected hour has no usable temperature; the caller then falls back to
    fixture weather (Req 4.7).
    """
    if not isinstance(payload, Mapping):
        return None
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        return None
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list):
        return None

    parsed = [(index, _localize(text, tz)) for index, text in enumerate(raw_times)]
    valid = [(index, moment) for index, moment in parsed if moment is not None]
    chosen = select_nearest_hour([moment for _, moment in valid], target)
    if chosen is None:
        return None
    row_index, observed_at = valid[chosen]

    units = payload.get("hourly_units")
    if not isinstance(units, Mapping):
        units = {}

    actual = normalize_value(
        _value_at(hourly, "temperature_2m", row_index),
        _unit_of(units, "temperature_2m", "C"),
        "C",
    )
    if actual is None:
        return None

    return WeatherReading(
        observed_at=observed_at,
        actual_temperature_c=actual,
        apparent_temperature_c=None,
        relative_humidity_pct=normalize_value(
            _value_at(hourly, "relative_humidity_2m", row_index),
            _unit_of(units, "relative_humidity_2m", "pct"),
            "pct",
        ),
        wind_speed_kmh=None,
        precipitation_mm=normalize_value(
            _value_at(hourly, "precipitation", row_index),
            _unit_of(units, "precipitation", "mm"),
            "mm",
        ),
        weather_code=None,
        provider="open_meteo",
        is_fixture=False,
    )
