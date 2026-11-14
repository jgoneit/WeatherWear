"""Shared Hypothesis strategies (design.md "Property-based test 설정", task 1.9).

Generators for the domain types in ``app/domain.py`` and for the raw inputs
of the pure functions (sidecar text, Open-Meteo archive payloads, raw env).

Main entry points:

- ``temps()``: temperatures in [-30.0, 45.0] on a 0.05 grid, mixed with fixed
  boundary values.
- ``tolerances()``: ``Decimal`` tolerances in (0, 5.0) on a 0.05 grid, often 3.0.
- ``aware_datetimes()``: aware datetimes with fixed UTC offsets in [-12:00, +14:00].
- ``clothing_tags()``: valid ``ClothingTags`` (``none``/``unknown`` mixed in).
- ``sidecars()`` / ``sidecar_dicts()``: valid ``SidecarMetadata`` and its JSON object.
- ``candidate_sets(today_c, tolerance_c)``: recommendation candidates including
  missing weather, missing temperature, missing votes, equal diffs and equal
  scores, plus temperatures at today ± tol, ± 5.0 and ± 5.05.
- ``recommendation_cases()``: ``(WeatherContext, tolerance, candidates)`` with
  the candidates built around today's temperature.
- ``archive_payloads(day)``: Open-Meteo Historical Weather API responses for one day.
- ``raw_env()``: raw ``dict[str, str]`` env mappings mixing valid, empty,
  invalid and arbitrary values for the backend config variables.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from hypothesis import strategies as st

from app.domain import (
    LOWER_TYPES,
    OUTER_TYPES,
    UPPER_TYPES,
    ClothingTags,
    OutfitWithWeatherAndFeedback,
    SidecarMetadata,
    ThermalVote,
    WeatherContext,
    WeatherReading,
)

GRID = Decimal("0.05")
TEMP_MIN_STEPS = -600  # -30.0 / 0.05
TEMP_MAX_STEPS = 900  # 45.0 / 0.05
FALLBACK_TOLERANCE_C = Decimal("5.0")
DEFAULT_TOLERANCE_C = Decimal("3.0")

# Range ends, zero crossing and the four demo scenarios.
TEMP_BOUNDARY_VALUES: tuple[float, ...] = (-30.0, 45.0, 0.0, -0.05, 0.05, 9.0, -12.0, 30.0, 38.0)

VOTES: tuple[ThermalVote, ...] = ("cold", "comfortable", "hot")

# Offsets in minutes: -12:00 .. +14:00 (Req 2.2, 10.10).
OFFSET_MIN_MINUTES = -12 * 60
OFFSET_MAX_MINUTES = 14 * 60
COMMON_OFFSETS_MINUTES = (0, 540, -300, 330, 345, -570, OFFSET_MIN_MINUTES, OFFSET_MAX_MINUTES)

SEOUL_LAT = 37.5665
SEOUL_LON = 126.9780


def grid_temp(steps: int) -> float:
    """``steps * 0.05`` as the float closest to the exact decimal value."""
    return float(Decimal(steps) * GRID)


def _add(temp_c: float, delta: Decimal) -> float:
    """Exact decimal addition, so values stay on the 0.05 grid without float noise."""
    return float(Decimal(str(temp_c)) + delta)


# ---------------------------------------------------------------- temperatures


def temps() -> st.SearchStrategy[float]:
    """Temperatures in [-30.0, 45.0] on a 0.05 grid, mixed with boundary values."""
    return st.one_of(
        st.integers(TEMP_MIN_STEPS, TEMP_MAX_STEPS).map(grid_temp),
        st.sampled_from(TEMP_BOUNDARY_VALUES),
    )


def tolerances() -> st.SearchStrategy[Decimal]:
    """Default tolerances t with 0 < t < 5.0 on a 0.05 grid; 3.0 is drawn often."""
    return st.one_of(
        st.just(DEFAULT_TOLERANCE_C),
        st.integers(1, 99).map(lambda n: Decimal(n) * GRID),
    )


def boundary_offsets(tolerance_c: Decimal) -> list[Decimal]:
    """Signed differences that sit on or right next to the selection boundaries."""
    magnitudes = {
        Decimal("0"),
        tolerance_c,
        tolerance_c - GRID,
        tolerance_c + GRID,
        FALLBACK_TOLERANCE_C - GRID,
        FALLBACK_TOLERANCE_C,
        FALLBACK_TOLERANCE_C + GRID,
    }
    offsets = set()
    for m in magnitudes:
        if m >= 0:
            offsets.add(m)
            offsets.add(-m)
    return sorted(offsets)


def boundary_temps(today_c: float, tolerance_c: Decimal) -> list[float]:
    """today ± tol, ± (tol ± 0.05), ± 4.95, ± 5.0, ± 5.05 and today itself."""
    return [_add(today_c, d) for d in boundary_offsets(tolerance_c)]


# ---------------------------------------------------------------- datetimes


def fixed_offsets() -> st.SearchStrategy[timezone]:
    minutes = st.one_of(
        st.sampled_from(COMMON_OFFSETS_MINUTES),
        st.integers(OFFSET_MIN_MINUTES, OFFSET_MAX_MINUTES),
    )
    return minutes.map(lambda m: timezone(timedelta(minutes=m)))


def aware_datetimes(
    *,
    min_value: datetime = datetime(2000, 1, 1),
    max_value: datetime = datetime(2035, 12, 31, 23, 59, 59),
    microseconds: bool = True,
) -> st.SearchStrategy[datetime]:
    """Aware datetimes with a fixed UTC offset in [-12:00, +14:00]."""
    strategy = st.datetimes(min_value=min_value, max_value=max_value, timezones=fixed_offsets())
    if not microseconds:
        strategy = strategy.map(lambda dt: dt.replace(microsecond=0))
    return strategy


# ---------------------------------------------------------------- clothing tags

_COLOR_WORDS = ("black", "white", "navy", "cream", "beige", "grey", "sage", "rust")
_TEXT = st.characters(codec="utf-8")


def _color() -> st.SearchStrategy[str]:
    return st.one_of(st.sampled_from(_COLOR_WORDS), st.text(_TEXT, min_size=1, max_size=30))


def clothing_tags() -> st.SearchStrategy[ClothingTags]:
    """Valid ClothingTags drawn only from the canonical garment taxonomy."""
    return st.builds(
        ClothingTags,
        upper_type=st.sampled_from(UPPER_TYPES),
        outer_type=st.sampled_from(OUTER_TYPES),
        lower_type=st.sampled_from(LOWER_TYPES),
        colors=st.lists(_color(), min_size=0, max_size=5),
        layers_visible=st.integers(0, 5),
        confidence=st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False),
    )


# ---------------------------------------------------------------- sidecars


def latitudes() -> st.SearchStrategy[float]:
    return st.one_of(
        st.sampled_from((SEOUL_LAT, -90.0, 90.0, 0.0)),
        st.floats(-90.0, 90.0, allow_nan=False, allow_infinity=False),
    )


def longitudes() -> st.SearchStrategy[float]:
    return st.one_of(
        st.sampled_from((SEOUL_LON, -180.0, 180.0, 0.0)),
        st.floats(-180.0, 180.0, allow_nan=False, allow_infinity=False),
    )


def votes_or_none() -> st.SearchStrategy[ThermalVote | None]:
    return st.one_of(st.none(), st.sampled_from(VOTES))


def sidecars() -> st.SearchStrategy[SidecarMetadata]:
    """Valid SidecarMetadata with any offset, in-range coordinates and an optional vote."""
    return st.builds(
        SidecarMetadata,
        captured_at=aware_datetimes(),
        latitude=latitudes(),
        longitude=longitudes(),
        thermal_vote=votes_or_none(),
    )


def sidecar_to_dict(meta: SidecarMetadata) -> dict[str, Any]:
    """JSON object for a sidecar file. ``thermal_vote`` is omitted when None."""
    data: dict[str, Any] = {
        "captured_at": meta.captured_at.isoformat(),
        "latitude": meta.latitude,
        "longitude": meta.longitude,
    }
    if meta.thermal_vote is not None:
        data["thermal_vote"] = meta.thermal_vote
    return data


def sidecar_dicts() -> st.SearchStrategy[dict[str, Any]]:
    return sidecars().map(sidecar_to_dict)


# ---------------------------------------------------------------- weather / candidates


def _apparent(today_c: float | None) -> st.SearchStrategy[float | None]:
    near = [today_c, _add(today_c, Decimal("-2.5")), _add(today_c, Decimal("1.0"))] if today_c is not None else []
    return st.one_of(st.none(), st.sampled_from([*near, -5.0, 0.0, 10.0]), temps())


def _humidity() -> st.SearchStrategy[float | None]:
    return st.one_of(st.none(), st.sampled_from((40.0, 55.0, 70.0)), st.floats(0.0, 100.0, allow_nan=False))


def _wind() -> st.SearchStrategy[float | None]:
    return st.one_of(st.none(), st.sampled_from((0.0, 5.0, 12.0)), st.floats(0.0, 80.0, allow_nan=False))


def _precip() -> st.SearchStrategy[float | None]:
    return st.one_of(st.none(), st.sampled_from((0.0, 0.5, 2.0)), st.floats(0.0, 50.0, allow_nan=False))


@st.composite
def weather_readings(
    draw: st.DrawFn,
    actual_temperature_c: float | None = None,
    observed_at: datetime | None = None,
    today_apparent_c: float | None = None,
) -> WeatherReading:
    """A valid WeatherReading; optional fields are sometimes None."""
    provider = draw(st.sampled_from(("open_meteo", "fixture")))
    return WeatherReading(
        observed_at=observed_at if observed_at is not None else draw(aware_datetimes()),
        actual_temperature_c=actual_temperature_c if actual_temperature_c is not None else draw(temps()),
        apparent_temperature_c=draw(_apparent(today_apparent_c)),
        relative_humidity_pct=draw(_humidity()),
        wind_speed_kmh=draw(_wind()),
        precipitation_mm=draw(_precip()),
        weather_code=draw(st.one_of(st.none(), st.integers(0, 99))),
        provider=provider,
        is_fixture=provider == "fixture",
    )


def reading_without_temperature(reading: WeatherReading) -> WeatherReading:
    """A WeatherReading whose ``actual_temperature_c`` is None.

    The domain type does not allow this, so it is built with ``model_construct``
    to exercise the defensive "missing temperature" path (Req 6.9).
    """
    return WeatherReading.model_construct(**{**reading.__dict__, "actual_temperature_c": None})


@st.composite
def today_contexts(
    draw: st.DrawFn,
    today_c: float | None = None,
    allow_missing_temperature: bool = False,
) -> WeatherContext:
    """Today's WeatherContext with optional fields sometimes None."""
    if today_c is None:
        if allow_missing_temperature:
            today_c = draw(st.one_of(st.none(), temps()))
        else:
            today_c = draw(temps())
    scenario = draw(st.booleans())
    provider = "scenario" if scenario else draw(st.sampled_from(("open_meteo", "fixture")))
    return WeatherContext(
        actual_temperature_c=today_c,
        apparent_temperature_c=draw(_apparent(today_c)),
        relative_humidity_pct=draw(_humidity()),
        wind_speed_kmh=draw(_wind()),
        precipitation_mm=draw(_precip()),
        observed_at=None if scenario else draw(aware_datetimes()),
        provider=provider,
        is_fixture=provider != "open_meteo",
        scenario_id="chilly-morning" if scenario else None,
        scenario_name="쌀쌀한 아침" if scenario else None,
    )


# Kinds of candidate drawn by candidate_sets(); some kinds appear twice to weight them.
_CANDIDATE_KINDS = (
    "random", "random", "boundary", "boundary", "same_diff", "same_score",
    "no_weather", "no_temperature",
)


@st.composite
def candidate_sets(
    draw: st.DrawFn,
    today_c: float | None = None,
    tolerance_c: Decimal | float | None = None,
    *,
    min_size: int = 0,
    max_size: int = 12,
    allow_missing_temperature: bool = True,
) -> list[OutfitWithWeatherAndFeedback]:
    """Recommendation candidates built around ``today_c``.

    The set deliberately contains Outfits without weather, weather without a
    temperature (only when ``allow_missing_temperature``), candidates without
    votes, pairs with the same rounded diff (same or mirrored temperature),
    pairs with the same score (none at diff e vs comfortable at e + 1.5, or
    cold/hot at e vs none at e + 1.5) and temperatures on the selection
    boundaries. When ``today_c``/``tolerance_c`` are omitted they are drawn,
    so pass the test's own values to get meaningful boundary cases.
    """
    if today_c is None:
        today_c = draw(temps())
    tol = draw(tolerances()) if tolerance_c is None else Decimal(str(tolerance_c))
    size = draw(st.integers(min_size, max_size))
    kinds = [k for k in _CANDIDATE_KINDS if allow_missing_temperature or k != "no_temperature"]

    # Each spec is (past temperature or None, vote, weather mode).
    specs: list[tuple[float | None, ThermalVote | None, str]] = []
    while len(specs) < size:
        kind = draw(st.sampled_from(kinds))
        vote = draw(votes_or_none())
        past_temps = [t for t, _, mode in specs if mode == "ok" and t is not None]
        if kind == "no_weather":
            specs.append((None, vote, "none"))
        elif kind == "no_temperature":
            specs.append((None, vote, "no_temperature"))
        elif kind == "boundary":
            specs.append((draw(st.sampled_from(boundary_temps(today_c, tol))), vote, "ok"))
        elif kind == "same_diff" and past_temps:
            earlier = draw(st.sampled_from(past_temps))
            mirrored = _add(today_c, Decimal(str(today_c)) - Decimal(str(earlier)))
            specs.append((draw(st.sampled_from((earlier, mirrored))), vote, "ok"))
        elif kind == "same_score" and len(specs) <= size - 2:
            diff = Decimal(draw(st.integers(0, 70))) * GRID  # 0.0 .. 3.5
            sign = draw(st.sampled_from((Decimal(1), Decimal(-1))))
            near = _add(today_c, sign * diff)
            far = _add(today_c, sign * (diff + Decimal("1.5")))
            if draw(st.booleans()):
                # none at e: 100 - 10e  ==  comfortable at e + 1.5: 100 - 10e - 15 + 15
                specs += [(near, None, "ok"), (far, "comfortable", "ok")]
            else:
                # cold/hot at e: 100 - 10e - 15  ==  none at e + 1.5
                specs += [(near, draw(st.sampled_from(("cold", "hot"))), "ok"), (far, None, "ok")]
        else:
            specs.append((draw(temps()), vote, "ok"))

    ids = draw(st.lists(st.integers(1, 100_000), min_size=len(specs), max_size=len(specs), unique=True))
    candidates = []
    for outfit_id, (temp, vote, mode) in zip(ids, specs, strict=True):
        captured_at = draw(aware_datetimes())
        weather: WeatherReading | None = None
        if mode != "none":
            weather = draw(
                weather_readings(
                    actual_temperature_c=temp if temp is not None else 0.0,
                    observed_at=captured_at,
                    today_apparent_c=today_c,
                )
            )
            if mode == "no_temperature":
                weather = reading_without_temperature(weather)
        candidates.append(
            OutfitWithWeatherAndFeedback(
                outfit_id=outfit_id,
                captured_at=captured_at,
                tags=draw(clothing_tags()),
                weather=weather,
                effective_vote=vote,
            )
        )
    return list(draw(st.permutations(candidates)))


@st.composite
def recommendation_cases(
    draw: st.DrawFn,
    **candidate_kwargs: Any,
) -> tuple[WeatherContext, Decimal, list[OutfitWithWeatherAndFeedback]]:
    """(today, tolerance, candidates) where candidates are built around today's temperature."""
    today = draw(today_contexts())
    tol = draw(tolerances())
    candidates = draw(candidate_sets(today.actual_temperature_c, tol, **candidate_kwargs))
    return today, tol, candidates


# ---------------------------------------------------------------- Open-Meteo archive

ARCHIVE_HOURLY_VARIABLES = ("temperature_2m", "precipitation", "relative_humidity_2m")


def _hourly_values(
    values: st.SearchStrategy[float | int], allow_missing: bool
) -> st.SearchStrategy[list[Any]]:
    item = st.one_of(st.none(), values) if allow_missing else values
    return st.lists(item, min_size=24, max_size=24)


@st.composite
def archive_payloads(
    draw: st.DrawFn,
    day: date | None = None,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    tz_name: str = "Asia/Seoul",
    utc_offset_seconds: int = 9 * 3600,
    allow_missing: bool = True,
) -> dict[str, Any]:
    """An Open-Meteo Historical Weather API response for one day (24 hourly rows).

    ``hourly.time`` holds local times without an offset ("YYYY-MM-DDTHH:MM"),
    as the API returns them when ``timezone`` is set. Values may be null when
    ``allow_missing`` is true.
    """
    if day is None:
        day = draw(st.dates(min_value=date(2000, 1, 1), max_value=date(2035, 12, 31)))
    temperature = st.integers(-300, 450).map(lambda n: float(Decimal(n) / 10))
    precipitation = st.integers(0, 500).map(lambda n: float(Decimal(n) / 10))
    humidity = st.integers(0, 100)
    return {
        "latitude": latitude if latitude is not None else draw(latitudes()),
        "longitude": longitude if longitude is not None else draw(longitudes()),
        "generationtime_ms": 0.5,
        "utc_offset_seconds": utc_offset_seconds,
        "timezone": tz_name,
        "timezone_abbreviation": "GMT+9" if tz_name == "Asia/Seoul" else tz_name,
        "elevation": 38.0,
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "°C",
            "precipitation": "mm",
            "relative_humidity_2m": "%",
        },
        "hourly": {
            "time": [f"{day.isoformat()}T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": draw(_hourly_values(temperature, allow_missing)),
            "precipitation": draw(_hourly_values(precipitation, allow_missing)),
            "relative_humidity_2m": draw(_hourly_values(humidity, allow_missing)),
        },
    }


# ---------------------------------------------------------------- raw env

ENV_KEYS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "VISION_PROVIDER",
    "GEMINI_MODEL",
    "WEATHER_PROVIDER",
    "DATABASE_URL",
    "PHOTO_DIR",
    "APP_TIMEZONE",
    "TEMPERATURE_TOLERANCE_C",
    "DEFAULT_LATITUDE",
    "DEFAULT_LONGITUDE",
    "CORS_ORIGINS",
)
FRONTEND_ENV_KEY = "VITE_API_BASE_URL"  # frontend only, ignored by the backend

_API_KEY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def api_keys(min_size: int = 16, max_size: int = 64) -> st.SearchStrategy[str]:
    """Fake Gemini API keys (never a real key)."""
    return st.text(st.sampled_from(_API_KEY_ALPHABET), min_size=min_size, max_size=max_size)


def _float_text(lo: float, hi: float) -> st.SearchStrategy[str]:
    return st.floats(lo, hi, allow_nan=False, allow_infinity=False).map(repr)


def _origins() -> st.SearchStrategy[str]:
    origin = st.sampled_from(
        ("http://localhost:5173", "http://127.0.0.1:5173", "https://example.com", "http://localhost:4173")
    )
    spaces = st.sampled_from(("", " ", "  ", "\t"))

    @st.composite
    def joined(draw: st.DrawFn) -> str:
        items = draw(st.lists(origin, min_size=1, max_size=4))
        return ",".join(f"{draw(spaces)}{o}{draw(spaces)}" for o in items)

    return joined()


_NON_NUMBERS = ("abc", "nan", "NaN", "inf", "-inf", "Infinity", "1,5", "3°C", "--1")

_VALID_ENV: dict[str, st.SearchStrategy[str]] = {
    "GEMINI_API_KEY": api_keys(),
    "VISION_PROVIDER": st.sampled_from(("gemini", "fixture")),
    "GEMINI_MODEL": st.sampled_from(("gemini-4-hydrogen", "gemini-4-argon", "m-1")),
    "WEATHER_PROVIDER": st.sampled_from(("open_meteo", "fixture")),
    "DATABASE_URL": st.sampled_from(("sqlite:///./data/weatherwear.db", "sqlite:///:memory:")),
    "PHOTO_DIR": st.sampled_from(("./data/demo/photos", "/tmp/weatherwear-photos")),
    "APP_TIMEZONE": st.sampled_from(("Asia/Seoul", "UTC", "America/New_York", "Europe/Berlin")),
    "TEMPERATURE_TOLERANCE_C": st.one_of(
        st.sampled_from(("3", "3.0", "0.5", "4.99", "0.01")), tolerances().map(str)
    ),
    "DEFAULT_LATITUDE": st.one_of(st.sampled_from(("37.5665", "-90", "90", "0")), _float_text(-90.0, 90.0)),
    "DEFAULT_LONGITUDE": st.one_of(st.sampled_from(("126.9780", "-180", "180", "0")), _float_text(-180.0, 180.0)),
    "CORS_ORIGINS": _origins(),
}

_INVALID_ENV: dict[str, st.SearchStrategy[str]] = {
    "VISION_PROVIDER": st.sampled_from(("Gemini", "FIXTURE", "openai", "none", "gemini,fixture")),
    "WEATHER_PROVIDER": st.sampled_from(("open-meteo", "OpenMeteo", "openmeteo", "kma", "FIXTURE")),
    "APP_TIMEZONE": st.sampled_from(("Mars/Olympus", "Asia/Seol", "+09:00", "../etc/passwd", "KST9")),
    "TEMPERATURE_TOLERANCE_C": st.one_of(
        st.sampled_from(("0", "0.0", "5", "5.0", "-1", "1e400", "-0.0", *_NON_NUMBERS)),
        _float_text(5.0, 1e6),
        _float_text(-1e6, 0.0),
    ),
    "DEFAULT_LATITUDE": st.one_of(
        st.sampled_from(("90.1", "-90.01", "1e400", *_NON_NUMBERS)), _float_text(90.0001, 1e6)
    ),
    "DEFAULT_LONGITUDE": st.one_of(
        st.sampled_from(("180.1", "-180.01", "1e400", *_NON_NUMBERS)), _float_text(-1e6, -180.0001)
    ),
    "CORS_ORIGINS": st.sampled_from((",", " , ", ",,,")),
}

_ENV_CHOICES = ("absent", "empty", "valid", "valid", "invalid", "text")


@st.composite
def raw_env(draw: st.DrawFn, *, include_frontend: bool = True) -> dict[str, str]:
    """Raw env mapping for ``resolve_config``.

    Each backend variable is independently absent, empty/whitespace, valid,
    invalid (out of range, non-numeric, NaN/inf, provider outside the allowed
    list) or arbitrary text. Variables without an invalid form get arbitrary
    text instead.
    """
    env: dict[str, str] = {}
    for key in ENV_KEYS:
        choice = draw(st.sampled_from(_ENV_CHOICES))
        if choice == "absent":
            continue
        if choice == "empty":
            env[key] = draw(st.sampled_from(("", " ", "\t", "  \n")))
        elif choice == "valid":
            env[key] = draw(_VALID_ENV[key])
        elif choice == "invalid" and key in _INVALID_ENV:
            env[key] = draw(_INVALID_ENV[key])
        else:
            env[key] = draw(st.text(_TEXT, max_size=20))
    if include_frontend and draw(st.booleans()):
        env[FRONTEND_ENV_KEY] = draw(st.sampled_from(("http://localhost:8000", "")))
    return env
