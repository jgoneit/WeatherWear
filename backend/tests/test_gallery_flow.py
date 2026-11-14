"""Flow A integration examples (tasks 3.19, Req 20.2, 20.3 (a)–(g), 4.1, 4.4, 1.3).

Real GalleryStore, GalleryImportService and GalleryWatcher over tmp folders.
No threads: ``poll_once()`` and a FakeClock only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from app.config import AutomationConfig, resolve_config
from app.domain import UNKNOWN_TAGS, VisionAnalysis
from app.gallery.runtime import build_gallery_runtime
from app.gallery.simulate import add_overlay_entry_atomic, encode_with_exif, overlay_entry
from app.ingestion.service import ImageIngestionService
from app.main import create_app
from app.providers.vision.base import VisionError
from app.providers.vision.fixture import (
    DEFAULT_VISION_FIXTURE_PATH,
    FixtureVisionProvider,
    load_vision_fixture,
)
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.forecast import FORECAST_ENDPOINT
from app.providers.weather.open_meteo import ARCHIVE_URL
from app.repository import OutfitRepository
from tests.gallery_support import KNIT_TAGS, OUTFIT, FakeClock, FakeVision, GalleryEnv, png_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO = REPO_ROOT / "data" / "demo"
TZ = ZoneInfo("Asia/Seoul")
KST = timezone(timedelta(hours=9))


class CountingFixtureVision(FixtureVisionProvider):
    """The real fixture provider (demo map + overlay) that counts calls."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.calls = 0

    def analyze(self, image: bytes, mime_type: str, sha256: str | None = None) -> VisionAnalysis:
        self.calls += 1
        return super().analyze(image, mime_type, sha256)


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def demo_photos(count: int) -> list[Path]:
    photos = sorted((DEMO / "photos").glob("*.png"))[:count] if (DEMO / "photos").is_dir() else []
    if len(photos) < count:
        pytest.skip("demo photos not prepared")
    return photos


def copy_demo_tree(target: Path, photos: list[Path]) -> Path:
    """A tmp copy of Photo_Dir (a few photos + sidecars), the manifest and the fixtures."""
    (target / "photos").mkdir(parents=True)
    for photo in photos:
        shutil.copy2(photo, target / "photos" / photo.name)
        sidecar = photo.with_name(photo.stem + ".metadata.json")
        if sidecar.exists():
            shutil.copy2(sidecar, target / "photos" / sidecar.name)
    shutil.copy2(DEMO / "photos.manifest.json", target / "photos.manifest.json")
    shutil.copytree(DEMO / "fixtures", target / "fixtures")
    return target


def test_demo_dirs_unchanged_after_imports(tmp_path: Path) -> None:
    demo = copy_demo_tree(tmp_path / "demo", demo_photos(2))
    before = tree_digest(demo)
    outfit_bytes, food_bytes, dark_bytes, broken_bytes = (png_bytes((i, 0, 0)) for i in (1, 2, 3, 4))
    vision = FakeVision(OUTFIT, by_hash={
        hashlib.sha256(food_bytes).hexdigest(): VisionAnalysis(
            photo_type="non_outfit", photo_type_confidence=0.9, reason="food", tags=UNKNOWN_TAGS),
        hashlib.sha256(dark_bytes).hexdigest(): VisionAnalysis(
            photo_type="uncertain", photo_type_confidence=0.2, reason="dark", tags=UNKNOWN_TAGS),
        hashlib.sha256(broken_bytes).hexdigest(): VisionError("timeout"),
    })
    env = GalleryEnv(tmp_path, vision)
    env.poll()
    for name, data in (("o.png", outfit_bytes), ("f.png", food_bytes), ("d.png", dark_bytes),
                       ("b.png", broken_bytes)):
        env.add(name, data)
    gallery_before = tree_digest(env.gallery)
    env.poll(2)
    for _ in range(3):  # let the timeout file use up its retries
        env.clock.advance(minutes=6)
        env.poll()

    statuses = {name: env.row(name).status for name in ("o.png", "f.png", "d.png", "b.png")}
    assert statuses == {"o.png": "imported", "f.png": "skipped_non_outfit", "d.png": "uncertain",
                        "b.png": "failed"}
    assert tree_digest(demo) == before  # Photo_Dir, manifest, fixtures untouched (Req 4.1)
    assert tree_digest(env.gallery) == gallery_before  # the watcher never writes Gallery_Dir (Req 1.6)
    assert env.user_files() == ["o.metadata.json", "o.png"]  # only imported copies remain (Req 3.7)
    manifest = json.loads((demo / "photos.manifest.json").read_text(encoding="utf-8"))
    assert all(entry["file"] != "o.png" for entry in manifest["photos"])  # not demo data (Req 4.3)


def test_demo_photo_bytes_in_gallery_is_duplicate(tmp_path: Path) -> None:
    demo_map = load_vision_fixture(DEFAULT_VISION_FIXTURE_PATH)
    def is_outfit(photo: Path) -> bool:
        analysis = demo_map.get(hashlib.sha256(photo.read_bytes()).hexdigest())
        return analysis is not None and analysis.photo_type == "outfit"

    photos = [p for p in demo_photos(12) if is_outfit(p)][:1]
    if not photos:
        pytest.skip("no outfit demo photo found")
    demo = copy_demo_tree(tmp_path / "demo", photos)
    vision = CountingFixtureVision(demo_map, overlay_path=tmp_path / "user" / "fixtures" / "vision.overlay.json")
    env = GalleryEnv(tmp_path, vision)
    report = ImageIngestionService(demo / "photos", vision, FixtureWeatherProvider(tz=TZ), env.repo, TZ).scan()
    assert report.succeeded == 1
    calls = vision.calls

    env.poll()
    env.add("from_phone.png", photos[0].read_bytes())
    env.poll(2)
    row = env.row("from_phone.png")
    assert row.status == "duplicate" and row.outfit_id is None
    assert vision.calls == calls  # no Vision call for a duplicate (Req 2.6)
    assert env.repo.counts()["outfits"] == 1 and env.user_files() == []


def load_simulator():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("simulate_capture", REPO_ROOT / "scripts" / "simulate_capture.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_overlay_written_before_file_imports_as_outfit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay = tmp_path / "user" / "fixtures" / "vision.overlay.json"
    vision = CountingFixtureVision(load_vision_fixture(DEFAULT_VISION_FIXTURE_PATH), overlay_path=overlay)
    env = GalleryEnv(tmp_path, vision)
    env.poll()  # baseline of the empty gallery

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'sim-readonly.db'}")
    monkeypatch.setenv("GALLERY_DIR", str(env.gallery))
    monkeypatch.setenv("USER_PHOTO_DIR", str(env.user))
    monkeypatch.setenv("VISION_PROVIDER", "fixture")
    assert load_simulator().main(["--at", "2026-11-14T08:10:00+09:00", "--env-file", str(tmp_path / "x.env")]) == 0
    name = capsys.readouterr().out.strip()
    assert name == "capture_20261114_081000.jpg"

    env.poll()
    result = env.poll()
    assert result.processed == [(name, "imported")]
    row = env.row(name)
    assert row.capture_origin == "exif" and row.photo_type == "outfit"
    record = env.repo.get_outfit(row.outfit_id)
    assert record.tags == KNIT_TAGS and record.vision_provider == "fixture"  # overlay, labeled fixture
    assert record.captured_at == datetime(2026, 11, 14, 8, 10, tzinfo=KST) and record.source == "exif"
    assert vision.calls == 1


def test_fixture_unknown_hash_without_overlay_stays_uncertain(tmp_path: Path) -> None:
    vision = CountingFixtureVision({}, overlay_path=tmp_path / "user" / "fixtures" / "vision.overlay.json")
    env = GalleryEnv(tmp_path, vision)
    env.poll()
    env.add("new.png", png_bytes((5, 5, 5)))
    env.poll(2)
    assert env.row("new.png").status == "uncertain" and env.repo.counts()["outfits"] == 0


def test_watch_disabled_reads_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    (gallery / "photo.png").write_bytes(png_bytes((6, 6, 6)))
    touched: list[str] = []
    real_scandir, real_stat, real_listdir = os.scandir, os.stat, os.listdir

    def guard(original):  # noqa: ANN001, ANN202
        def wrapper(path=".", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            if str(gallery) in os.fspath(path):
                touched.append(os.fspath(path))
            return original(path, *args, **kwargs)
        return wrapper

    monkeypatch.setattr(os, "scandir", guard(real_scandir))
    monkeypatch.setattr(os, "stat", guard(real_stat))
    monkeypatch.setattr(os, "listdir", guard(real_listdir))

    config, _ = resolve_config({"DATABASE_URL": f"sqlite:///{tmp_path / 'off.db'}", "PHOTO_DIR": str(tmp_path / "p")})
    config = replace(config, automation=AutomationConfig(gallery_dir=gallery, user_photo_dir=tmp_path / "u" / "p"))
    assert config.automation.gallery_watch_enabled is False  # the default (Req 1.3)
    app = create_app(config)
    with TestClient(app) as client:
        client.get("/api/meta")
        runtime = build_gallery_runtime(app.state.config, app.state.container)
    assert runtime.watcher is None
    assert touched == []
    from app.gallery.store import GalleryStore
    assert GalleryStore(app.state.container.engine).list_imports(100) == []


def test_restart_does_not_redo_baseline_or_versions(tmp_path: Path) -> None:
    from tests.gallery_support import make_engine
    engine = make_engine(tmp_path)
    first = GalleryEnv(tmp_path, FakeVision(OUTFIT), engine=engine)
    first.add("old.png", png_bytes((7, 7, 7)))
    first.poll()
    first.add("new.png", png_bytes((8, 8, 8)))
    first.poll(2)
    second = GalleryEnv(tmp_path, FakeVision(OUTFIT), engine=engine)
    second.clock.now = first.clock.now
    second.poll(4)
    assert first.vision.calls == 1 and second.vision.calls == 0
    assert (second.row("old.png").status, second.row("new.png").status) == ("baseline", "imported")


def recent_jpeg(at: datetime, color: tuple[int, int, int]) -> bytes:
    return encode_with_exif(Image.new("RGB", (8, 8), color), "JPEG", at, 37.57, 126.98, "Asia/Seoul")


def hourly(day: date, temp: float) -> dict:
    times = [f"{day.isoformat()}T{h:02d}:00" for h in range(24)]
    return {"hourly": {"time": times, "temperature_2m": [temp] * 24, "precipitation": [0.0] * 24,
                       "relative_humidity_2m": [50] * 24}}


@respx.mock
def test_demo_scan_weather_unchanged(tmp_path: Path) -> None:
    today = datetime.now(TZ).replace(microsecond=0)
    photo_dir = tmp_path / "demo" / "photos"
    photo_dir.mkdir(parents=True)
    scan_bytes = recent_jpeg(today - timedelta(hours=1), (9, 9, 9))
    (photo_dir / "recent_demo.jpg").write_bytes(scan_bytes)
    gallery_bytes = recent_jpeg(today - timedelta(hours=2), (10, 10, 10))

    config, _ = resolve_config({
        "DATABASE_URL": f"sqlite:///{tmp_path / 'live.db'}", "PHOTO_DIR": str(photo_dir),
        "WEATHER_PROVIDER": "open_meteo",
    })
    config = replace(config, automation=AutomationConfig(
        gallery_watch_enabled=True, gallery_dir=tmp_path / "gallery", user_photo_dir=tmp_path / "user" / "photos"))
    overlay = config.automation.vision_overlay_path
    for data in (scan_bytes, gallery_bytes):
        add_overlay_entry_atomic(overlay, hashlib.sha256(data).hexdigest(), overlay_entry(KNIT_TAGS))

    archive = respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, json=hourly(today.date(), 7.0)))
    forecast = respx.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=hourly(today.date(), 8.0)))

    app = create_app(config)
    with TestClient(app) as client:
        report = client.post("/api/demo/scan").json()
        assert report["succeeded"] == 1
        # Phase 1 demo scan: Archive only, even for a photo taken today (Req 7.5).
        assert (archive.call_count, forecast.call_count) == (1, 0)

        container = app.state.container
        clock = FakeClock(datetime.now(UTC))
        runtime = build_gallery_runtime(app.state.config, container, clock=clock)
        gallery = app.state.config.automation.gallery_dir
        gallery.mkdir(parents=True)
        runtime.watcher.poll_once()  # baseline
        (gallery / "now.jpg").write_bytes(gallery_bytes)
        runtime.watcher.poll_once()
        clock.advance(runtime.watcher.poll_seconds)
        result = runtime.watcher.poll_once()
        assert result.processed == [("now.jpg", "imported")]
        assert (archive.call_count, forecast.call_count) == (1, 1)  # gallery import: Forecast (Req 7.1)

        repo: OutfitRepository = container.repository
        temps = sorted(r.weather.actual_temperature_c for r in repo.list_outfits())
        assert temps == [7.0, 8.0]


def test_runtime_wiring_with_container_namespace(tmp_path: Path) -> None:
    # build_gallery_runtime only needs these container attributes (tasks 7.1).
    base, _ = resolve_config({})
    config = replace(base, automation=AutomationConfig(gallery_watch_enabled=True, gallery_dir=tmp_path / "g",
                                                       user_photo_dir=tmp_path / "u" / "p",
                                                       gallery_poll_seconds=0.5))
    from tests.gallery_support import make_engine
    engine = make_engine(tmp_path)
    container = SimpleNamespace(engine=engine, repository=OutfitRepository(engine), vision_provider=FakeVision(),
                                fixture_weather=FixtureWeatherProvider(tz=TZ), location=None)
    runtime = build_gallery_runtime(config, container)  # type: ignore[arg-type]
    assert runtime.watcher is not None and runtime.watcher.poll_seconds == 0.5
    assert runtime.importer.user_photo_dir == tmp_path / "u" / "p"
