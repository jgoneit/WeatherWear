"""Shared domain value objects (design.md §2, owned by the lead).

These types are the contract shared by ingestion, providers, repository,
feedback and recommendation code. API request/response models live in
``app/schemas.py`` so table or domain changes do not leak into OpenAPI.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

ThermalVote = Literal["cold", "comfortable", "hot"]
FeedbackSource = Literal["user_action", "demo_seed"]
WeatherProviderName = Literal["open_meteo", "fixture"]
VisionProviderName = Literal["gemini", "fixture"]
ConfidenceLevel = Literal["high", "medium", "low"]

ALGORITHM_VERSION: Literal["rule_based_v1"] = "rule_based_v1"

# Canonical garment taxonomy. These snake_case identifiers are shared by
# Gemini structured output, fixtures, storage, OpenAPI and the frontend.
UpperType = Literal[
    "unknown",
    "t_shirt",
    "long_sleeve_t_shirt",
    "shirt",
    "blouse",
    "knit",
    "turtleneck",
    "sweatshirt",
    "hoodie",
    "sleeveless_top",
    "cardigan",
    "fleece",
    "jersey",
    "dress",
    "jumpsuit",
    "coveralls",
]
OuterType = Literal[
    "unknown",
    "none",
    "long_puffer",
    "short_puffer",
    "puffer_jacket",
    "parka",
    "coat",
    "trench_coat",
    "jacket",
    "blazer",
    "cardigan",
    "denim_jacket",
    "leather_jacket",
    "bomber_jacket",
    "fleece_jacket",
    "rain_jacket",
    "windbreaker",
    "track_jacket",
    "utility_jacket",
    "shirt_jacket",
    "varsity_jacket",
    "vest",
    "jersey",
]
LowerType = Literal[
    "unknown",
    "none",
    "jeans",
    "trousers",
    "slacks",
    "chinos",
    "cargo_pants",
    "corduroy_pants",
    "leggings",
    "shorts",
    "skirt",
    "track_pants",
    "jogger_pants",
    "parachute_pants",
]

UPPER_TYPES: tuple[UpperType, ...] = (
    "unknown", "t_shirt", "long_sleeve_t_shirt", "shirt", "blouse", "knit",
    "turtleneck", "sweatshirt", "hoodie", "sleeveless_top", "cardigan",
    "fleece", "jersey", "dress", "jumpsuit", "coveralls",
)
OUTER_TYPES: tuple[OuterType, ...] = (
    "unknown", "none", "long_puffer", "short_puffer", "puffer_jacket", "parka",
    "coat", "trench_coat", "jacket", "blazer", "cardigan", "denim_jacket",
    "leather_jacket", "bomber_jacket", "fleece_jacket", "rain_jacket",
    "windbreaker", "track_jacket", "utility_jacket", "shirt_jacket",
    "varsity_jacket", "vest", "jersey",
)
LOWER_TYPES: tuple[LowerType, ...] = (
    "unknown", "none", "jeans", "trousers", "slacks", "chinos", "cargo_pants",
    "corduroy_pants", "leggings", "shorts", "skirt", "track_pants",
    "jogger_pants", "parachute_pants",
)

# Known spellings written by pre-taxonomy releases. These are used only by
# the startup data migration; provider responses still have to match the
# canonical Literal values exactly.
_UPPER_ALIASES: dict[str, UpperType] = {
    "floral_blouse": "blouse",
    "short_sleeve_blouse": "blouse",
    "fleece_top": "fleece",
    "graphic_t_shirt": "t_shirt",
    "striped_t_shirt": "t_shirt",
    "long_sleeve_top": "long_sleeve_t_shirt",
    "striped_long_sleeve_top": "long_sleeve_t_shirt",
    "striped_top": "long_sleeve_t_shirt",
    "oxford_shirt": "shirt",
    "short_sleeve_shirt": "shirt",
    "striped_shirt": "shirt",
    "rugby_shirt": "jersey",
    "striped_rugby_top": "jersey",
    "soccer_jersey": "jersey",
    "knit_top": "knit",
    "sweater": "knit",
    "tank_top": "sleeveless_top",
    "wrap_cardigan": "cardigan",
    "gingham_dress": "dress",
    "knit_dress": "dress",
    "satin_slip_dress": "dress",
    "shirt_dress": "dress",
    "short_sleeve_dress": "dress",
    "sleeveless_dress": "dress",
    "top": "unknown",
}
_OUTER_ALIASES: dict[str, OuterType] = {
    "baseball_jersey": "jersey",
    "cable_knit_cardigan": "cardigan",
    "patchwork_cardigan": "cardigan",
    "cropped_puffer_jacket": "short_puffer",
    "padded_jacket": "puffer_jacket",
    "puffer_coat": "long_puffer",
    "long_puffer_coat": "long_puffer",
    "long_parka": "parka",
    "trench": "trench_coat",
    "long_coat": "coat",
    "wool_coat": "coat",
    "denim_shirt": "shirt_jacket",
    "flannel_shirt": "shirt_jacket",
    "shirt": "shirt_jacket",
    "suit_jacket": "blazer",
    "plaid_blazer": "blazer",
    "velvet_blazer": "blazer",
    "running_jacket": "track_jacket",
    "sport_jacket": "track_jacket",
    "shell_jacket": "rain_jacket",
    "light_jacket": "jacket",
    "suede_jacket": "jacket",
    "chore_jacket": "utility_jacket",
    "work_jacket": "utility_jacket",
    "long_vest": "vest",
    "sweater_vest": "vest",
    "utility_vest": "vest",
}
_LOWER_ALIASES: dict[str, LowerType] = {
    "baggy_jeans": "jeans",
    "carpenter_jeans": "jeans",
    "cargo_jeans": "cargo_pants",
    "cargo_trousers": "cargo_pants",
    "corduroy_trousers": "corduroy_pants",
    "denim_shorts": "shorts",
    "shorts_over_leggings": "shorts",
    "denim_skirt": "skirt",
    "denim_maxi_skirt": "skirt",
    "midi_skirt": "skirt",
    "pencil_skirt": "skirt",
    "plaid_midi_skirt": "skirt",
    "pleated_midi_skirt": "skirt",
    "pleated_mini_skirt": "skirt",
    "pleated_skirt": "skirt",
    "tiered_maxi_skirt": "skirt",
    "linen_trousers": "trousers",
    "wide_trousers": "trousers",
    "carpenter_trousers": "trousers",
    "plaid_trousers": "trousers",
    "suit_trousers": "trousers",
    "tartan_trousers": "trousers",
    "parachute_pants": "parachute_pants",
    "parachute_trousers": "parachute_pants",
    "wide_track_pants": "track_pants",
}


def _category_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def normalize_upper_type(value: str) -> UpperType:
    key = _category_key(value)
    if key in UPPER_TYPES:
        return cast(UpperType, key)
    return _UPPER_ALIASES.get(key, "unknown")


def normalize_outer_type(value: str) -> OuterType:
    key = _category_key(value)
    if key in OUTER_TYPES:
        return cast(OuterType, key)
    return _OUTER_ALIASES.get(key, "unknown")


def normalize_lower_type(value: str) -> LowerType:
    key = _category_key(value)
    if key in LOWER_TYPES:
        return cast(LowerType, key)
    return _LOWER_ALIASES.get(key, "unknown")


ColorName = Annotated[str, StringConstraints(min_length=1, max_length=30)]


class ClothingTags(BaseModel):
    """Canonical clothing tags extracted from one photo.

    ``unknown`` means that the position is not visible or cannot be classified.
    ``outer_type="none"`` means no outerwear is worn. ``lower_type="none"``
    is reserved for one-piece garments such as a dress, jumpsuit or coveralls;
    ``upper_type`` intentionally has no ``none`` value. Garment types use a
    closed taxonomy so providers and fixtures cannot create spelling variants.
    """

    model_config = ConfigDict(extra="forbid")

    upper_type: UpperType
    outer_type: OuterType
    lower_type: LowerType
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

PhotoType = Literal["outfit", "non_outfit", "uncertain"]
DecisionReason = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class VisionAnalysis(BaseModel):
    """Photo-type decision and visible garment analysis from one vision call."""

    model_config = ConfigDict(extra="forbid")

    photo_type: PhotoType
    photo_type_confidence: Annotated[
        float, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    reason: DecisionReason
    tags: ClothingTags

    @model_validator(mode="after")
    def non_outfit_has_no_garments(self) -> VisionAnalysis:
        if self.photo_type == "non_outfit" and self.tags != UNKNOWN_TAGS:
            raise ValueError("non_outfit analysis must use UNKNOWN_TAGS")
        return self


UNKNOWN_ANALYSIS = VisionAnalysis(
    photo_type="uncertain",
    photo_type_confidence=0.0,
    reason="No fixture analysis is available.",
    tags=UNKNOWN_TAGS,
)


Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]

# Where an Outfit's capture time and location came from (Outfit.source, Req 2.7).
MetadataSource = Literal["exif", "demo_sidecar"]


class SidecarMetadata(BaseModel):
    """Optional ``*.metadata.json`` next to a photo (Req 2.6, 2.9, 2.16).

    ``captured_at``/``latitude``/``longitude`` are only required when the photo
    has no valid embedded metadata; the sidecar parser enforces that.
    """

    captured_at: AwareDatetime | None = None  # timezone offset required when present
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    thermal_vote: ThermalVote | None = None


class CaptureMetadata(BaseModel):
    """Capture time and location resolved by the Metadata_Reader (Req 2.1, 2.8, 2.15)."""

    captured_at: AwareDatetime  # original local time + offset
    latitude: Latitude
    longitude: Longitude
    synthetic: bool  # virtual (synthetic) metadata
    origin: Literal["user_comment", "exif_standard", "sidecar"]  # for logs and tests


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
