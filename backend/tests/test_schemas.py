"""Unit tests for API schemas (Req 1.2, 8.1, 9.1, 9.2, 10.4, 10.6, 10.10)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain import UNKNOWN_TAGS
from app.schemas import (
    ErrorResponse,
    FeedbackIn,
    FeedbackOut,
    OutfitOut,
    RecommendationItemOut,
    RecommendationResponse,
    ScanReport,
    WeatherContextOut,
    WeatherSnapshotOut,
)

KST = timezone(timedelta(hours=9))


def make_outfit(weather: WeatherSnapshotOut | None = None) -> OutfitOut:
    return OutfitOut(
        id=1,
        captured_at=datetime(2025, 11, 20, 8, 30, tzinfo=KST),
        latitude=37.5665,
        longitude=126.978,
        source="demo_sidecar",
        photo_url="/api/outfits/1/photo",
        tags=UNKNOWN_TAGS,
        vision_provider="fixture",
        weather=weather,
        effective_vote=None,
    )


def make_today() -> WeatherContextOut:
    return WeatherContextOut(
        actual_temperature_c=9.0,
        apparent_temperature_c=None,
        relative_humidity_pct=None,
        wind_speed_kmh=None,
        precipitation_mm=None,
        observed_at=None,
        provider="scenario",
        is_fixture=True,
        scenario_id="chilly-morning",
        scenario_name="쌀쌀한 아침",
    )


def make_item(rank: int = 1, explanation: str = "오늘 9.0°C") -> RecommendationItemOut:
    return RecommendationItemOut(
        rank=rank,
        outfit=make_outfit(),
        score=100.0,
        temperature_diff_c=0.0,
        selected_by_fallback=False,
        confidence_level="medium",
        reasons=[],
        explanation=explanation,
    )


@pytest.mark.parametrize("vote", ["cold", "comfortable", "hot"])
def test_feedback_in_accepts_exact_values(vote: str) -> None:
    assert FeedbackIn.model_validate({"thermal_vote": vote}).thermal_vote == vote


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"thermal_vote": None},
        {"thermal_vote": "COLD"},
        {"thermal_vote": " cold"},
        {"thermal_vote": ""},
        {"thermal_vote": 1},
        {"thermal_vote": True},
        {"thermal_vote": ["cold"]},
    ],
)
def test_feedback_in_rejects_invalid(body: dict) -> None:
    with pytest.raises(ValidationError):
        FeedbackIn.model_validate(body)


def test_feedback_in_ignores_extra_fields() -> None:
    parsed = FeedbackIn.model_validate({"thermal_vote": "hot", "outfit_id": 99, "note": "x"})
    assert parsed.model_dump() == {"thermal_vote": "hot"}


def test_optional_fields_serialize_as_null_not_omitted() -> None:
    data = json.loads(make_outfit().model_dump_json())
    assert "weather" in data and data["weather"] is None
    assert "effective_vote" in data and data["effective_vote"] is None
    today = json.loads(make_today().model_dump_json())
    for key in ("apparent_temperature_c", "observed_at", "precipitation_mm"):
        assert key in today and today[key] is None


def test_datetimes_keep_offsets() -> None:
    outfit = json.loads(make_outfit().model_dump_json())
    assert outfit["captured_at"] == "2025-11-20T08:30:00+09:00"
    fb = FeedbackOut(
        outfit_id=1,
        thermal_vote="cold",
        created_at=datetime(2025, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc),
    )
    assert json.loads(fb.model_dump_json())["created_at"] == "2025-01-01T00:00:00.123456Z"


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValidationError):
        FeedbackOut(outfit_id=1, thermal_vote="cold", created_at=datetime(2025, 1, 1))


def test_today_requires_actual_temperature() -> None:
    with pytest.raises(ValidationError):
        WeatherContextOut(**{**make_today().model_dump(), "actual_temperature_c": None})


def test_recommendation_limits() -> None:
    with pytest.raises(ValidationError):
        RecommendationResponse(
            status="ok",
            algorithm_version="rule_based_v1",
            fallback=False,
            applied_tolerance_c=3.0,
            today=make_today(),
            items=[make_item(rank=i) for i in range(1, 7)],
        )
    for bad in ({"rank": 0}, {"explanation": ""}, {"explanation": "가" * 301}):
        with pytest.raises(ValidationError):
            make_item(**bad)


def test_scan_report_and_error_shape() -> None:
    report = ScanReport(
        scanned=2,
        succeeded=1,
        duplicate=0,
        failed=1,
        errors=[
            {"file_name": "a.png", "stage": "validation", "code": "SIDECAR_NOT_FOUND", "message": "x"}
        ],
    )
    assert report.errors[0].stage == "validation"
    err = ErrorResponse(error={"code": "OUTFIT_NOT_FOUND", "message": "착장을 찾을 수 없습니다."})
    assert err.model_dump() == {
        "error": {"code": "OUTFIT_NOT_FOUND", "message": "착장을 찾을 수 없습니다."}
    }


def test_openapi_marks_nullable_fields_required() -> None:
    """Nullable output fields must be required in OpenAPI so the generated TS
    types are ``T | null`` rather than optional (Req 10.4, 10.6)."""
    app = FastAPI()

    @app.get("/outfit", response_model=OutfitOut)
    def get_outfit() -> OutfitOut:
        return make_outfit()

    client = TestClient(app)
    assert client.get("/outfit").json()["weather"] is None
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    outfit_schema = next(v for k, v in schema.items() if k.startswith("OutfitOut"))
    assert {"weather", "effective_vote", "captured_at", "source", "metadata_synthetic"} <= set(
        outfit_schema["required"]
    )
    assert set(outfit_schema["properties"]["source"]["enum"]) == {"exif", "demo_sidecar"}


def test_outfit_out_metadata_fields() -> None:  # Req 2.7, 2.15
    data = json.loads(make_outfit().model_copy(update={"source": "exif", "metadata_synthetic": False}).model_dump_json())
    assert data["source"] == "exif" and data["metadata_synthetic"] is False
    assert json.loads(make_outfit().model_dump_json())["metadata_synthetic"] is True
    with pytest.raises(ValidationError):
        OutfitOut(**{**make_outfit().model_dump(), "source": "upload"})
