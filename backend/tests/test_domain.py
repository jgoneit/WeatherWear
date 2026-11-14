"""Unit tests for domain value objects (Req 3.1, 3.6, 4.3, 7.9, 15.8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain import (
    UNKNOWN_TAGS,
    ClothingTags,
    RecommendationResult,
    SidecarMetadata,
    WeatherReading,
)

VALID_TAGS = {
    "upper_type": "knit",
    "outer_type": "coat",
    "lower_type": "slacks",
    "colors": ["navy", "cream"],
    "layers_visible": 2,
    "confidence": 0.8,
}

KST = timezone(timedelta(hours=9))


def test_valid_clothing_tags() -> None:
    tags = ClothingTags(**VALID_TAGS)
    assert tags.colors == ["navy", "cream"]


@pytest.mark.parametrize(
    "override",
    [
        {"upper_type": ""},
        {"outer_type": "x" * 51},
        {"colors": ["a"] * 6},
        {"colors": [""]},
        {"colors": ["c" * 31]},
        {"layers_visible": -1},
        {"layers_visible": 6},
        {"confidence": -0.01},
        {"confidence": 1.01},
        {"confidence": float("nan")},
        {"extra_field": "x"},
    ],
)
def test_clothing_tags_rejects_out_of_bounds(override: dict) -> None:
    with pytest.raises(ValidationError):
        ClothingTags(**{**VALID_TAGS, **override})


def test_clothing_tags_requires_all_fields() -> None:
    data = dict(VALID_TAGS)
    del data["lower_type"]
    with pytest.raises(ValidationError):
        ClothingTags(**data)


def test_clothing_tags_boundaries_accepted() -> None:
    ClothingTags(
        upper_type="u" * 50,
        outer_type="none",
        lower_type="l",
        colors=["c" * 30] * 5,
        layers_visible=5,
        confidence=1.0,
    )


def test_unknown_tags_default() -> None:
    assert UNKNOWN_TAGS.upper_type == UNKNOWN_TAGS.outer_type == UNKNOWN_TAGS.lower_type == "unknown"
    assert UNKNOWN_TAGS.colors == []
    assert UNKNOWN_TAGS.layers_visible == 0
    assert UNKNOWN_TAGS.confidence == 0.0


def test_sidecar_requires_offset_and_range() -> None:
    ok = SidecarMetadata(captured_at="2025-01-15T08:30:00+09:00", latitude=37.5, longitude=127.0)
    assert ok.captured_at.utcoffset() == timedelta(hours=9)
    assert ok.thermal_vote is None
    with pytest.raises(ValidationError):
        SidecarMetadata(captured_at="2025-01-15T08:30:00", latitude=37.5, longitude=127.0)
    with pytest.raises(ValidationError):
        SidecarMetadata(captured_at="2025-01-15T08:30:00+09:00", latitude=90.1, longitude=0)
    with pytest.raises(ValidationError):
        SidecarMetadata(captured_at="2025-01-15T08:30:00+09:00", latitude=0, longitude=-180.1)
    with pytest.raises(ValidationError):
        SidecarMetadata(
            captured_at="2025-01-15T08:30:00+09:00", latitude=0, longitude=0, thermal_vote="COLD"
        )


def test_weather_reading_allows_null_optionals_and_checks_humidity() -> None:
    base = dict(
        observed_at=datetime(2025, 1, 15, 8, tzinfo=KST),
        actual_temperature_c=3.5,
        apparent_temperature_c=None,
        relative_humidity_pct=None,
        wind_speed_kmh=None,
        precipitation_mm=None,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )
    assert WeatherReading(**base).relative_humidity_pct is None
    with pytest.raises(ValidationError):
        WeatherReading(**{**base, "relative_humidity_pct": 101})
    with pytest.raises(ValidationError):
        WeatherReading(**{**base, "provider": "scenario"})


def test_recommendation_result_version_and_explanation_length() -> None:
    base = dict(
        outfit_id=1,
        rank=1,
        score=115.0,
        temperature_diff_c=0.0,
        selected_by_fallback=False,
        confidence_level="high",
        reasons=["과거에 적당했음 (+15.0점)"],
        explanation="오늘 9.0°C",
        algorithm_version="rule_based_v1",
    )
    RecommendationResult(**base)
    for override in (
        {"algorithm_version": "v2"},
        {"explanation": ""},
        {"explanation": "가" * 301},
        {"rank": 0},
        {"confidence_level": "none"},
    ):
        with pytest.raises(ValidationError):
            RecommendationResult(**{**base, **override})
