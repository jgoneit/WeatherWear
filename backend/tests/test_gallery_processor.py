"""GalleryImportService (tasks 3.11, Req 2.6, 2.7, 3.1–3.4, 3.6, 4.1, 5.3, 6.1, 6.2, 6.5–6.7, 6.10)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from app.db import Feedback, Outfit, WeatherSnapshot
from app.domain import UNKNOWN_TAGS, VisionAnalysis
from app.gallery.processor import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    GalleryImportService,
    failure_transition,
)
from app.gallery.rules import Signature, StableEvent
from app.gallery.store import GalleryFileRow, GalleryStore, gallery_key
from app.ingestion.service import ImageIngestionService
from app.location import LatLon, LocationService
from app.providers.vision.base import VisionError
from app.providers.weather.fixture import FixtureWeatherProvider
from app.repository import OutfitRepository
from tests.gallery_support import (
    LOW_OUTFIT,
    OUTFIT,
    T0,
    FakeClock,
    FakeVision,
    make_engine,
    jpeg_bytes,
    png_bytes,
    write_file,
)

TZ = ZoneInfo("Asia/Seoul")
NON_OUTFIT = VisionAnalysis(photo_type="non_outfit", photo_type_confidence=0.97, reason="A plate of food.",
                            tags=UNKNOWN_TAGS)
UNCERTAIN = VisionAnalysis(photo_type="uncertain", photo_type_confidence=0.3, reason="Too dark to tell.",
                           tags=UNKNOWN_TAGS)


class Env:
    def __init__(self, tmp_path: Path, vision: FakeVision) -> None:
        self.gallery = tmp_path / "gallery"
        self.user = tmp_path / "user" / "photos"
        self.gallery.mkdir()
        self.engine = make_engine(tmp_path)
        self.store = GalleryStore(self.engine)
        self.repo = OutfitRepository(self.engine)
        self.vision = vision
        self.clock = FakeClock()
        self.location = LocationService(self.engine, LatLon(37.5665, 126.978), clock=self.clock)
        weather = FixtureWeatherProvider(path=tmp_path / "missing-weather.json", tz=TZ)
        ingestion = ImageIngestionService(self.user, vision, weather, self.repo, TZ)
        self.importer = GalleryImportService(
            self.gallery, self.user, self.store, self.repo, ingestion, self.location, TZ
        )
        self.key = gallery_key(self.gallery)

    def add(self, name: str, data: bytes, *, mtime: datetime | None = None) -> Path:
        return write_file(self.gallery, name, data, mtime=mtime or T0 - timedelta(hours=1))

    def claimed(self, name: str) -> GalleryFileRow:
        st = os.stat(self.gallery / name)
        event = StableEvent(name, Signature(st.st_size, st.st_mtime_ns), self.clock())
        row = self.store.upsert_version(self.key, event, self.clock()) or next(
            r for r in self.store.due_rows(self.key, self.clock()) if r.file_name == name
        )
        assert self.store.claim(row.id, self.clock())
        return self.store.get(row.id)

    def run(self, name: str):
        row = self.claimed(name)
        outcome = self.importer.process(row, self.clock())
        self.store.finish(row.id, outcome, self.clock())
        return outcome, self.store.get(row.id)

    def counts(self) -> dict[str, int]:
        with Session(self.engine) as session:
            return {
                model.__tablename__: session.exec(select(func.count()).select_from(model)).one()
                for model in (Outfit, WeatherSnapshot, Feedback)
            }

    def user_files(self) -> list[str]:
        return sorted(p.name for p in self.user.iterdir()) if self.user.exists() else []


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path, FakeVision(OUTFIT))


def test_failure_transition_schedule() -> None:
    assert failure_transition("VISION_TIMEOUT", 1, T0) == ("retry_wait", T0 + RETRY_DELAYS[0])
    assert failure_transition("VISION_TIMEOUT", 2, T0) == ("retry_wait", T0 + timedelta(minutes=5))
    assert failure_transition("VISION_TIMEOUT", MAX_ATTEMPTS, T0) == ("failed", None)
    assert failure_transition("INVALID_IMAGE_FORMAT", 1, T0) == ("failed", None)
    assert failure_transition("FILE_TOO_LARGE", 1, T0) == ("failed", None)
    assert failure_transition("GALLERY_FILE_MISSING", 1, T0) == ("failed", None)


def test_outfit_imported_with_user_photo(env: Env) -> None:
    data = png_bytes((10, 20, 30))
    env.add("capture.png", data)
    outcome, row = env.run("capture.png")

    sha = hashlib.sha256(data).hexdigest()
    assert outcome.status == "imported" and row.status == "imported"
    assert row.outfit_id is not None and row.sha256 == sha and row.attempts == 1
    assert (row.photo_type, row.photo_type_confidence) == ("outfit", 0.95)
    assert row.capture_origin == "estimated"
    assert env.store.user_photo_file(sha) == "capture.png"
    assert (env.user / "capture.png").read_bytes() == data  # byte for byte (Req 3.1)
    sidecar = json.loads((env.user / "capture.metadata.json").read_text(encoding="utf-8"))
    assert sidecar["estimated"] is True and sidecar["time_source"] == "file_mtime"
    record = env.repo.get_outfit(row.outfit_id)
    assert record.file_name == "capture.png" and record.source == "demo_sidecar"
    assert record.metadata_synthetic is True
    assert record.captured_at == (T0 - timedelta(hours=1)).astimezone(TZ)
    assert env.vision.calls == 1
    assert env.counts() == {"outfits": 1, "weather_snapshots": 1, "feedback": 0}
    assert [r.outfit_id for r in env.repo.list_candidates()] == [row.outfit_id]  # Req 3.6
    assert not any(p.name.endswith(".metadata.json") for p in env.gallery.iterdir())  # Req 5.4


def test_non_outfit_skipped_copy_removed(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeVision(NON_OUTFIT))
    env.add("food.png", png_bytes((1, 1, 1)), mtime=T0)
    outcome, row = env.run("food.png")
    assert outcome.status == "skipped_non_outfit" == row.status
    assert (row.photo_type, row.photo_type_confidence, row.reason) == ("non_outfit", 0.97, "A plate of food.")
    assert env.user_files() == []
    assert env.store.user_photo_file(row.sha256) is None
    assert env.counts()["outfits"] == 0


def test_uncertain_kept_out_and_listed(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeVision(UNCERTAIN))
    env.add("dark.png", png_bytes((2, 2, 2)))
    outcome, row = env.run("dark.png")
    assert row.status == "uncertain" and row.photo_type == "uncertain" and row.reason == "Too dark to tell."
    assert env.user_files() == [] and env.counts()["outfits"] == 0
    assert [r.id for r in env.store.list_imports(20, "uncertain")] == [row.id]


def test_low_confidence_outfit_is_uncertain(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeVision(LOW_OUTFIT))
    env.add("blurry.png", png_bytes((3, 3, 3)))
    _, row = env.run("blurry.png")
    assert row.status == "uncertain" and row.photo_type == "uncertain"
    assert row.photo_type_confidence == 0.69
    assert env.counts()["outfits"] == 0 and env.user_files() == []


def test_known_non_outfit_hash_skips_vision(env: Env) -> None:
    data = png_bytes((4, 4, 4))
    env.repo.save_photo_analysis(hashlib.sha256(data).hexdigest(), "old.png", NON_OUTFIT, "fixture")
    env.add("again.png", data)
    _, row = env.run("again.png")
    assert row.status == "skipped_non_outfit" and row.photo_type == "non_outfit"
    assert env.vision.calls == 0 and env.user_files() == []


def test_same_bytes_two_names_one_outfit(env: Env) -> None:
    data = png_bytes((5, 5, 5))
    env.add("a.png", data)
    env.add("b.png", data)
    _, first = env.run("a.png")
    _, second = env.run("b.png")
    assert first.status == "imported" and second.status == "duplicate"
    assert env.vision.calls == 1 and env.counts()["outfits"] == 1
    assert env.user_files() == ["a.metadata.json", "a.png"]


def test_in_flight_same_bytes_is_duplicate(env: Env) -> None:
    data = png_bytes((6, 6, 6))
    env.add("a.png", data)
    env.add("b.png", data)
    first = env.claimed("a.png")
    env.store.record_sha(first.id, hashlib.sha256(data).hexdigest(), env.clock())
    outcome, _ = env.run("b.png")
    assert outcome.status == "duplicate" and env.vision.calls == 0


def test_name_collision_uses_sha8_suffix(env: Env) -> None:
    env.user.mkdir(parents=True)
    (env.user / "x.png").write_bytes(b"someone else")
    data = png_bytes((7, 7, 7))
    env.add("x.png", data)
    _, row = env.run("x.png")
    sha = hashlib.sha256(data).hexdigest()
    assert row.status == "imported"
    assert env.store.user_photo_file(sha) == f"x_{sha[:8]}.png"
    assert (env.user / "x.png").read_bytes() == b"someone else"  # never overwritten (Req 3.2)
    assert (env.user / f"x_{sha[:8]}.png").read_bytes() == data
    assert env.repo.get_outfit(row.outfit_id).file_name == f"x_{sha[:8]}.png"


def test_same_stem_other_extension_gets_own_sidecar(env: Env) -> None:
    # a.png and a.jpg would share a.metadata.json; the second copy gets its own name.
    env.add("a.png", png_bytes((8, 8, 8)))
    _, first = env.run("a.png")
    jpg = jpeg_bytes((9, 9, 9))
    env.add("a.jpg", jpg)
    _, second = env.run("a.jpg")
    sha = hashlib.sha256(jpg).hexdigest()
    assert first.status == second.status == "imported"
    assert env.store.user_photo_file(sha) == f"a_{sha[:8]}.jpg"
    assert (env.user / f"a_{sha[:8]}.metadata.json").exists()
    assert (env.user / "a.metadata.json").exists()


def test_vision_timeout_retries_after_1_then_5_minutes(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeVision(VisionError("timeout")))
    data = png_bytes((11, 11, 11))
    env.add("slow.png", data)

    _, row = env.run("slow.png")
    assert (row.status, row.attempts, row.last_error_stage, row.last_error_code) == (
        "retry_wait", 1, "analysis", "VISION_TIMEOUT")
    assert row.next_attempt_at == env.clock() + timedelta(minutes=1)
    assert env.user_files() == ["slow.metadata.json", "slow.png"]  # kept for the next try (Req 3.7)
    assert env.store.due_rows(env.key, env.clock() + timedelta(seconds=59)) == []

    env.clock.advance(minutes=1)
    _, row = env.run("slow.png")
    assert (row.status, row.attempts) == ("retry_wait", 2)
    assert row.next_attempt_at == env.clock() + timedelta(minutes=5)

    env.clock.advance(minutes=5)
    env.vision.default = OUTFIT  # the provider recovers on the third attempt
    _, row = env.run("slow.png")
    assert (row.status, row.attempts, row.last_error_code) == ("imported", 3, None)
    assert env.vision.calls == 3


def test_third_failure_is_final(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeVision(VisionError("api_error")))
    env.add("broken.png", png_bytes((12, 12, 12)))
    for _ in range(3):
        _, row = env.run("broken.png")
        env.clock.advance(minutes=10)
    assert (row.status, row.attempts, row.last_error_code, row.next_attempt_at) == (
        "failed", 3, "VISION_API_ERROR", None)
    assert env.vision.calls == 3 and env.user_files() == []
    assert env.store.user_photo_file(row.sha256) is None
    assert env.store.due_rows(env.key, env.clock() + timedelta(days=1)) == []  # no fourth call (Req 6.11)


def test_invalid_format_not_retried(env: Env) -> None:
    env.add("garbage.jpg", b"definitely not a jpeg")
    _, row = env.run("garbage.jpg")
    assert (row.status, row.attempts, row.last_error_stage, row.last_error_code) == (
        "failed", 1, "validation", "INVALID_IMAGE_FORMAT")
    assert env.vision.calls == 0 and env.user_files() == []


def test_failed_attempt_leaves_no_rows(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeVision(VisionError("schema_invalid")))
    env.add("x.png", png_bytes((13, 13, 13)))
    _, row = env.run("x.png")
    assert row.status == "retry_wait"
    assert env.counts() == {"outfits": 0, "weather_snapshots": 0, "feedback": 0}


def test_missing_file_is_final_and_changed_file_is_deferred(env: Env) -> None:
    path = env.add("gone.png", png_bytes((14, 14, 14)))
    row = env.claimed("gone.png")
    path.unlink()
    outcome = env.importer.process(row, env.clock())
    assert (outcome.status, outcome.code) == ("failed", "GALLERY_FILE_MISSING")

    path = env.add("moving.png", png_bytes((15, 15, 15)))
    row = env.claimed("moving.png")
    path.write_bytes(png_bytes((15, 15, 15)) + b"more")
    outcome = env.importer.process(row, env.clock())
    assert outcome.status == "deferred"
    env.store.finish(row.id, outcome, env.clock())
    released = env.store.get(row.id)
    assert (released.status, released.attempts) == ("pending", 0)
    assert env.vision.calls == 0


def test_valid_exif_photo_is_not_estimated(env: Env) -> None:
    from tests.test_gallery_estimate import jpeg_with_exif

    data = jpeg_with_exif(when="2026:11:13 08:30:00", offset="+09:00", gps=(37.55, 126.92))
    env.add("exif.jpg", data)
    _, row = env.run("exif.jpg")
    assert row.status == "imported" and row.capture_origin == "exif"
    assert env.user_files() == ["exif.jpg"]
    record = env.repo.get_outfit(row.outfit_id)
    assert record.source == "exif"
    assert record.captured_at == datetime(2026, 11, 12, 23, 30, tzinfo=UTC)


def test_last_location_used_for_estimate(env: Env) -> None:
    env.location.report(35.1796, 129.0756, 25.0)
    env.add("busan.png", png_bytes((16, 16, 16)))
    _, row = env.run("busan.png")
    record = env.repo.get_outfit(row.outfit_id)
    assert (record.latitude, record.longitude) == (35.18, 129.08)


def test_storage_exception_is_retryable(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.add("x.png", png_bytes((17, 17, 17)))

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(env.repo, "exists_sha", boom)
    _, row = env.run("x.png")
    assert (row.status, row.last_error_stage, row.last_error_code) == ("retry_wait", "storage", "STORAGE_ERROR")
