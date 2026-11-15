"""Public one-file ingestion and recommendations for a given day (Phase 2 tasks 1.6).

``ImageIngestionService.ingest_file`` returns one of four result types and
``Stored.outfit_id`` is the id ``create_outfit_bundle`` returned. The two
Phase 2 hooks (``analysis_override``, ``before_commit``) used by the
uncertain-photo confirmation (Req 6.12, 6.13) are checked here at the
contract level. Phase 1 scan behaviour is covered, unchanged, by
``test_ingestion*.py`` and ``test_integration.py``.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
from sqlalchemy import text
from sqlmodel import Session, select

from app.config import resolve_config
from app.db import Outfit, PhotoAnalysisRow, create_db_engine
from app.domain import UNKNOWN_ANALYSIS, ClothingTags, VisionAnalysis, WeatherContext
from app.ingestion.service import (
    Duplicate,
    Failed,
    ImageIngestionService,
    NotOutfit,
    Stored,
)
from app.providers.weather.fixture import FixtureWeatherProvider
from app.recommendation.factory import build_strategy
from app.recommendation.service import RecommendationService
from app.repository import OutfitRepository
from app.today_weather import ScenarioStore, TodayWeatherService

TZ = ZoneInfo("Asia/Seoul")

OUTFIT = VisionAnalysis(
    photo_type="outfit",
    photo_type_confidence=0.95,
    reason="A full outfit is visible.",
    tags=ClothingTags(
        upper_type="knit", outer_type="none", lower_type="jeans",
        colors=["cream"], layers_visible=1, confidence=0.9,
    ),
)


class FakeVision:
    name = "fixture"

    def __init__(self, analysis: VisionAnalysis) -> None:
        self.analysis = analysis
        self.calls = 0

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis:
        self.calls += 1
        return self.analysis.model_copy(deep=True)


def png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def write_photo(folder: Path, name: str, color: tuple[int, int, int]) -> Path:
    path = folder / name
    path.write_bytes(png(color))
    (folder / f"{path.stem}.metadata.json").write_text(
        json.dumps({"captured_at": "2025-11-20T08:30:00+09:00", "latitude": 37.5665, "longitude": 126.978}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "photos"
    path.mkdir()
    return path


def service(folder: Path, engine, vision: FakeVision) -> ImageIngestionService:
    return ImageIngestionService(
        folder, vision, FixtureWeatherProvider(tz=TZ), OutfitRepository(engine), TZ
    )


def test_stored_returns_outfit_id(folder: Path, engine) -> None:
    photo = write_photo(folder, "a.png", (1, 2, 3))
    result = service(folder, engine, FakeVision(OUTFIT)).ingest_file(photo)
    assert isinstance(result, Stored)
    with Session(engine) as session:
        row = session.exec(select(Outfit)).one()
    assert result.outfit_id == row.id
    assert result.sha256 == row.sha256


def test_duplicate_for_stored_hash_and_for_processed_hashes(folder: Path, engine) -> None:
    photo = write_photo(folder, "a.png", (1, 2, 3))
    vision = FakeVision(OUTFIT)
    first = service(folder, engine, vision).ingest_file(photo)
    assert isinstance(first, Stored)

    again = service(folder, engine, vision).ingest_file(photo)
    assert again == Duplicate(sha256=first.sha256)

    other = write_photo(folder, "b.png", (4, 5, 6))
    other_sha = hashlib.sha256(other.read_bytes()).hexdigest()
    hashes = {other_sha}
    calls = vision.calls
    result = service(folder, engine, vision).ingest_file(other, processed_hashes=hashes)
    assert result == Duplicate(sha256=other_sha)
    assert vision.calls == calls  # no provider call for a duplicate
    assert hashes == {other_sha}  # the caller's set is read, not changed


def test_not_outfit_for_uncertain(folder: Path, engine) -> None:
    photo = write_photo(folder, "a.png", (1, 2, 3))
    result = service(folder, engine, FakeVision(UNKNOWN_ANALYSIS)).ingest_file(photo)
    assert isinstance(result, NotOutfit)
    assert result.analysis.photo_type == "uncertain"


def test_failed_for_corrupt_image(folder: Path, engine) -> None:
    photo = folder / "broken.png"
    photo.write_bytes(b"not an image")
    result = service(folder, engine, FakeVision(OUTFIT)).ingest_file(photo)
    assert isinstance(result, Failed)
    assert result.stage == "validation"


def test_analysis_override_skips_vision_and_stores(folder: Path, engine) -> None:
    photo = write_photo(folder, "a.png", (1, 2, 3))
    vision = FakeVision(UNKNOWN_ANALYSIS)
    svc = service(folder, engine, vision)
    assert isinstance(svc.ingest_file(photo), NotOutfit)
    calls = vision.calls

    confirmed = OUTFIT.model_copy(update={"reason": "user confirmed"})
    result = svc.ingest_file(photo, analysis_override=confirmed)
    assert isinstance(result, Stored)
    assert vision.calls == calls
    with Session(engine) as session:
        stored = session.exec(select(PhotoAnalysisRow)).one()
    assert stored.photo_type == "outfit"
    assert VisionAnalysis.model_validate_json(stored.analysis_json).reason == "user confirmed"

    # An existing Outfit for the hash is still a duplicate.
    assert svc.ingest_file(photo, analysis_override=confirmed) == Duplicate(sha256=result.sha256)


def test_before_commit_runs_in_the_same_transaction(folder: Path, engine) -> None:
    photo = write_photo(folder, "a.png", (1, 2, 3))
    seen: list[int] = []

    def audit(session: Session) -> None:
        outfit = session.exec(select(Outfit)).one()  # flushed, not yet committed
        seen.append(outfit.id)

    result = service(folder, engine, FakeVision(OUTFIT)).ingest_file(photo, before_commit=audit)
    assert isinstance(result, Stored)
    assert seen == [result.outfit_id]


def test_before_commit_failure_rolls_back_everything(folder: Path, engine) -> None:
    photo = write_photo(folder, "a.png", (1, 2, 3))

    def boom(session: Session) -> None:
        raise RuntimeError("audit insert failed")

    result = service(folder, engine, FakeVision(OUTFIT)).ingest_file(photo, before_commit=boom)
    assert isinstance(result, Failed)
    assert (result.stage, result.code) == ("storage", "STORAGE_ERROR")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM outfits")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM weather_snapshots")).scalar() == 0


def test_recommendations_for_uses_the_given_weather(folder: Path, engine) -> None:
    write_photo(folder, "a.png", (1, 2, 3))
    svc = service(folder, engine, FakeVision(OUTFIT))
    assert svc.scan().succeeded == 1

    cfg, _ = resolve_config({})
    fixture = FixtureWeatherProvider(tz=TZ)
    today_service = TodayWeatherService(cfg, fixture, ScenarioStore([]), fallback=fixture)
    recs = RecommendationService(
        today_service, OutfitRepository(engine), build_strategy(tolerance_c=cfg.temperature_tolerance_c)
    )
    default = recs.today_recommendations()
    given = WeatherContext(
        actual_temperature_c=-20.0, apparent_temperature_c=None, relative_humidity_pct=None,
        wind_speed_kmh=None, precipitation_mm=None, observed_at=default.today.observed_at,
        provider="fixture", is_fixture=True, scenario_id=None, scenario_name=None,
    )
    custom = recs.recommendations_for(given)
    assert custom.today.actual_temperature_c == -20.0
    assert default.today.actual_temperature_c != -20.0
