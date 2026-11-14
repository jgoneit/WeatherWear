"""Unit tests for app/ingestion/service.py (task 6.4).

Covers the pipeline order, stage/code reporting, in-scan duplicate rules and
the ScanReport invariant. Area (a) tests named in task 6.7 live in
``test_ingestion.py``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.db import create_db_engine
from app.domain import UNKNOWN_TAGS, ClothingTags, VisionAnalysis
from app.ingestion.service import ImageIngestionService, PhotoDirUnavailable
from app.providers.vision.base import VisionError
from app.providers.weather.fixture import FixtureWeatherProvider
from app.repository import OutfitRepository
from app.schemas import ScanReport

TZ = ZoneInfo("Asia/Seoul")


DEFAULT_ANALYSIS = VisionAnalysis(
    photo_type="outfit",
    photo_type_confidence=0.99,
    reason="A full outfit is visible.",
    tags=ClothingTags(
        upper_type="t_shirt",
        outer_type="none",
        lower_type="jeans",
        colors=["white", "blue"],
        layers_visible=1,
        confidence=0.9,
    ),
)


class CountingVision:
    """Vision fake that counts calls and can fail selected calls."""

    name = "fixture"

    def __init__(
        self,
        fail_calls: set[int] | None = None,
        kind: str = "timeout",
        analysis: VisionAnalysis = DEFAULT_ANALYSIS,
    ) -> None:
        self.fail_calls = fail_calls or set()
        self.kind = kind
        self.analysis = analysis
        self.calls: list[str] = []

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis:
        self.calls.append(sha256)
        if len(self.calls) in self.fail_calls:
            raise VisionError(self.kind)  # type: ignore[arg-type]
        return self.analysis.model_copy(deep=True)


class FailingStoreRepository(OutfitRepository):
    def create_outfit_bundle(self, outfit, weather, seed_vote):  # type: ignore[override]
        raise RuntimeError("disk full")


def png_bytes(color: tuple[int, int, int] = (200, 10, 10), fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format=fmt)
    return buf.getvalue()


def write_photo(
    folder: Path, name: str, data: bytes, sidecar: dict | str | None = "default"
) -> None:
    (folder / name).write_bytes(data)
    if sidecar is None:
        return
    if sidecar == "default":
        sidecar = {"captured_at": "2025-11-20T08:30:00+09:00", "latitude": 37.5665, "longitude": 126.978}
    stem = Path(name).stem
    text = sidecar if isinstance(sidecar, str) else json.dumps(sidecar)
    (folder / f"{stem}.metadata.json").write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'scan.db'}")
    yield OutfitRepository(engine)
    engine.dispose()


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def make_service(photo_dir: Path, repo: OutfitRepository, vision=None) -> ImageIngestionService:
    return ImageIngestionService(
        photo_dir, vision or CountingVision(), FixtureWeatherProvider(tz=TZ), repo, TZ
    )


def assert_invariant(report: ScanReport) -> None:
    assert report.scanned == (
        report.succeeded
        + report.duplicate
        + report.skipped_non_outfit
        + report.uncertain
        + report.failed
    )
    assert len(report.errors) == report.failed
    assert len(report.decisions) == report.skipped_non_outfit + report.uncertain


def test_mixed_folder_reports_each_failure_stage_and_code(photo_dir: Path, repo) -> None:
    write_photo(photo_dir, "a_ok.png", png_bytes((1, 2, 3)))
    write_photo(photo_dir, "b_upper.PNG", png_bytes((4, 5, 6)))
    write_photo(photo_dir, "c_corrupt.png", b"not an image")
    write_photo(photo_dir, "d_png_named_jpg.jpg", png_bytes((7, 8, 9)))
    write_photo(photo_dir, "e_no_sidecar.png", png_bytes((10, 11, 12)), sidecar=None)
    write_photo(photo_dir, "f_bad_vote.png", png_bytes((13, 14, 15)), sidecar={
        "captured_at": "2025-11-20T08:30:00+09:00", "latitude": 37.5, "longitude": 127.0,
        "thermal_vote": "COLD",
    })
    (photo_dir / "notes.txt").write_text("ignored")
    sub = photo_dir / "nested"
    sub.mkdir()
    write_photo(sub, "inner.png", png_bytes((16, 17, 18)))

    vision = CountingVision()
    report = make_service(photo_dir, repo, vision).scan()

    assert_invariant(report)
    assert (report.scanned, report.succeeded, report.duplicate, report.failed) == (6, 2, 0, 4)
    by_file = {e.file_name: (e.stage, e.code) for e in report.errors}
    assert by_file == {
        "c_corrupt.png": ("validation", "INVALID_IMAGE_FORMAT"),
        "d_png_named_jpg.jpg": ("validation", "INVALID_IMAGE_FORMAT"),
        "e_no_sidecar.png": ("validation", "SIDECAR_NOT_FOUND"),
        "f_bad_vote.png": ("validation", "SIDECAR_THERMAL_VOTE_INVALID"),
    }
    assert len(vision.calls) == 4  # every decodable image is classified before sidecar validation
    assert repo.counts() == {"outfits": 2, "weather_snapshots": 2, "feedback": 0}
    assert all(str(photo_dir) not in e.message for e in report.errors)


def test_non_outfit_is_skipped_before_sidecar_and_weather(photo_dir: Path, repo) -> None:
    (photo_dir / "food.png").write_bytes(png_bytes())  # deliberately no sidecar
    analysis = VisionAnalysis(
        photo_type="non_outfit",
        photo_type_confidence=0.98,
        reason="Only a meal is visible.",
        tags=UNKNOWN_TAGS,
    )
    vision = CountingVision(analysis=analysis)

    class WeatherMustNotRun:
        name = "fixture"

        def get_weather(self, *args):
            raise AssertionError("weather must not be called for a non-outfit photo")

    report = ImageIngestionService(photo_dir, vision, WeatherMustNotRun(), repo, TZ).scan()

    assert_invariant(report)
    assert (report.succeeded, report.skipped_non_outfit, report.uncertain, report.failed) == (
        0, 1, 0, 0
    )
    assert repo.counts()["outfits"] == 0
    assert report.decisions[0].file_name == "food.png"
    assert report.decisions[0].photo_type == "non_outfit"


def test_low_confidence_outfit_becomes_uncertain_before_sidecar(photo_dir: Path, repo) -> None:
    (photo_dir / "ambiguous.png").write_bytes(png_bytes())  # deliberately no sidecar
    analysis = DEFAULT_ANALYSIS.model_copy(
        update={"photo_type_confidence": 0.69, "reason": "A partly visible person."}
    )

    report = make_service(photo_dir, repo, CountingVision(analysis=analysis)).scan()

    assert_invariant(report)
    assert (report.succeeded, report.skipped_non_outfit, report.uncertain, report.failed) == (
        0, 0, 1, 0
    )
    assert repo.counts()["outfits"] == 0
    assert report.decisions[0].photo_type == "uncertain"
    assert "0.70 acceptance threshold" in report.decisions[0].reason


def test_same_bytes_in_one_scan_analyzed_once(photo_dir: Path, repo) -> None:
    data = png_bytes()
    write_photo(photo_dir, "a.png", data)
    write_photo(photo_dir, "b.png", data)
    vision = CountingVision()

    report = make_service(photo_dir, repo, vision).scan()

    assert (report.succeeded, report.duplicate) == (1, 1)
    assert len(vision.calls) == 1
    assert repo.counts()["outfits"] == 1


def test_failed_hash_is_retried_by_next_file_in_same_scan(photo_dir: Path, repo) -> None:
    data = png_bytes()
    write_photo(photo_dir, "a.png", data)
    write_photo(photo_dir, "b.png", data)
    vision = CountingVision(fail_calls={1})

    report = make_service(photo_dir, repo, vision).scan()

    assert (report.succeeded, report.duplicate, report.failed) == (1, 0, 1)
    assert report.errors[0].file_name == "a.png"
    assert len(vision.calls) == 2


@pytest.mark.parametrize(
    ("kind", "code"),
    [("timeout", "VISION_TIMEOUT"), ("api_error", "VISION_API_ERROR"),
     ("schema_invalid", "VISION_SCHEMA_INVALID")],
)
def test_vision_failure_stores_nothing_and_rescan_retries(
    photo_dir: Path, repo, kind: str, code: str
) -> None:
    write_photo(photo_dir, "a.png", png_bytes())
    vision = CountingVision(fail_calls={1}, kind=kind)
    service = make_service(photo_dir, repo, vision)

    first = service.scan()
    assert first.failed == 1
    assert (first.errors[0].stage, first.errors[0].code) == ("analysis", code)
    assert repo.counts()["outfits"] == 0

    second = service.scan()  # call 2 succeeds: failed file is processed again (Req 1.7)
    assert (second.succeeded, second.duplicate, second.failed) == (1, 0, 0)
    assert len(vision.calls) == 2

    third = service.scan()
    assert (third.succeeded, third.duplicate) == (0, 1)
    assert len(vision.calls) == 2  # duplicates call no provider


def test_storage_error_reported_at_storage_stage(photo_dir: Path, tmp_path: Path) -> None:
    write_photo(photo_dir, "a.png", png_bytes())
    engine = create_db_engine(f"sqlite:///{tmp_path / 'fail.db'}")
    repo = FailingStoreRepository(engine)

    report = make_service(photo_dir, repo).scan()

    assert report.failed == 1
    assert (report.errors[0].stage, report.errors[0].code) == ("storage", "STORAGE_ERROR")
    assert "disk full" not in report.errors[0].message
    engine.dispose()


def test_seed_vote_and_sidecar_values_are_stored(photo_dir: Path, repo) -> None:
    write_photo(photo_dir, "a.png", png_bytes(), sidecar={
        "captured_at": "2025-01-08T08:15:00+09:00", "latitude": 37.55, "longitude": 126.92,
        "thermal_vote": "cold", "note": "ignored",
    })

    report = make_service(photo_dir, repo).scan()

    assert report.succeeded == 1
    [record] = repo.list_outfits()
    assert record.file_name == "a.png"
    assert record.captured_at.isoformat() == "2025-01-08T08:15:00+09:00"
    assert (record.latitude, record.longitude) == (37.55, 126.92)
    assert record.effective_vote == "cold"
    assert record.weather is not None and record.weather.is_fixture


def test_missing_photo_dir_raises_without_path(tmp_path: Path, repo) -> None:
    missing = tmp_path / "secret-location" / "photos"
    with pytest.raises(PhotoDirUnavailable) as info:
        make_service(missing, repo).scan()
    assert "secret-location" not in str(info.value)
    assert repo.counts()["outfits"] == 0
