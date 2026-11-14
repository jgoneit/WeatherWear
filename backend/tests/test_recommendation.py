"""Tolerance boundary, insufficient-data and structure tests (task 9.11).

Areas (c) and (d) of Requirement 17.4, plus the strategy import rule (Req 7.13)
and the 300-character explanation limit (Req 8.1).

Rounding rule under test (Req 6.7): ``diff = round_half_up(|past - today|, 1)``
is computed first and compared with the tolerance after that, so a raw
difference of 3.04 counts as 3.0 (included) and 3.05 counts as 3.1 (excluded).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import resolve_config
from app.db import create_db_engine
from app.domain import (
    UNKNOWN_TAGS,
    ClothingTags,
    OutfitWithWeatherAndFeedback,
    ThermalVote,
    WeatherContext,
    WeatherReading,
)
from app.providers.weather.fixture import FixtureWeatherProvider
from app.recommendation.base import MissingTodayTemperature, RecommendationOutcome
from app.recommendation.explain import MAX_EXPLANATION_LENGTH, build_explanation, display_tags
from app.recommendation.factory import build_strategy
from app.recommendation.rule_based_v1 import FALLBACK_TOLERANCE_C, vote_adjustment
from app.recommendation.service import RecommendationService
from app.repository import OutfitCreate, OutfitRepository
from app.today_weather import ScenarioStore, TodayWeatherService, WeatherScenario

KST = timezone(timedelta(hours=9))
SEOUL = ZoneInfo("Asia/Seoul")
AT = datetime(2025, 11, 20, 8, 30, tzinfo=KST)
FIXED_NOW = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
APP_DIR = Path(__file__).resolve().parents[1] / "app"

COAT = ClothingTags(
    upper_type="knit", outer_type="coat", lower_type="slacks", colors=["navy"], layers_visible=2, confidence=0.9
)


def _today(temp: float | None = 9.0) -> WeatherContext:
    return WeatherContext(
        actual_temperature_c=temp,
        apparent_temperature_c=None,
        relative_humidity_pct=None,
        wind_speed_kmh=None,
        precipitation_mm=None,
        observed_at=None,
        provider="scenario",
        is_fixture=True,
        scenario_id="s",
        scenario_name="s",
    )


def _reading(temp: float) -> WeatherReading:
    return WeatherReading(
        observed_at=AT,
        actual_temperature_c=temp,
        apparent_temperature_c=None,
        relative_humidity_pct=None,
        wind_speed_kmh=None,
        precipitation_mm=None,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )


def _outfit(
    outfit_id: int,
    temp: float | None,
    vote: ThermalVote | None = None,
    tags: ClothingTags = COAT,
    *,
    has_weather: bool = True,
) -> OutfitWithWeatherAndFeedback:
    weather: WeatherReading | None = None
    if has_weather:
        weather = _reading(0.0)
        if temp is None:
            # WeatherReading requires a temperature; build the "no temperature"
            # case the strategy must also skip (Req 6.9).
            weather = weather.model_copy(update={"actual_temperature_c": None})
        else:
            weather = weather.model_copy(update={"actual_temperature_c": temp})
    return OutfitWithWeatherAndFeedback(
        outfit_id=outfit_id, captured_at=AT, tags=tags, weather=weather, effective_vote=vote
    )


def _recommend(today_c: float | None, candidates: list[OutfitWithWeatherAndFeedback]) -> RecommendationOutcome:
    return build_strategy(tolerance_c=Decimal("3.0")).recommend_with_outcome(_today(today_c), candidates)


def _ids(outcome: RecommendationOutcome) -> list[int]:
    return [r.outfit_id for r in outcome.results]


# --- (c) Tolerance boundaries (Req 6.1, 6.3, 6.7) ---------------------------


def test_diff_exactly_3_0_included() -> None:
    outcome = _recommend(9.0, [_outfit(1, 12.0), _outfit(2, 6.0)])

    assert sorted(_ids(outcome)) == [1, 2]
    assert outcome.fallback is False
    assert outcome.applied_tolerance_c == Decimal("3.0")
    assert all(r.temperature_diff_c == 3.0 and not r.selected_by_fallback for r in outcome.results)


def test_diff_3_1_excluded() -> None:
    outcome = _recommend(9.0, [_outfit(1, 12.1), _outfit(2, 5.9), _outfit(3, 10.0)])

    assert _ids(outcome) == [3]
    assert outcome.fallback is False


def test_rounding_3_04_included_3_05_excluded() -> None:
    # 12.04 / 5.96 -> raw 3.04 -> 3.0 (in); 12.05 / 5.95 -> raw 3.05 -> 3.1 (out).
    outcome = _recommend(9.0, [_outfit(1, 12.04), _outfit(2, 5.96), _outfit(3, 12.05), _outfit(4, 5.95)])

    assert sorted(_ids(outcome)) == [1, 2]
    assert outcome.fallback is False
    assert all(r.temperature_diff_c == 3.0 for r in outcome.results)


def test_rounding_3_05_alone_triggers_fallback_as_3_1() -> None:
    outcome = _recommend(9.0, [_outfit(1, 12.05)])

    assert _ids(outcome) == [1]
    assert outcome.fallback is True
    assert outcome.results[0].temperature_diff_c == 3.1


def test_fallback_boundary_5_0_included_5_1_excluded() -> None:
    outcome = _recommend(9.0, [_outfit(1, 14.0), _outfit(2, 4.0), _outfit(3, 14.1), _outfit(4, 3.9)])

    assert sorted(_ids(outcome)) == [1, 2]
    assert outcome.fallback is True
    assert outcome.applied_tolerance_c == FALLBACK_TOLERANCE_C == Decimal("5.0")
    assert all(r.temperature_diff_c == 5.0 and r.selected_by_fallback for r in outcome.results)


def test_fallback_applied_once_only() -> None:
    # Nothing within 3.0 and nothing within 5.0: no further expansion.
    beyond = _recommend(9.0, [_outfit(1, 14.1), _outfit(2, 20.0), _outfit(3, -5.0)])
    assert beyond.results == []
    assert beyond.fallback is True
    assert beyond.applied_tolerance_c == Decimal("5.0")

    # Something within 3.0: the 5.0 range is not added on top.
    normal = _recommend(9.0, [_outfit(1, 11.0), _outfit(2, 13.0), _outfit(3, 14.0)])
    assert _ids(normal) == [1]
    assert normal.fallback is False
    assert normal.applied_tolerance_c == Decimal("3.0")


# --- (d) Insufficient data (Req 6.5, 6.8, 6.9, 8.7) -------------------------


def test_insufficient_data_status(tmp_path: Path) -> None:
    repo = OutfitRepository(create_db_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    repo.create_outfit_bundle(
        OutfitCreate(
            sha256="a" * 64,
            file_name="far.png",
            captured_at=AT,
            latitude=37.5665,
            longitude=126.978,
            tags=COAT,
            vision_provider="fixture",
        ),
        _reading(9.0),  # 29.0°C away from today
        "comfortable",
    )
    store = ScenarioStore([WeatherScenario(id="heat", name="폭염", actual_temperature_c=38.0)])
    store.select("heat")
    cfg, _ = resolve_config({})
    today = TodayWeatherService(
        cfg, FixtureWeatherProvider(tz=SEOUL, now=lambda: FIXED_NOW), store, now=lambda: FIXED_NOW
    )
    service = RecommendationService(today, repo, build_strategy(tolerance_c=Decimal("3.0")))

    resp = service.today_recommendations()

    assert resp.status == "insufficient_data"
    assert resp.fallback is True
    assert resp.items == []
    assert resp.today.actual_temperature_c == 38.0


def test_outfits_without_weather_excluded() -> None:
    outcome = _recommend(
        9.0,
        [
            _outfit(1, None, "comfortable", has_weather=False),  # no Weather_Snapshot
            _outfit(2, None, "comfortable"),  # snapshot without temperature
            _outfit(3, 9.5),
        ],
    )
    assert _ids(outcome) == [3]

    only_missing = _recommend(9.0, [_outfit(1, None, has_weather=False), _outfit(2, None)])
    assert only_missing.results == []
    assert only_missing.fallback is True


def test_missing_today_temperature_raises() -> None:
    with pytest.raises(MissingTodayTemperature):
        _recommend(None, [_outfit(1, 9.0)])
    with pytest.raises(MissingTodayTemperature):
        _recommend(None, [])


# --- Non-outfit photos (Req 6.10, 15.13) ------------------------------------


def test_non_outfit_photo_excluded_from_candidates() -> None:
    outcome = _recommend(9.0, [_outfit(1, 9.0, "comfortable", UNKNOWN_TAGS), _outfit(2, 10.0)])
    assert _ids(outcome) == [2]
    assert outcome.fallback is False

    only_non_outfit = _recommend(9.0, [_outfit(1, 9.0, tags=UNKNOWN_TAGS)])
    assert only_non_outfit.results == [] and only_non_outfit.fallback is True


def test_non_outfit_photo_does_not_prevent_fallback() -> None:
    # The non-outfit photo is within 3.0, but only the 4.0 diff outfit counts.
    outcome = _recommend(9.0, [_outfit(1, 9.5, tags=UNKNOWN_TAGS), _outfit(2, 13.0)])
    assert _ids(outcome) == [2]
    assert outcome.fallback is True
    assert outcome.applied_tolerance_c == FALLBACK_TOLERANCE_C


# --- Structure (Req 7.13) and explanation length (Req 8.1) ------------------

STRATEGY_MODULES = ["base.py", "rule_based_v1.py", "explain.py"]
FORBIDDEN_IMPORT_PREFIXES = (
    "app.providers",
    "app.repository",
    "app.db",
    "app.ingestion",
    "app.today_weather",
    "app.feedback",
    "app.api",
    "app.main",
    "app.deps",
    "sqlmodel",
    "sqlalchemy",
    "httpx",
    "google",
)


def _imported_modules(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import"
            names.append(node.module or "")
    return names


@pytest.mark.parametrize("module_file", STRATEGY_MODULES)
def test_strategy_module_imports_no_providers_or_repo(module_file: str) -> None:
    imports = _imported_modules(APP_DIR / "recommendation" / module_file)

    assert imports, "parsed no imports; wrong file?"
    bad = [m for m in imports if m.startswith(FORBIDDEN_IMPORT_PREFIXES)]
    assert bad == [], f"{module_file} imports {bad}"
    app_imports = {m for m in imports if m.startswith("app.")}
    assert app_imports <= {"app.domain", "app.recommendation.base", "app.recommendation.explain"}


CANONICAL_TAGS = ClothingTags(
    upper_type="long_sleeve_t_shirt",
    outer_type="varsity_jacket",
    lower_type="parachute_pants",
    colors=["c" * 30] * 5,
    layers_visible=5,
    confidence=1.0,
)


@pytest.mark.parametrize("vote", ["cold", "comfortable", "hot", None])
def test_explanation_max_length_with_canonical_tags(vote: ThermalVote | None) -> None:
    tags = display_tags(CANONICAL_TAGS)
    assert len(tags) == 3
    explanation, reasons = build_explanation(
        Decimal("-25.5"),
        Decimal("-20.5"),
        Decimal("5.0"),
        tags,
        vote,
        True,
        adjustment=vote_adjustment(vote) if vote is not None else None,
    )
    assert 1 <= len(explanation) <= MAX_EXPLANATION_LENGTH
    assert "기온 허용 범위를 5.0°C로 확장" in explanation
    assert all(t[:20] in explanation for t in tags)
    assert reasons

    # Same limit through the strategy, for every selected result.
    outcome = _recommend(-25.5, [_outfit(i, -20.5, vote, CANONICAL_TAGS) for i in range(1, 7)])
    assert len(outcome.results) == 5
    for r in outcome.results:
        assert 1 <= len(r.explanation) <= MAX_EXPLANATION_LENGTH
        assert "-25.5°C" in r.explanation and "-20.5°C" in r.explanation
