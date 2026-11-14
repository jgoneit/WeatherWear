"""Smoke tests for the shared Hypothesis strategies (tests/strategies.py, task 1.9).

These only check that the generators produce well-formed values and that the
special cases the property tests rely on are reachable.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from hypothesis import given, settings

from app.domain import ClothingTags, SidecarMetadata, WeatherContext
from tests import strategies as s

SMOKE = settings(max_examples=30, deadline=None)


def _diff(past: float, today: float) -> Decimal:
    return abs(Decimal(str(past)) - Decimal(str(today))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _score(candidate, today: float) -> Decimal:
    adj = {"comfortable": Decimal(15), "cold": Decimal(-15), "hot": Decimal(-15), None: Decimal(0)}
    return 100 - 10 * _diff(candidate.weather.actual_temperature_c, today) + adj[candidate.effective_vote]


def _valid(cands):
    return [c for c in cands if c.weather is not None and c.weather.actual_temperature_c is not None]


@SMOKE
@given(t=s.temps())
def test_temps_on_grid_and_in_range(t: float) -> None:
    assert -30.0 <= t <= 45.0
    assert (Decimal(str(t)) / s.GRID) % 1 == 0


@SMOKE
@given(tol=s.tolerances())
def test_tolerances_in_open_range(tol: Decimal) -> None:
    assert Decimal(0) < tol < Decimal("5.0")


@SMOKE
@given(dt=s.aware_datetimes())
def test_aware_datetimes_offset_range(dt) -> None:
    offset = dt.utcoffset()
    assert offset is not None
    assert timedelta(hours=-12) <= offset <= timedelta(hours=14)


@SMOKE
@given(tags=s.clothing_tags(), meta=s.sidecars())
def test_tags_and_sidecars_revalidate(tags: ClothingTags, meta: SidecarMetadata) -> None:
    assert ClothingTags.model_validate(tags.model_dump()) == tags
    assert SidecarMetadata.model_validate(s.sidecar_to_dict(meta)) == meta


@SMOKE
@given(case=s.recommendation_cases())
def test_recommendation_cases_well_formed(case) -> None:
    today, tol, cands = case
    assert isinstance(today, WeatherContext) and today.actual_temperature_c is not None
    assert Decimal(0) < tol < Decimal("5.0")
    ids = [c.outfit_id for c in cands]
    assert len(ids) == len(set(ids))


def test_candidate_sets_reach_special_cases() -> None:
    today = 9.0
    boundaries = {12.0, 6.0, 14.0, 4.0, 14.05, 3.95}  # today ± 3.0, ± 5.0, ± 5.05
    seen: set[str] = set()
    seen_boundaries: set[float] = set()

    @settings(max_examples=200, deadline=None, database=None)
    @given(cs=s.candidate_sets(today_c=today, tolerance_c=Decimal("3.0")))
    def collect(cs) -> None:
        valid = _valid(cs)
        if any(c.weather is None for c in cs):
            seen.add("no_weather")
        if any(c.weather is not None and c.weather.actual_temperature_c is None for c in cs):
            seen.add("no_temperature")
        if any(c.effective_vote is None for c in valid):
            seen.add("no_vote")
        if len(valid) > len({_diff(c.weather.actual_temperature_c, today) for c in valid}):
            seen.add("same_diff")
        # Equal scores with different diffs (vote bonus/penalty offsets the distance).
        if any(
            _score(a, today) == _score(b, today)
            and _diff(a.weather.actual_temperature_c, today) != _diff(b.weather.actual_temperature_c, today)
            for a in valid
            for b in valid
        ):
            seen.add("same_score")
        seen_boundaries.update(c.weather.actual_temperature_c for c in valid if c.weather.actual_temperature_c in boundaries)

    collect()
    assert seen == {"no_weather", "no_temperature", "no_vote", "same_diff", "same_score"}
    assert seen_boundaries == boundaries


@SMOKE
@given(payload=s.archive_payloads())
def test_archive_payload_shape(payload) -> None:
    hourly = payload["hourly"]
    assert len(hourly["time"]) == 24
    for name in s.ARCHIVE_HOURLY_VARIABLES:
        assert len(hourly[name]) == 24
    assert hourly["time"][0].endswith("T00:00") and hourly["time"][23].endswith("T23:00")


@SMOKE
@given(env=s.raw_env())
def test_raw_env_shape(env: dict[str, str]) -> None:
    assert set(env) <= {*s.ENV_KEYS, s.FRONTEND_ENV_KEY}
    assert all(isinstance(v, str) for v in env.values())


@SMOKE
@given(key=s.api_keys())
def test_api_keys_have_min_length(key: str) -> None:
    assert 16 <= len(key) <= 64
