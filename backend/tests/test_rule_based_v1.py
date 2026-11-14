"""Unit tests for the rule_based_v1 strategy core (task 9.1).

Boundary/insufficient-data/structure tests named in task 9.11 live in
``test_recommendation.py``; numbered properties live in
``test_recommendation_props.py``. This file checks the strategy mechanics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given, settings

from app.domain import (
    UNKNOWN_TAGS,
    ClothingTags,
    OutfitWithWeatherAndFeedback,
    ThermalVote,
    WeatherContext,
    WeatherReading,
)
from app.recommendation.base import MissingTodayTemperature
from app.recommendation.factory import build_strategy
from app.recommendation.rule_based_v1 import (
    COMFORTABLE_BONUS,
    FALLBACK_TOLERANCE_C,
    MAX_RESULTS,
    MISMATCH_PENALTY,
    TEMP_SCORE_BASE,
    TEMP_WEIGHT_PER_C,
    RuleBasedTemperatureStrategy,
    is_non_outfit,
    temperature_diff,
)
from tests.strategies import recommendation_cases

KST = timezone(timedelta(hours=9))
AT = datetime(2025, 11, 20, 8, 30, tzinfo=KST)
COAT = ClothingTags(
    upper_type="knit", outer_type="coat", lower_type="slacks", colors=["navy"], layers_visible=2, confidence=0.9
)


def today(
    temp: float | None = 9.0,
    apparent: float | None = None,
    humidity: float | None = None,
    wind: float | None = None,
    precip: float | None = None,
) -> WeatherContext:
    return WeatherContext(
        actual_temperature_c=temp,
        apparent_temperature_c=apparent,
        relative_humidity_pct=humidity,
        wind_speed_kmh=wind,
        precipitation_mm=precip,
        observed_at=None,
        provider="scenario",
        is_fixture=True,
        scenario_id="s",
        scenario_name="s",
    )


def outfit(
    outfit_id: int,
    temp: float | None,
    vote: ThermalVote | None = None,
    *,
    apparent: float | None = None,
    humidity: float | None = None,
    wind: float | None = None,
    precip: float | None = None,
) -> OutfitWithWeatherAndFeedback:
    weather = None
    if temp is not None:
        weather = WeatherReading(
            observed_at=AT,
            actual_temperature_c=temp,
            apparent_temperature_c=apparent,
            relative_humidity_pct=humidity,
            wind_speed_kmh=wind,
            precipitation_mm=precip,
            weather_code=None,
            provider="fixture",
            is_fixture=True,
        )
    return OutfitWithWeatherAndFeedback(
        outfit_id=outfit_id, captured_at=AT, tags=COAT, weather=weather, effective_vote=vote
    )


def strategy(tol: str = "3.0") -> RuleBasedTemperatureStrategy:
    return RuleBasedTemperatureStrategy(tolerance_c=Decimal(tol))


def test_constants_match_design() -> None:
    assert FALLBACK_TOLERANCE_C == Decimal("5.0")
    assert TEMP_SCORE_BASE == 100 and TEMP_WEIGHT_PER_C == 10
    assert COMFORTABLE_BONUS == 15 and MISMATCH_PENALTY == 15
    assert MAX_RESULTS == 5


def test_diff_rounds_half_up_to_one_decimal() -> None:
    assert temperature_diff(12.05, 9.0) == Decimal("3.1")
    assert temperature_diff(12.04, 9.0) == Decimal("3.0")
    assert temperature_diff(-12.0, -9.0) == Decimal("3.0")


def test_scores_and_vote_order_at_same_diff() -> None:
    cands = [outfit(1, 9.0, "cold"), outfit(2, 9.0, None), outfit(3, 9.0, "comfortable"), outfit(4, 8.0, "hot")]
    results = strategy().recommend(today(9.0), cands)
    assert [(r.outfit_id, r.score) for r in results] == [(3, 115.0), (2, 100.0), (1, 85.0), (4, 75.0)]
    assert [r.rank for r in results] == [1, 2, 3, 4]
    assert all(r.algorithm_version == "rule_based_v1" for r in results)


def test_boundary_included_and_outside_excluded() -> None:
    outcome = strategy().recommend_with_outcome(today(-12.0), [outfit(1, -9.0), outfit(2, -8.5)])
    assert [r.outfit_id for r in outcome.results] == [1]
    assert outcome.results[0].temperature_diff_c == 3.0
    assert outcome.fallback is False and outcome.applied_tolerance_c == Decimal("3.0")


def test_fallback_expands_once_to_5_0() -> None:
    cands = [outfit(1, 25.0), outfit(2, 26.5, "comfortable"), outfit(3, 24.9)]
    outcome = strategy().recommend_with_outcome(today(30.0), cands)
    assert outcome.fallback is True and outcome.applied_tolerance_c == Decimal("5.0")
    assert [r.outfit_id for r in outcome.results] == [2, 1]  # 24.9 (diff 5.1) excluded
    assert all(r.selected_by_fallback for r in outcome.results)
    assert {r.outfit_id: r.confidence_level for r in outcome.results} == {2: "medium", 1: "low"}


def test_insufficient_data_returns_empty_with_fallback() -> None:
    outcome = strategy().recommend_with_outcome(today(38.0), [outfit(1, 26.5), outfit(2, None)])
    assert outcome.results == [] and outcome.fallback is True
    assert outcome.applied_tolerance_c == Decimal("5.0")


def test_missing_weather_or_temperature_excluded() -> None:
    no_temp = outfit(3, 9.0)
    no_temp.weather = WeatherReading.model_construct(**{**no_temp.weather.__dict__, "actual_temperature_c": None})
    results = strategy().recommend(today(9.0), [outfit(1, 9.5), outfit(2, None), no_temp])
    assert [r.outfit_id for r in results] == [1]


def test_is_non_outfit_requires_all_unknown_and_zero_confidence() -> None:
    assert is_non_outfit(UNKNOWN_TAGS)
    assert not is_non_outfit(COAT)
    assert not is_non_outfit(UNKNOWN_TAGS.model_copy(update={"confidence": 0.1}))
    assert not is_non_outfit(UNKNOWN_TAGS.model_copy(update={"outer_type": "none"}))
    assert not is_non_outfit(UNKNOWN_TAGS.model_copy(update={"upper_type": "knit"}))


def test_missing_today_temperature_raises() -> None:
    with pytest.raises(MissingTodayTemperature):
        strategy().recommend(today(None), [outfit(1, 9.0)])


def test_top_five_only_and_confidence_high_medium() -> None:
    cands = [outfit(i, 9.0 + i * 0.1, "comfortable" if i % 2 else None) for i in range(1, 9)]
    results = strategy().recommend(today(9.0), cands)
    assert len(results) == MAX_RESULTS
    assert [r.rank for r in results] == [1, 2, 3, 4, 5]
    for r in results:
        assert r.confidence_level == ("high" if r.outfit_id % 2 else "medium")


def test_tie_break_order_and_missing_values_last() -> None:
    t = today(9.0, apparent=6.0, humidity=50.0, wind=10.0, precip=0.0)
    cands = [
        outfit(10, 9.0, apparent=None),  # missing apparent → after any present value
        outfit(11, 9.0, apparent=8.0),  # apparent diff 2.0
        outfit(12, 9.0, apparent=7.0, humidity=90.0),  # apparent 1.0, humidity 40
        outfit(13, 9.0, apparent=5.0, humidity=55.0),  # apparent 1.0, humidity 5
        outfit(5, 9.0, apparent=None),  # same as 10 on all keys → smaller id first
    ]
    assert [r.outfit_id for r in strategy().recommend(t, cands)] == [13, 12, 11, 5, 10]


def test_explanation_placeholder_is_valid() -> None:
    (r,) = strategy().recommend(today(9.0), [outfit(1, 8.0, "comfortable")])
    assert 1 <= len(r.explanation) <= 300
    assert "9.0°C" in r.explanation and "8.0°C" in r.explanation and "1.0°C" in r.explanation
    assert any("과거에 적당했음" in reason for reason in r.reasons)


def test_build_strategy() -> None:
    s = build_strategy(tolerance_c=Decimal("2.5"))
    assert isinstance(s, RuleBasedTemperatureStrategy)
    assert s.algorithm_version == "rule_based_v1" and s.tolerance_c == Decimal("2.5")
    with pytest.raises(ValueError):
        build_strategy("unknown", tolerance_c=Decimal("3.0"))


@settings(max_examples=50, deadline=None)
@given(case=recommendation_cases())
def test_quick_check_results_are_well_formed(case) -> None:
    today_ctx, tol, cands = case
    outcome = build_strategy(tolerance_c=tol).recommend_with_outcome(today_ctx, cands)
    ids = {c.outfit_id for c in cands}
    assert len(outcome.results) <= MAX_RESULTS
    assert all(r.outfit_id in ids for r in outcome.results)
    assert [r.rank for r in outcome.results] == list(range(1, len(outcome.results) + 1))
    limit = float(outcome.applied_tolerance_c)
    assert all(r.temperature_diff_c <= limit for r in outcome.results)
    reversed_outcome = build_strategy(tolerance_c=tol).recommend_with_outcome(today_ctx, cands[::-1])
    assert reversed_outcome == outcome
