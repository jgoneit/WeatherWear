"""Area (a) unit tests: ingestion and duplicate removal (task 6.7).

Requirements 1.4, 1.5, 1.10, 1.11, 1.12, 2.6, 2.7, 2.12, 2.16, 3.8, 17.4(a).

Every test uses a fresh ``tmp_path`` photo folder with tiny generated PNGs
(plain PNGs with location sidecars, or PNGs with embedded UserComment EXIF),
a temporary SQLite file, and the real fixture providers.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import stat
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
from sqlalchemy import text
from sqlmodel import Session, select

from app.db import Feedback, Outfit, create_db_engine
from app.domain import ClothingTags, VisionAnalysis
from app.ingestion.image_check import MAX_IMAGE_BYTES
from app.ingestion.service import ImageIngestionService, PhotoDirUnavailable
from app.providers.weather.fixture import FixtureWeatherProvider
from app.repository import OutfitRepository

TZ = ZoneInfo("Asia/Seoul")

DEFAULT_SIDECAR = {
    "captured_at": "2025-11-20T08:30:00+09:00",
    "latitude": 37.5665,
    "longitude": 126.978,
}


DEFAULT_TAGS = ClothingTags(
    upper_type="t_shirt",
    outer_type="none",
    lower_type="jeans",
    colors=["white", "blue"],
    layers_visible=1,
    confidence=0.9,
)
DEFAULT_ANALYSIS = VisionAnalysis(
    photo_type="outfit",
    photo_type_confidence=0.99,
    reason="A full outfit is visible.",
    tags=DEFAULT_TAGS,
)


class CountingVision:
    """Deterministic vision fake with a call counter and configurable results."""

    def __init__(
        self,
        name: str = "fixture",
        tags: dict[str, ClothingTags] | None = None,
        analyses: dict[str, VisionAnalysis] | None = None,
    ) -> None:
        self.name = name
        self._analyses = dict(analyses or {})
        for sha, value in (tags or {}).items():
            self._analyses[sha] = VisionAnalysis(
                photo_type="outfit",
                photo_type_confidence=0.99,
                reason="A full outfit is visible.",
                tags=value,
            )
        self.calls: list[str] = []

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis:
        self.calls.append(sha256)
        return self._analyses.get(sha256, DEFAULT_ANALYSIS).model_copy(deep=True)


class CountingWeather:
    """Real FixtureWeatherProvider plus a call counter."""

    name = "fixture"

    def __init__(self) -> None:
        self._inner = FixtureWeatherProvider(tz=TZ)
        self.calls = 0

    def get_weather(self, at, latitude, longitude):
        self.calls += 1
        return self._inner.get_weather(at, latitude, longitude)


def png_bytes(color: tuple[int, int, int] = (200, 10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def write_photo(folder: Path, name: str, data: bytes, sidecar: dict | None = None) -> Path:
    path = folder / name
    path.write_bytes(data)
    meta = DEFAULT_SIDECAR if sidecar is None else sidecar
    (folder / f"{Path(name).stem}.metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> OutfitRepository:
    return OutfitRepository(engine)


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def make_service(photo_dir: Path, repo: OutfitRepository, vision=None, weather=None):
    return ImageIngestionService(
        photo_dir,
        vision or CountingVision(),
        weather or CountingWeather(),
        repo,
        TZ,
    )


# -- duplicates (Req 1.4, 1.5) -------------------------------------------------


def test_rescan_same_hash_creates_no_outfit(photo_dir: Path, repo) -> None:
    write_photo(photo_dir, "a.png", png_bytes())
    vision, weather = CountingVision(), CountingWeather()
    service = make_service(photo_dir, repo, vision, weather)

    first = service.scan()
    assert (first.scanned, first.succeeded, first.duplicate, first.failed) == (1, 1, 0, 0)
    counts_after_first = repo.counts()

    second = service.scan()

    assert (second.scanned, second.succeeded, second.duplicate, second.failed) == (1, 0, 1, 0)
    assert repo.counts() == counts_after_first == {
        "outfits": 1, "weather_snapshots": 1, "feedback": 0,
    }
    assert len(vision.calls) == 1  # duplicate calls no provider
    assert weather.calls == 1


def test_same_content_two_names_one_outfit(photo_dir: Path, repo) -> None:
    data = png_bytes((5, 6, 7))
    write_photo(photo_dir, "first.png", data)
    write_photo(photo_dir, "second_copy.png", data)
    vision, weather = CountingVision(), CountingWeather()

    report = make_service(photo_dir, repo, vision, weather).scan()

    assert (report.scanned, report.succeeded, report.duplicate, report.failed) == (2, 1, 1, 0)
    [record] = repo.list_outfits()
    assert record.file_name == "first.png"  # files are processed in name order
    assert len(vision.calls) == 1
    assert weather.calls == 1


# -- transaction rollback (Req 1.10) --------------------------------------------


def test_snapshot_insert_failure_rolls_back_outfit(photo_dir: Path, engine, repo) -> None:
    write_photo(photo_dir, "a.png", png_bytes(), sidecar={**DEFAULT_SIDECAR, "thermal_vote": "hot"})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TRIGGER fail_snapshot BEFORE INSERT ON weather_snapshots "
            "BEGIN SELECT RAISE(ABORT, 'snapshot insert failed'); END;"
        ))
    service = make_service(photo_dir, repo)

    report = service.scan()

    assert (report.succeeded, report.failed) == (0, 1)
    [error] = report.errors
    assert (error.file_name, error.stage, error.code) == ("a.png", "storage", "STORAGE_ERROR")
    assert repo.counts() == {"outfits": 0, "weather_snapshots": 0, "feedback": 0}

    # With the fault removed, the same file is stored on the next scan (Req 1.7).
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER fail_snapshot"))
    retry = service.scan()
    assert (retry.succeeded, retry.duplicate, retry.failed) == (1, 0, 0)
    assert repo.counts() == {"outfits": 1, "weather_snapshots": 1, "feedback": 1}


# -- PHOTO_DIR checks (Req 1.11) -------------------------------------------------


def _missing(tmp_path: Path) -> Path:
    return tmp_path / "hidden-place" / "photos"


def _not_a_dir(tmp_path: Path) -> Path:
    path = tmp_path / "hidden-place.png"
    path.write_bytes(png_bytes())
    return path


def _unreadable(tmp_path: Path) -> Path:
    path = tmp_path / "hidden-place"
    path.mkdir()
    write_photo(path, "a.png", png_bytes())
    path.chmod(0)
    return path


_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.parametrize(
    "make_dir",
    [
        pytest.param(_missing, id="missing"),
        pytest.param(_not_a_dir, id="not_a_dir"),
        pytest.param(
            _unreadable,
            id="unreadable",
            marks=pytest.mark.skipif(_IS_ROOT, reason="root ignores directory permissions"),
        ),
    ],
)
def test_photo_dir_missing_notdir_unreadable_raises(tmp_path: Path, repo, make_dir) -> None:
    target = make_dir(tmp_path)
    vision, weather = CountingVision(), CountingWeather()
    try:
        with pytest.raises(PhotoDirUnavailable) as info:
            make_service(target, repo, vision, weather).scan()
    finally:
        if target.exists():
            target.chmod(stat.S_IRWXU)  # let tmp_path cleanup succeed

    assert info.value.code == "PHOTO_DIR_UNAVAILABLE"
    assert "hidden-place" not in str(info.value)
    assert str(tmp_path) not in str(info.value)
    assert repo.counts() == {"outfits": 0, "weather_snapshots": 0, "feedback": 0}
    assert vision.calls == [] and weather.calls == 0


# -- size limit (Req 1.12) --------------------------------------------------------


def test_file_over_20mb_fails_without_vision(
    photo_dir: Path, repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = write_photo(photo_dir, "big.png", png_bytes())
    with big.open("r+b") as fh:  # sparse: extends the file without writing data
        fh.truncate(MAX_IMAGE_BYTES + 1)
    assert big.stat().st_size == MAX_IMAGE_BYTES + 1
    write_photo(photo_dir, "small.png", png_bytes((1, 1, 1)))

    read_names: list[str] = []
    real_read_bytes = pathlib.Path.read_bytes

    def spy_read_bytes(self: pathlib.Path) -> bytes:
        read_names.append(self.name)
        return real_read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", spy_read_bytes)
    vision, weather = CountingVision(), CountingWeather()

    report = make_service(photo_dir, repo, vision, weather).scan()

    assert (report.scanned, report.succeeded, report.failed) == (2, 1, 1)
    [error] = report.errors
    assert (error.file_name, error.stage, error.code) == ("big.png", "validation", "FILE_TOO_LARGE")
    assert "big.png" not in read_names  # content never read
    assert len(vision.calls) == 1  # only small.png
    assert weather.calls == 1
    assert [r.file_name for r in repo.list_outfits()] == ["small.png"]


# -- stored values (Req 2.6, 2.7, 3.8) --------------------------------------------


def test_seed_vote_saved_as_demo_seed(photo_dir: Path, engine, repo) -> None:
    write_photo(photo_dir, "voted.png", png_bytes((1, 2, 3)),
                sidecar={**DEFAULT_SIDECAR, "thermal_vote": "comfortable"})
    write_photo(photo_dir, "unvoted.png", png_bytes((4, 5, 6)))

    report = make_service(photo_dir, repo).scan()

    assert report.succeeded == 2
    by_name = {r.file_name: r for r in repo.list_outfits()}
    assert by_name["voted.png"].effective_vote == "comfortable"
    assert by_name["unvoted.png"].effective_vote is None
    with Session(engine) as session:
        rows = session.exec(select(Feedback)).all()
    assert len(rows) == 1
    [row] = rows
    assert (row.outfit_id, row.thermal_vote, row.source) == (
        by_name["voted.png"].id, "comfortable", "demo_seed",
    )


def exif_png_bytes(color: tuple[int, int, int], comment: dict) -> bytes:
    """8×8 PNG with a UserComment JSON in EXIF, like the ex_img demo photos."""
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9286] = b"ASCII\x00\x00\x00" + json.dumps(comment).encode()
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG", exif=exif.tobytes())
    return buf.getvalue()


def test_source_is_exif_for_embedded_metadata(photo_dir: Path, engine, repo) -> None:
    comment = {
        "date": "2025-01-08", "time": "08:15:00", "utc_offset": "+09:00",
        "latitude": 37.556, "longitude": 126.924, "synthetic": True,
        "outfit": "코트", "location": "서울",
    }
    (photo_dir / "embedded.png").write_bytes(exif_png_bytes((1, 2, 3), comment))
    # Vote-only sidecar: its location is not needed when EXIF is valid (Req 2.16).
    (photo_dir / "embedded.metadata.json").write_text(
        json.dumps({"thermal_vote": "hot"}), encoding="utf-8"
    )
    real = {**comment, "synthetic": False}
    (photo_dir / "real.png").write_bytes(exif_png_bytes((4, 5, 6), real))  # no sidecar

    report = make_service(photo_dir, repo).scan()

    assert (report.succeeded, report.failed) == (2, 0)
    by_name = {r.file_name: r for r in repo.list_outfits()}
    embedded = by_name["embedded.png"]
    assert embedded.source == "exif"
    assert embedded.metadata_synthetic is True
    assert embedded.captured_at.isoformat() == "2025-01-08T08:15:00+09:00"
    assert (embedded.latitude, embedded.longitude) == (37.556, 126.924)
    assert embedded.effective_vote == "hot"
    assert by_name["real.png"].source == "exif"
    assert by_name["real.png"].metadata_synthetic is False
    assert by_name["real.png"].effective_vote is None
    with Session(engine) as session:
        assert set(session.exec(select(Outfit.source)).all()) == {"exif"}


def test_source_is_demo_sidecar_for_sidecar_fallback(photo_dir: Path, engine, repo) -> None:
    write_photo(photo_dir, "a.png", png_bytes((1, 2, 3)))  # no EXIF, sidecar has location
    write_photo(photo_dir, "b.png", png_bytes((4, 5, 6)))

    make_service(photo_dir, repo).scan()

    records = repo.list_outfits()
    assert len(records) == 2
    assert {r.source for r in records} == {"demo_sidecar"}
    assert all(r.metadata_synthetic for r in records)
    with Session(engine) as session:
        assert set(session.exec(select(Outfit.source)).all()) == {"demo_sidecar"}


@pytest.mark.parametrize("provider_name", ["fixture", "gemini"])
def test_vision_provider_stored(photo_dir: Path, repo, provider_name: str) -> None:
    data = png_bytes((9, 9, 9))
    write_photo(photo_dir, "a.png", data)
    tags = ClothingTags(
        upper_type="knit", outer_type="coat", lower_type="slacks",
        colors=["navy", "cream"], layers_visible=2, confidence=0.9,
    )
    vision = CountingVision(name=provider_name, tags={hashlib.sha256(data).hexdigest(): tags})

    make_service(photo_dir, repo, vision).scan()

    [record] = repo.list_outfits()
    assert record.vision_provider == provider_name
    assert record.tags == tags
