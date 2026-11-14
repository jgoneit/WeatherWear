"""Unit tests for domain value objects (Req 3.1, 3.6, 4.3, 7.9, 15.8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.domain import (
    LOWER_TYPES,
    OUTER_TYPES,
    UPPER_TYPES,
    LowerType,
    OuterType,
    UpperType,
    UNKNOWN_ANALYSIS,
    UNKNOWN_TAGS,
    CaptureMetadata,
    ClothingTags,
    VisionAnalysis,
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


def test_taxonomy_constants_match_literal_types() -> None:
    assert UPPER_TYPES == get_args(UpperType)
    assert OUTER_TYPES == get_args(OuterType)
    assert LOWER_TYPES == get_args(LowerType)


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


def test_clothing_tags_accepts_taxonomy_boundaries() -> None:
    ClothingTags(
        upper_type="coveralls",
        outer_type="long_puffer",
        lower_type="parachute_pants",
        colors=["c" * 30] * 5,
        layers_visible=5,
        confidence=1.0,
    )


def test_clothing_tags_rejects_noncanonical_spelling() -> None:
    for field, value in (
        ("upper_type", "t-shirt"),
        ("outer_type", "puffer coat"),
        ("lower_type", "baggy jeans"),
    ):
        with pytest.raises(ValidationError):
            ClothingTags(**{**VALID_TAGS, field: value})


def test_unknown_tags_default() -> None:
    assert UNKNOWN_TAGS.upper_type == UNKNOWN_TAGS.outer_type == UNKNOWN_TAGS.lower_type == "unknown"
    assert UNKNOWN_TAGS.colors == []
    assert UNKNOWN_TAGS.layers_visible == 0
    assert UNKNOWN_TAGS.confidence == 0.0


def test_vision_analysis_enforces_photo_type_contract() -> None:
    non_outfit = VisionAnalysis(
        photo_type="non_outfit",
        photo_type_confidence=0.9,
        reason="No person is visible.",
        tags=UNKNOWN_TAGS,
    )
    assert non_outfit.photo_type == "non_outfit"
    assert UNKNOWN_ANALYSIS.photo_type == "uncertain"

    with pytest.raises(ValidationError):
        VisionAnalysis(
            photo_type="non_outfit",
            photo_type_confidence=0.9,
            reason="Contradictory garment output.",
            tags=ClothingTags(**VALID_TAGS),
        )
    with pytest.raises(ValidationError):
        VisionAnalysis(
            photo_type="other",
            photo_type_confidence=0.9,
            reason="Invalid type.",
            tags=UNKNOWN_TAGS,
        )


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


def test_sidecar_location_fields_are_optional() -> None:  # Req 2.16
    vote_only = SidecarMetadata(thermal_vote="comfortable")
    assert vote_only.captured_at is None and vote_only.latitude is None and vote_only.longitude is None


def test_capture_metadata_requires_offset_range_and_origin() -> None:  # Req 2.8, 2.15
    ok = CaptureMetadata(
        captured_at="2025-01-08T08:15:00+09:00", latitude=37.556, longitude=126.924,
        synthetic=True, origin="user_comment",
    )
    assert ok.captured_at.utcoffset() == timedelta(hours=9) and ok.synthetic is True
    base = dict(captured_at="2025-01-08T08:15:00+09:00", latitude=0, longitude=0,
                synthetic=False, origin="exif_standard")
    for bad in ({"captured_at": "2025-01-08T08:15:00"}, {"latitude": 90.5},
                {"longitude": 181}, {"origin": "gps"}):
        with pytest.raises(ValidationError):
            CaptureMetadata(**{**base, **bad})


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
