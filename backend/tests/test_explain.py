"""Unit tests for recommendation explanations (task 9.7, design.md §10).

Property 22 lives in ``test_recommendation_props.py`` (task 9.8).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain import (
    UNKNOWN_TAGS,
    ClothingTags,
    OutfitWithWeatherAndFeedback,
    WeatherContext,
    WeatherReading,
)
from app.recommendation.explain import (
    FALLBACK_TEXT,
    MAX_EXPLANATION_LENGTH,
    VOTE_KO,
    build_explanation,
    display_tags,
    format_celsius,
)
from app.recommendation.rule_based_v1 import RuleBasedTemperatureStrategy

KST = timezone(timedelta(hours=9))
AT = datetime(2025, 11, 20, 8, 30, tzinfo=KST)
D = Decimal


def tags(outer: str = "코트", upper: str = "니트", lower: str = "슬랙스") -> ClothingTags:
    return ClothingTags(
        upper_type=upper, outer_type=outer, lower_type=lower,
        colors=["navy"], layers_visible=2, confidence=0.9,
    )


def test_vote_ko_mapping() -> None:
    assert VOTE_KO == {"cold": "추웠음", "comfortable": "적당함", "hot": "더웠음"}


def test_display_tags_order_and_exclusions() -> None:
    assert display_tags(tags()) == ["코트", "니트", "슬랙스"]
    assert display_tags(tags(outer="none")) == ["니트", "슬랙스"]
    assert display_tags(tags(outer="none", lower="unknown")) == ["니트"]
    assert display_tags(UNKNOWN_TAGS) == []  # colors never fill the list
    assert display_tags(tags(), limit=2) == ["코트", "니트"]


def test_format_celsius_one_decimal_no_negative_zero() -> None:
    assert format_celsius(D("9")) == "9.0°C"
    assert format_celsius(D("3.05")) == "3.1°C"
    assert format_celsius(-12.0) == "-12.0°C"
    assert format_celsius(D("-0.04")) == "0.0°C"


def test_comfortable_matches_design_template() -> None:
    text, reasons = build_explanation(
        D("9.0"), D("8.0"), D("1.0"), ["코트", "니트", "슬랙스"], "comfortable", False,
        adjustment=D("15.0"),
    )
    assert text == (
        "오늘 9.0°C, 지난 8.0°C 기록과 기온 차이 1.0°C입니다. "
        "당시 착장: 코트+니트+슬랙스. 체감: 적당함 (과거에 적당했음, +15.0점)."
    )
    assert reasons == ["과거에 적당했음 (+15.0점)"]


def test_cold_and_hot_penalty_reasons() -> None:
    cold, cold_reasons = build_explanation(
        D("9.0"), D("9.0"), D("0.0"), ["니트"], "cold", False, adjustment=D("-15.0")
    )
    assert "체감: 추웠음 (과거에 추웠음, −15.0점)." in cold
    assert cold_reasons == ["과거에 추웠음 (−15.0점)"]
    hot, hot_reasons = build_explanation(
        D("9.0"), D("9.0"), D("0.0"), ["니트"], "hot", False, adjustment=D("-15.0")
    )
    assert "체감: 더웠음 (과거에 더웠음, −15.0점)." in hot
    assert hot_reasons == ["과거에 더웠음 (−15.0점)"]


def test_no_tags_no_vote_and_fallback_sentences() -> None:
    text, reasons = build_explanation(D("30.0"), D("26.0"), D("4.0"), [], None, True)
    assert "당시 착장: 착장 태그 정보 없음." in text
    assert "체감 피드백 기록 없음." in text
    assert "체감:" not in text
    assert text.endswith(FALLBACK_TEXT)
    assert reasons == ["체감 피드백 기록 없음", FALLBACK_TEXT]


def test_fallback_sentence_only_when_fallback() -> None:
    text, reasons = build_explanation(D("9.0"), D("8.0"), D("1.0"), ["니트"], None, False)
    assert "5.0°C로 확장" not in text
    assert FALLBACK_TEXT not in reasons


def test_long_tags_truncated_to_fit_300_chars() -> None:
    long_tags = ["가" * 50, "나" * 50, "다" * 50]
    # Worst case within the domain: three 50-char tags, vote, fallback, wide temperatures.
    text, _ = build_explanation(
        D("-30.0"), D("-25.0"), D("5.0"), long_tags, "comfortable", True, adjustment=D("15.0")
    )
    assert 1 <= len(text) <= MAX_EXPLANATION_LENGTH
    assert "가" * 50 in text  # fits without truncation

    huge = ["가" * 120, "나" * 120, "다" * 120]  # out-of-domain length forces the 20-char cut
    text, _ = build_explanation(D("9.0"), D("8.0"), D("1.0"), huge, "hot", True, adjustment=D("-15.0"))
    assert len(text) <= MAX_EXPLANATION_LENGTH
    assert "가" * 20 + "+" + "나" * 20 + "+" + "다" * 20 in text
    assert "가" * 21 not in text
    assert text.endswith(FALLBACK_TEXT)


def _today(temp: float) -> WeatherContext:
    return WeatherContext(
        actual_temperature_c=temp, apparent_temperature_c=None, relative_humidity_pct=None,
        wind_speed_kmh=None, precipitation_mm=None, observed_at=None,
        provider="scenario", is_fixture=True, scenario_id="s", scenario_name="s",
    )


def _candidate(outfit_id: int, temp: float, vote, clothing: ClothingTags) -> OutfitWithWeatherAndFeedback:
    return OutfitWithWeatherAndFeedback(
        outfit_id=outfit_id, captured_at=AT, tags=clothing, effective_vote=vote,
        weather=WeatherReading(
            observed_at=AT, actual_temperature_c=temp, apparent_temperature_c=None,
            relative_humidity_pct=None, wind_speed_kmh=None, precipitation_mm=None,
            weather_code=None, provider="fixture", is_fixture=True,
        ),
    )


def test_strategy_fills_explanation_and_reasons() -> None:
    results = RuleBasedTemperatureStrategy().recommend(
        _today(9.0),
        [
            _candidate(1, 8.0, "comfortable", tags()),
            # No displayable tags, but not a non-outfit photo (confidence > 0, Req 6.10).
            _candidate(2, 9.0, None, UNKNOWN_TAGS.model_copy(update={"confidence": 0.5})),
        ],
    )
    by_id = {r.outfit_id: r for r in results}
    assert by_id[1].explanation == (
        "오늘 9.0°C, 지난 8.0°C 기록과 기온 차이 1.0°C입니다. "
        "당시 착장: 코트+니트+슬랙스. 체감: 적당함 (과거에 적당했음, +15.0점)."
    )
    assert by_id[1].reasons == ["과거에 적당했음 (+15.0점)"]
    assert "착장 태그 정보 없음" in by_id[2].explanation
    assert "체감 피드백 기록 없음" in by_id[2].explanation


def test_strategy_fallback_candidates_get_fallback_sentence() -> None:
    (r,) = RuleBasedTemperatureStrategy().recommend(
        _today(30.0), [_candidate(1, 26.0, "hot", tags(outer="none"))]
    )
    assert r.selected_by_fallback
    assert "오늘 30.0°C, 지난 26.0°C 기록과 기온 차이 4.0°C입니다." in r.explanation
    assert "당시 착장: 니트+슬랙스." in r.explanation
    assert "체감: 더웠음 (과거에 더웠음, −15.0점)." in r.explanation
    assert r.explanation.endswith(FALLBACK_TEXT)
    assert r.reasons == ["과거에 더웠음 (−15.0점)", FALLBACK_TEXT]
