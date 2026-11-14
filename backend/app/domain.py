"""Shared domain value objects (design.md §2, owned by the lead).

These types are the contract shared by ingestion, providers, repository,
feedback and recommendation code. API request/response models live in
``app/schemas.py`` so table or domain changes do not leak into OpenAPI.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

ThermalVote = Literal["cold", "comfortable", "hot"]
FeedbackSource = Literal["user_action", "demo_seed"]
WeatherProviderName = Literal["open_meteo", "fixture"]
VisionProviderName = Literal["gemini", "fixture"]
ConfidenceLevel = Literal["high", "medium", "low"]

ALGORITHM_VERSION: Literal["rule_based_v1"] = "rule_based_v1"

# Req 3.1 field constraints
TagType = Annotated[str, StringConstraints(min_length=1, max_length=50)]
ColorName = Annotated[str, StringConstraints(min_length=1, max_length=30)]


class ClothingTags(BaseModel):
    """Clothing tags extracted from one photo (Req 3.1, 3.6).

    ``outer_type`` is ``"none"`` when no outerwear is worn; any attribute that
    cannot be determined from the photo is ``"unknown"``.
    """

    model_config = ConfigDict(extra="forbid")

    upper_type: TagType
    outer_type: TagType
    lower_type: TagType
    colors: Annotated[list[ColorName], Field(min_length=0, max_length=5)]
    layers_visible: Annotated[int, Field(ge=0, le=5)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


# Default result for photos with no fixture entry (Req 15.8).
UNKNOWN_TAGS = ClothingTags(
    upper_type="unknown",
    outer_type="unknown",
    lower_type="unknown",
    colors=[],
    layers_visible=0,
    confidence=0.0,
)


class SidecarMetadata(BaseModel):
    """Virtual photo metadata read from ``*.metadata.json`` (Req 2.1, 2.8)."""

    captured_at: AwareDatetime  # timezone offset required
    latitude: Annotated[float, Field(ge=-90.0, le=90.0)]
    longitude: Annotated[float, Field(ge=-180.0, le=180.0)]
    thermal_vote: ThermalVote | None = None


class WeatherReading(BaseModel):
    """Weather values stored as one Weather_Snapshot (Req 4.3)."""

    observed_at: AwareDatetime
    actual_temperature_c: float
    apparent_temperature_c: float | None
    relative_humidity_pct: Annotated[float, Field(ge=0.0, le=100.0)] | None
    wind_speed_kmh: float | None
    precipitation_mm: float | None
    weather_code: int | None
    provider: WeatherProviderName
    is_fixture: bool


class WeatherContext(BaseModel):
    """Today's weather. ``actual_temperature_c`` may be None internally;
    the API turns that into 503 TODAY_TEMPERATURE_MISSING (Req 6.8)."""

    actual_temperature_c: float | None
    apparent_temperature_c: float | None
    relative_humidity_pct: float | None
    wind_speed_kmh: float | None
    precipitation_mm: float | None
    observed_at: AwareDatetime | None
    provider: Literal["open_meteo", "fixture", "scenario"]
    is_fixture: bool
    scenario_id: str | None
    scenario_name: str | None


class OutfitWithWeatherAndFeedback(BaseModel):
    """Recommendation input: one Outfit with its snapshot and Effective_Vote."""

    outfit_id: int
    captured_at: AwareDatetime
    tags: ClothingTags
    weather: WeatherReading | None
    effective_vote: ThermalVote | None


class RecommendationResult(BaseModel):
    """One ranked recommendation (Req 7.9, 8.1, 8.5)."""

    outfit_id: int
    rank: Annotated[int, Field(ge=1)]  # starts at 1
    score: float  # rounded to one decimal place
    temperature_diff_c: float  # rounded to one decimal place
    selected_by_fallback: bool
    confidence_level: ConfidenceLevel
    reasons: list[str]  # e.g. "과거에 적당했음 (+15.0점)"
    explanation: Annotated[str, StringConstraints(min_length=1, max_length=300)]  # Korean
    algorithm_version: Literal["rule_based_v1"]
