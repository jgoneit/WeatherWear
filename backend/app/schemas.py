"""API request/response models (design.md §11, owned by the lead).

These models are the OpenAPI contract the frontend consumes through
``openapi-typescript``. They are kept separate from the SQLModel tables in
``app/db.py`` and from the domain value objects in ``app/domain.py`` so that
storage changes do not leak into the API.

Conventions:
- Every datetime is ``AwareDatetime`` and serializes with ``Z`` or ``±HH:MM``
  (Req 10.10).
- Optional output fields have no default: they are always present and
  serialize as ``null`` instead of being omitted (Req 10.4). Do not use
  ``response_model_exclude_none``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.domain import (
    ClothingTags,
    ConfidenceLevel,
    MetadataSource,
    ThermalVote,
    VisionProviderName,
    WeatherProviderName,
)

AlgorithmVersion = Literal["rule_based_v1"]
TodayWeatherProvider = Literal["open_meteo", "fixture", "scenario"]
FailureStage = Literal["validation", "analysis", "storage"]  # 화면 표기: 검증/분석/저장

MAX_RECOMMENDATIONS = 5


# --- Common error -----------------------------------------------------------


class ErrorDetail(BaseModel):
    code: str  # e.g. "OUTFIT_NOT_FOUND"
    message: str  # Korean user-facing message, never contains paths or secrets


class ErrorResponse(BaseModel):
    """``{"error": {"code": "...", "message": "..."}}`` for every error status."""

    error: ErrorDetail


# --- Demo: scan -------------------------------------------------------------


class ScanError(BaseModel):
    file_name: str  # file name only, never an absolute path
    stage: FailureStage
    code: str  # e.g. "SIDECAR_MISSING_FIELD", "VISION_TIMEOUT"
    message: str  # Korean reason


class ScanReport(BaseModel):
    """Result of ``POST /api/demo/scan`` (Req 1.2).

    ``scanned = succeeded + duplicate + failed``.
    """

    scanned: Annotated[int, Field(ge=0)]
    succeeded: Annotated[int, Field(ge=0)]
    duplicate: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    errors: list[ScanError]


# --- Demo: scenarios and status ---------------------------------------------


class ScenarioOut(BaseModel):
    id: str
    name: str
    actual_temperature_c: float
    apparent_temperature_c: float | None
    relative_humidity_pct: float | None
    wind_speed_kmh: float | None
    precipitation_mm: float | None


class ScenarioListResponse(BaseModel):
    """``GET /api/demo/scenarios`` (Req 5.2, 14.3)."""

    scenarios: list[ScenarioOut]
    selected_id: str | None


class ScenarioSelectIn(BaseModel):
    """Body of ``PUT /api/demo/scenarios/selected``."""

    scenario_id: str


class ScenarioSelection(BaseModel):
    """Response of ``PUT /api/demo/scenarios/selected`` (Req 5.3)."""

    selected_id: str
    scenario: ScenarioOut


class DemoStatus(BaseModel):
    """``GET /api/demo/status`` (Req 14, 16.5). The API key is exposed only as a bool."""

    vision_provider: VisionProviderName
    weather_provider: WeatherProviderName
    gemini_api_key_configured: bool
    outfit_count: Annotated[int, Field(ge=0)]
    weather_snapshot_count: Annotated[int, Field(ge=0)]
    feedback_count: Annotated[int, Field(ge=0)]
    selected_scenario: ScenarioOut | None


class AppMeta(BaseModel):
    """``GET /api/meta`` (Req 11.4)."""

    app_timezone: str  # IANA name, e.g. "Asia/Seoul"
    algorithm_version: AlgorithmVersion
    vision_provider: VisionProviderName
    weather_provider: WeatherProviderName


# --- Weather ----------------------------------------------------------------


class WeatherContextOut(BaseModel):
    """Today's weather. A missing actual temperature is a 503, never ``null`` here."""

    actual_temperature_c: float
    apparent_temperature_c: float | None
    relative_humidity_pct: float | None
    wind_speed_kmh: float | None
    precipitation_mm: float | None
    observed_at: AwareDatetime | None
    provider: TodayWeatherProvider
    is_fixture: bool
    scenario_id: str | None
    scenario_name: str | None


class WeatherSnapshotOut(BaseModel):
    observed_at: AwareDatetime
    actual_temperature_c: float
    apparent_temperature_c: float | None
    relative_humidity_pct: float | None
    wind_speed_kmh: float | None
    precipitation_mm: float | None
    weather_code: int | None
    provider: WeatherProviderName
    is_fixture: bool


# --- Outfits ----------------------------------------------------------------


class OutfitOut(BaseModel):
    # Fields with defaults are still listed as required in the response schema.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: int
    captured_at: AwareDatetime  # original offset of the photo metadata
    latitude: float
    longitude: float
    source: MetadataSource  # where capture time/location came from (Req 2.7)
    # Virtual (synthetic) capture metadata (Req 2.15, 15.4). The default only
    # keeps older builders working; it is always serialized.
    metadata_synthetic: bool = True
    photo_url: str  # "/api/outfits/{id}/photo"
    tags: ClothingTags
    vision_provider: VisionProviderName
    weather: WeatherSnapshotOut | None  # null, never omitted (Req 10.4)
    effective_vote: ThermalVote | None  # null, never omitted


# --- Recommendations --------------------------------------------------------


class RecommendationItemOut(BaseModel):
    rank: Annotated[int, Field(ge=1)]
    outfit: OutfitOut
    score: float
    temperature_diff_c: float
    selected_by_fallback: bool
    confidence_level: ConfidenceLevel
    reasons: list[str]
    explanation: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class RecommendationResponse(BaseModel):
    """``GET /api/recommendations/today`` (Req 8.1, 8.7)."""

    status: Literal["ok", "insufficient_data"]
    algorithm_version: AlgorithmVersion
    fallback: bool
    applied_tolerance_c: float  # default tolerance or 5.0
    today: WeatherContextOut
    items: Annotated[list[RecommendationItemOut], Field(max_length=MAX_RECOMMENDATIONS)]


# --- Feedback ---------------------------------------------------------------


class FeedbackIn(BaseModel):
    """Body of ``POST /api/outfits/{id}/feedback`` (Req 9.1, 9.2, 9.7).

    Strict mode: only the exact lowercase strings are accepted. ``"COLD"``,
    ``" cold"``, ``""``, numbers, ``null`` and a missing field are rejected.
    Unknown extra fields are ignored and never stored.
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    thermal_vote: ThermalVote


class FeedbackOut(BaseModel):
    outfit_id: int
    thermal_vote: ThermalVote
    created_at: AwareDatetime  # UTC, serialized with "Z"
