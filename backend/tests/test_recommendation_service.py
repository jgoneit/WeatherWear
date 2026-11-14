"""Unit tests for RecommendationService (task 9.10, Req 6.3, 6.5, 6.8, 8.1, 8.7, 9.6).

The last tests scan the real demo photos with fixture providers and check the
scenario outcomes listed in design.md "데모 데이터 파일" against
data/demo/scenario_expectations.json.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.demo_inventory import full_demo_images_present

from app.config import resolve_config
from app.db import create_db_engine
from app.domain import UNKNOWN_TAGS, ClothingTags, ThermalVote, WeatherContext, WeatherReading
from app.ingestion.service import ImageIngestionService
from app.providers.vision.fixture import FixtureVisionProvider
from app.providers.weather.fixture import FixtureWeatherProvider
from app.recommendation.base import MissingTodayTemperature
from app.recommendation.factory import build_strategy
from app.recommendation.service import RecommendationService
from app.repository import OutfitCreate, OutfitRepository
from app.schemas import RecommendationResponse
from app.today_weather import DEFAULT_SCENARIOS_PATH, ScenarioStore, TodayWeatherService, WeatherScenario, load_scenarios
from tests.test_demo_data import first_scan_counts

KST = timezone(timedelta(hours=9))
SEOUL = ZoneInfo("Asia/Seoul")
FIXED_NOW = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_PHOTO_DIR = REPO_ROOT / "data" / "demo" / "photos"
MANIFEST_PATH = REPO_ROOT / "data" / "demo" / "photos.manifest.json"
EXPECTATIONS_PATH = REPO_ROOT / "data" / "demo" / "scenario_expectations.json"

COAT = ClothingTags(
    upper_type="knit", outer_type="coat", lower_type="slacks", colors=["navy"], layers_visible=2, confidence=0.9
)


def _scenario(temp: float, id_: str = "s") -> WeatherScenario:
    return WeatherScenario(id=id_, name=f"name-{id_}", actual_temperature_c=temp)


class _Env:
    def __init__(self, tmp_path: Path, scenarios: list[WeatherScenario] | None = None) -> None:
        engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
        self.repo = OutfitRepository(engine)
        self.store = ScenarioStore(scenarios or [_scenario(9.0)])
        cfg, _ = resolve_config({})
        today = TodayWeatherService(
            cfg, FixtureWeatherProvider(tz=SEOUL, now=lambda: FIXED_NOW), self.store, now=lambda: FIXED_NOW
        )
        self.service = RecommendationService(today, self.repo, build_strategy(tolerance_c=Decimal("3.0")))
        self._n = 0

    def add(self, temp: float, vote: ThermalVote | None = None, tags: ClothingTags = COAT) -> int:
        self._n += 1
        captured = datetime(2025, 11, 1, 8, 0, tzinfo=KST) + timedelta(hours=self._n)
        return self.repo.create_outfit_bundle(
            OutfitCreate(
                sha256=f"{self._n:064x}",
                file_name=f"stored_file_{self._n}.png",
                captured_at=captured,
                latitude=37.5665,
                longitude=126.978,
                tags=tags,
                vision_provider="fixture",
            ),
            WeatherReading(
                observed_at=captured,
                actual_temperature_c=temp,
                apparent_temperature_c=None,
                relative_humidity_pct=None,
                wind_speed_kmh=None,
                precipitation_mm=None,
                weather_code=None,
                provider="fixture",
                is_fixture=True,
            ),
            vote,
        )

    def run(self, scenario_id: str = "s") -> RecommendationResponse:
        self.store.select(scenario_id)
        return self.service.today_recommendations()


def test_ok_response_has_items_and_full_outfit_payload(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    best = env.add(9.0, "comfortable")
    env.add(10.0)
    env.add(20.0)  # outside 3.0 and 5.0

    resp = env.run()

    assert resp.status == "ok"
    assert resp.fallback is False
    assert resp.applied_tolerance_c == 3.0
    assert resp.algorithm_version == "rule_based_v1"
    assert resp.today.provider == "scenario" and resp.today.actual_temperature_c == 9.0
    assert [i.rank for i in resp.items] == [1, 2]
    top = resp.items[0]
    assert top.outfit.id == best
    assert top.score == 115.0 and top.temperature_diff_c == 0.0
    assert top.confidence_level == "high" and top.selected_by_fallback is False
    assert top.outfit.photo_url == f"/api/outfits/{best}/photo"
    assert top.outfit.tags == COAT
    assert top.outfit.effective_vote == "comfortable"
    assert top.outfit.source == "demo_sidecar" and top.outfit.vision_provider == "fixture"
    assert top.outfit.weather is not None and top.outfit.weather.actual_temperature_c == 9.0
    assert top.outfit.captured_at.utcoffset() == timedelta(hours=9)
    # The stored file name never appears in the response.
    assert "stored_file_" not in resp.model_dump_json()


def test_at_most_five_items(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    for _ in range(8):
        env.add(9.0)
    resp = env.run()
    assert len(resp.items) == 5
    assert [i.rank for i in resp.items] == [1, 2, 3, 4, 5]


def test_fallback_expands_to_five(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.add(13.0)  # diff 4.0
    env.add(14.0)  # diff 5.0
    env.add(14.5)  # diff 5.5, excluded

    resp = env.run()

    assert resp.status == "ok"
    assert resp.fallback is True
    assert resp.applied_tolerance_c == 5.0
    assert [i.temperature_diff_c for i in resp.items] == [4.0, 5.0]
    assert all(i.selected_by_fallback for i in resp.items)


def test_insufficient_data_returns_empty_items(tmp_path: Path) -> None:
    env = _Env(tmp_path, [_scenario(38.0)])
    env.add(9.0)

    resp = env.run()

    assert resp.status == "insufficient_data"
    assert resp.fallback is True
    assert resp.items == []
    assert resp.applied_tolerance_c == 5.0
    assert resp.today.actual_temperature_c == 38.0


def test_insufficient_data_with_no_outfits(tmp_path: Path) -> None:
    resp = _Env(tmp_path).run()
    assert resp.status == "insufficient_data" and resp.fallback is True and resp.items == []


def test_missing_today_temperature_propagates(tmp_path: Path) -> None:
    class NoTemperature:
        def get_today(self) -> WeatherContext:
            return WeatherContext(
                actual_temperature_c=None,
                apparent_temperature_c=None,
                relative_humidity_pct=None,
                wind_speed_kmh=None,
                precipitation_mm=None,
                observed_at=None,
                provider="fixture",
                is_fixture=True,
                scenario_id=None,
                scenario_name=None,
            )

    env = _Env(tmp_path)
    env.add(9.0)
    service = RecommendationService(NoTemperature(), env.repo, build_strategy(tolerance_c=Decimal("3.0")))  # type: ignore[arg-type]
    with pytest.raises(MissingTodayTemperature):
        service.today_recommendations()


def test_new_feedback_changes_next_result_without_cache(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    first = env.add(9.0)
    second = env.add(9.0)
    assert [i.outfit.id for i in env.run().items] == [first, second]  # tie -> smaller id

    env.repo.add_feedback(second, "comfortable", "user_action", datetime.now(UTC))
    resp = env.run()

    assert [i.outfit.id for i in resp.items] == [second, first]
    assert resp.items[0].score == 115.0
    assert resp.items[0].outfit.effective_vote == "comfortable"


def test_outfit_with_no_displayable_tags_still_listed(tmp_path: Path) -> None:
    # Only the all-unknown + confidence 0.0 case is a non-outfit photo (Req 6.10).
    no_display = ClothingTags(
        upper_type="unknown", outer_type="none", lower_type="unknown", colors=[], layers_visible=0, confidence=0.4
    )
    env = _Env(tmp_path)
    env.add(9.0, tags=no_display)
    item = env.run().items[0]
    assert "착장 태그 정보 없음" in item.explanation
    assert item.outfit.effective_vote is None


def test_stored_row_without_garment_evidence_is_never_recommended(tmp_path: Path) -> None:
    # A scan never stores a non-outfit photo (the ingestion gate reports it as
    # non_outfit or uncertain; see test_ingestion_service). This row is inserted
    # through the repository without the gate, so it is listed, and the
    # recommendation safety net (Req 6.10) still keeps it out of the results.
    env = _Env(tmp_path)
    no_garments = env.add(9.0, tags=UNKNOWN_TAGS)
    assert env.run().items == []
    assert [r.id for r in env.repo.list_outfits()] == [no_garments]


# --- Real demo data (fixture mode) -------------------------------------------


@pytest.fixture(scope="module")
def demo_env(tmp_path_factory: pytest.TempPathFactory) -> tuple[RecommendationService, ScenarioStore, dict[int, str]]:
    if not full_demo_images_present():
        pytest.skip(
            "The complete private demo album is not installed; "
            "run scripts/prepare_demo_photos.py to copy the demo photos"
        )
    engine = create_db_engine(f"sqlite:///{tmp_path_factory.mktemp('demo') / 'demo.db'}")
    repo = OutfitRepository(engine)
    weather = FixtureWeatherProvider(tz=SEOUL)
    report = ImageIngestionService(DEMO_PHOTO_DIR, FixtureVisionProvider.from_file(), weather, repo, SEOUL).scan()
    assert report.failed == 0, report.errors
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert report.scanned == sum(1 for p in manifest["photos"] if p["rights_confirmed"] is True)
    counts = first_scan_counts()  # from the vision.json photo types (ingestion gate)
    assert {key: getattr(report, key) for key in counts} == counts

    scenarios, errors = load_scenarios(DEFAULT_SCENARIOS_PATH)
    assert errors == []
    store = ScenarioStore(scenarios)
    cfg, _ = resolve_config({})
    today = TodayWeatherService(cfg, weather, store)
    service = RecommendationService(today, repo, build_strategy(tolerance_c=Decimal("3.0")))
    names = {r.id: r.file_name for r in repo.list_outfits()}
    return service, store, names


def _demo(demo_env, scenario_id: str) -> tuple[RecommendationResponse, list[str]]:  # type: ignore[no-untyped-def]
    service, store, names = demo_env
    store.select(scenario_id)
    resp = service.today_recommendations()
    return resp, [names[i.outfit.id] for i in resp.items]


def _expected(scenario_id: str) -> dict[str, Any]:
    """The scenario_expectations.json entry recorded by scripts/select_scenarios.py."""
    doc = json.loads(EXPECTATIONS_PATH.read_text(encoding="utf-8"))
    return next(s for s in doc["scenarios"] if s["id"] == scenario_id)


def _digest(resp: RecommendationResponse, files: list[str]) -> dict[str, Any]:
    """The response in the shape of a scenario_expectations.json entry."""
    return {
        "status": resp.status,
        "fallback": resp.fallback,
        "applied_tolerance_c": resp.applied_tolerance_c,
        "count": len(resp.items),
        "top_file": files[0] if files else None,
        "items": [
            {
                "rank": item.rank,
                "file": file,
                "past_temperature_c": item.outfit.weather.actual_temperature_c if item.outfit.weather else None,
                "temperature_diff_c": item.temperature_diff_c,
                "score": item.score,
                "effective_vote": item.outfit.effective_vote,
                "confidence_level": item.confidence_level,
                "selected_by_fallback": item.selected_by_fallback,
            }
            for item, file in zip(resp.items, files, strict=True)
        ],
    }


def _assert_matches_expectation(resp: RecommendationResponse, files: list[str], scenario_id: str) -> None:
    expected = _expected(scenario_id)
    assert resp.today.actual_temperature_c == expected["actual_temperature_c"]
    keys = ("status", "fallback", "applied_tolerance_c", "count", "top_file", "items")
    assert _digest(resp, files) == {key: expected[key] for key in keys}


def test_demo_chilly_morning(demo_env) -> None:  # type: ignore[no-untyped-def]
    resp, files = _demo(demo_env, "chilly-morning")
    assert resp.status == "ok" and resp.fallback is False and resp.applied_tolerance_c == 3.0
    assert len(resp.items) == 5
    assert all(i.temperature_diff_c <= 3.0 for i in resp.items)
    _assert_matches_expectation(resp, files, "chilly-morning")


def test_demo_cold_snap_boundary(demo_env) -> None:  # type: ignore[no-untyped-def]
    resp, files = _demo(demo_env, "cold-snap")
    assert resp.status == "ok" and resp.fallback is False and resp.applied_tolerance_c == 3.0
    assert len(files) == 1
    assert resp.items[0].temperature_diff_c == 3.0  # exactly on the 3.0°C tolerance boundary
    assert resp.items[0].confidence_level == "high"
    _assert_matches_expectation(resp, files, "cold-snap")


def test_demo_early_heat_fallback(demo_env) -> None:  # type: ignore[no-untyped-def]
    resp, files = _demo(demo_env, "early-heat")
    assert resp.status == "ok" and resp.fallback is True and resp.applied_tolerance_c == 5.0
    assert 1 <= len(resp.items) <= 5
    assert all(i.selected_by_fallback for i in resp.items)
    assert all(3.5 <= i.temperature_diff_c <= 5.0 for i in resp.items)
    assert all(i.confidence_level in ("medium", "low") for i in resp.items)
    _assert_matches_expectation(resp, files, "early-heat")


def test_demo_heatwave_insufficient(demo_env) -> None:  # type: ignore[no-untyped-def]
    resp, files = _demo(demo_env, "heatwave")
    assert resp.status == "insufficient_data" and resp.fallback is True and resp.items == []
    _assert_matches_expectation(resp, files, "heatwave")
