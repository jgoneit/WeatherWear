"""Test helpers for flow A (gallery watching). Imported by ``test_gallery_*`` files only."""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image
from sqlalchemy.engine import Engine

from app.db import create_db_engine
from app.domain import ClothingTags, VisionAnalysis

T0 = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)  # 09:00 Asia/Seoul

KNIT_TAGS = ClothingTags(
    upper_type="knit", outer_type="none", lower_type="jeans",
    colors=["cream"], layers_visible=1, confidence=0.9,
)
OUTFIT = VisionAnalysis(
    photo_type="outfit", photo_type_confidence=0.95, reason="A full outfit is visible.", tags=KNIT_TAGS,
)
LOW_OUTFIT = OUTFIT.model_copy(update={"photo_type_confidence": 0.69})


class FakeClock:
    """Injected clock: ``clock()`` returns the current fake time."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 0, **kwargs: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds, **kwargs)
        return self.now


def make_engine(tmp_path: Path, name: str = "ww.db") -> Engine:
    return create_db_engine(f"sqlite:///{tmp_path / name}")


def png_bytes(color: tuple[int, int, int] = (200, 180, 150), size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes(color: tuple[int, int, int] = (200, 180, 150), size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def write_file(folder: Path, name: str, data: bytes, *, mtime: datetime | None = None) -> Path:
    path = folder / name
    path.write_bytes(data)
    if mtime is not None:
        stamp = mtime.timestamp()
        os.utime(path, (stamp, stamp))
    return path


class GalleryEnv:
    """Store, importer, watcher and confirm service over tmp folders, driven by a FakeClock.

    Built through ``build_gallery_runtime`` with a minimal container, so the
    wiring is the one tasks 7.1 uses.
    """

    def __init__(
        self,
        tmp_path: Path,
        vision,  # noqa: ANN001 - any VisionProvider
        *,
        gallery: Path | None = None,
        poll_seconds: float = 2.0,
        weather_path: Path | None = None,
        engine: Engine | None = None,
    ) -> None:
        from dataclasses import replace
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo

        from app.config import AutomationConfig, resolve_config
        from app.gallery.runtime import build_gallery_runtime
        from app.location import LatLon, LocationService
        from app.providers.weather.fixture import FixtureWeatherProvider
        from app.repository import OutfitRepository

        self.tz = ZoneInfo("Asia/Seoul")
        self.gallery = gallery or tmp_path / "gallery"
        self.gallery.mkdir(parents=True, exist_ok=True)
        self.user = tmp_path / "user" / "photos"
        self.clock = FakeClock()
        self.engine = engine or make_engine(tmp_path)
        self.repo = OutfitRepository(self.engine)
        self.vision = vision
        base, _ = resolve_config({})
        self.config = replace(
            base,
            automation=AutomationConfig(
                gallery_watch_enabled=True, gallery_dir=self.gallery,
                gallery_poll_seconds=poll_seconds, user_photo_dir=self.user,
            ),
        )
        self.location = LocationService(self.engine, LatLon(37.5665, 126.978), clock=self.clock)
        fixture = FixtureWeatherProvider(
            path=weather_path or tmp_path / "missing-weather.json", tz=self.tz, now=self.clock
        )
        container = SimpleNamespace(
            engine=self.engine, repository=self.repo, vision_provider=vision,
            fixture_weather=fixture, location=self.location,
        )
        runtime = build_gallery_runtime(self.config, container, clock=self.clock)  # type: ignore[arg-type]
        self.store = runtime.store
        self.importer = runtime.importer
        self.watcher = runtime.watcher
        self.confirm = runtime.outfit_confirm
        self.poll_seconds = poll_seconds

    def add(self, name: str, data: bytes, *, mtime: datetime | None = None) -> Path:
        return write_file(self.gallery, name, data, mtime=mtime or self.clock() - timedelta(minutes=30))

    def poll(self, times: int = 1):  # noqa: ANN201
        result = None
        for _ in range(times):
            result = self.watcher.poll_once()
            self.clock.advance(self.poll_seconds)
        return result

    def row(self, name: str):  # noqa: ANN201
        from sqlmodel import Session, select

        from app.db_automation import GalleryFile

        with Session(self.engine) as session:
            record = session.exec(select(GalleryFile).where(GalleryFile.file_name == name)).first()
            return None if record is None else self.store.get(record.id)

    def user_files(self) -> list[str]:
        return sorted(p.name for p in self.user.iterdir()) if self.user.exists() else []


class FakeVision:
    """Vision provider double: answers per hash, else ``default``; counts calls."""

    name = "fixture"

    def __init__(self, default: VisionAnalysis | Exception = OUTFIT, by_hash: dict | None = None) -> None:
        self.default = default
        self.by_hash: dict[str, VisionAnalysis | Exception] = dict(by_hash or {})
        self.calls = 0
        self.hashes: list[str] = []

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis:
        self.calls += 1
        self.hashes.append(sha256)
        result = self.by_hash.get(sha256, self.default)
        if isinstance(result, Exception):
            raise result
        return result.model_copy(deep=True)
