"""Fixture Vision overlay (tasks 3.7, Req 6.3, 6.4, 4.6)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import AutomationConfig, resolve_config
from app.db import create_db_engine
from app.domain import UNKNOWN_ANALYSIS, VisionAnalysis
from app.ingestion.service import ImageIngestionService
from app.providers.vision.factory import build_vision_provider
from app.providers.vision.fixture import (
    DEFAULT_VISION_FIXTURE_PATH,
    REPO_ROOT,
    FixtureVisionProvider,
    load_vision_fixture,
)
from app.providers.weather.fixture import FixtureWeatherProvider
from app.repository import OutfitRepository
from tests.gallery_support import KNIT_TAGS, OUTFIT

DEMO_PHOTO_DIR = REPO_ROOT / "data" / "demo" / "photos"
SHA_NEW = "1" * 64
SHA_DEMO = "2" * 64
DEMO_ANALYSIS = OUTFIT.model_copy(update={"reason": "demo fixture"})
OVERLAY_ANALYSIS = OUTFIT.model_copy(update={"reason": "simulate_capture fixture"})


def write_overlay(path: Path, payload: object, *, bump_ns: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    if bump_ns:
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + bump_ns))


def dump(analysis: VisionAnalysis) -> dict:
    return analysis.model_dump()


def test_fixture_unknown_hash_uncertain_without_overlay(tmp_path: Path) -> None:
    provider = FixtureVisionProvider({SHA_DEMO: DEMO_ANALYSIS}, overlay_path=tmp_path / "missing.json")
    assert provider.analyze(b"x", "image/png", SHA_NEW) == UNKNOWN_ANALYSIS
    assert provider.analyze(b"x", "image/png", SHA_NEW).photo_type == "uncertain"
    assert provider.analyze(b"x", "image/png", SHA_DEMO) == DEMO_ANALYSIS


def test_overlay_wins_over_demo_fixture(tmp_path: Path) -> None:
    overlay = tmp_path / "vision.overlay.json"
    write_overlay(overlay, {SHA_NEW: dump(OVERLAY_ANALYSIS), SHA_DEMO: dump(OVERLAY_ANALYSIS)})
    provider = FixtureVisionProvider({SHA_DEMO: DEMO_ANALYSIS}, overlay_path=overlay)
    assert provider.analyze(b"x", "image/png", SHA_NEW) == OVERLAY_ANALYSIS
    assert provider.analyze(b"x", "image/png", SHA_DEMO) == OVERLAY_ANALYSIS


def test_overlay_reload_on_change(tmp_path: Path) -> None:
    overlay = tmp_path / "vision.overlay.json"
    provider = FixtureVisionProvider({}, overlay_path=overlay)
    assert provider.analyze(b"x", "image/png", SHA_NEW) == UNKNOWN_ANALYSIS  # created later

    write_overlay(overlay, {SHA_NEW: dump(OVERLAY_ANALYSIS)})
    assert provider.analyze(b"x", "image/png", SHA_NEW) == OVERLAY_ANALYSIS

    other = OVERLAY_ANALYSIS.model_copy(update={"tags": KNIT_TAGS.model_copy(update={"colors": ["navy"]})})
    write_overlay(overlay, {SHA_NEW: dump(other)}, bump_ns=1_000_000)
    assert provider.analyze(b"x", "image/png", SHA_NEW) == other

    overlay.unlink()
    assert provider.analyze(b"x", "image/png", SHA_NEW) == UNKNOWN_ANALYSIS


def test_broken_overlay_ignored_with_one_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    overlay = tmp_path / "vision.overlay.json"
    write_overlay(overlay, "{not json")
    provider = FixtureVisionProvider({SHA_DEMO: DEMO_ANALYSIS}, overlay_path=overlay)
    with caplog.at_level(logging.WARNING, logger="app.providers.vision.fixture"):
        for _ in range(5):
            assert provider.analyze(b"x", "image/png", SHA_DEMO) == DEMO_ANALYSIS
            assert provider.analyze(b"x", "image/png", SHA_NEW) == UNKNOWN_ANALYSIS
    warnings = [r for r in caplog.records if "overlay" in r.getMessage()]
    assert len(warnings) == 1
    assert str(tmp_path) not in warnings[0].getMessage()  # file name only

    # A new broken version warns once more; a repaired file is used again.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.providers.vision.fixture"):
        write_overlay(overlay, "[1, 2, 3]", bump_ns=1_000_000)
        provider.analyze(b"x", "image/png", SHA_NEW)
        provider.analyze(b"x", "image/png", SHA_NEW)
    assert len([r for r in caplog.records if "overlay" in r.getMessage()]) == 1
    write_overlay(overlay, {SHA_NEW: dump(OVERLAY_ANALYSIS)}, bump_ns=2_000_000)
    assert provider.analyze(b"x", "image/png", SHA_NEW) == OVERLAY_ANALYSIS


def test_invalid_overlay_entries_skipped(tmp_path: Path) -> None:
    overlay = tmp_path / "vision.overlay.json"
    write_overlay(overlay, {"not-a-sha": dump(OVERLAY_ANALYSIS), SHA_NEW: {"photo_type": "outfit"}})
    provider = FixtureVisionProvider({}, overlay_path=overlay)
    assert provider.analyze(b"x", "image/png", SHA_NEW) == UNKNOWN_ANALYSIS


def test_factory_passes_overlay_path(tmp_path: Path) -> None:
    cfg, _ = resolve_config({})
    cfg = replace(cfg, automation=AutomationConfig(user_photo_dir=tmp_path / "user" / "photos"))
    assert cfg.automation.vision_overlay_path == tmp_path / "user" / "fixtures" / "vision.overlay.json"
    write_overlay(cfg.automation.vision_overlay_path, {SHA_NEW: dump(OVERLAY_ANALYSIS)})
    demo = tmp_path / "vision.json"
    demo.write_text(json.dumps({SHA_DEMO: dump(DEMO_ANALYSIS)}), encoding="utf-8")
    provider = build_vision_provider(cfg, fixture_path=demo)
    assert isinstance(provider, FixtureVisionProvider)
    assert provider.analyze(b"", "image/png", SHA_NEW) == OVERLAY_ANALYSIS
    assert provider.analyze(b"", "image/png", SHA_DEMO) == DEMO_ANALYSIS


def test_demo_scan_results_unchanged_with_overlay(tmp_path: Path) -> None:
    demo_map = load_vision_fixture(DEFAULT_VISION_FIXTURE_PATH)
    if not demo_map:
        pytest.skip("demo vision fixture not present")
    overlay = tmp_path / "vision.overlay.json"
    write_overlay(overlay, {SHA_NEW: dump(OVERLAY_ANALYSIS), "3" * 64: dump(OVERLAY_ANALYSIS)})
    plain = FixtureVisionProvider(demo_map)
    layered = FixtureVisionProvider(demo_map, overlay_path=overlay)
    for sha in demo_map:  # every demo hash answers exactly as before (Req 4.6)
        assert layered.analyze(b"", "image/png", sha) == plain.analyze(b"", "image/png", sha)

    photos = sorted(p for p in DEMO_PHOTO_DIR.glob("*.png"))[:4] if DEMO_PHOTO_DIR.is_dir() else []
    if not photos:
        pytest.skip("demo photos not prepared")
    reports = []
    for label, vision in (("plain", plain), ("layered", layered)):
        folder = tmp_path / label
        folder.mkdir()
        for photo in photos:
            shutil.copy2(photo, folder / photo.name)
            sidecar = photo.with_name(photo.stem + ".metadata.json")
            if sidecar.exists():
                shutil.copy2(sidecar, folder / sidecar.name)
        repo = OutfitRepository(create_db_engine(f"sqlite:///{tmp_path / (label + '.db')}"))
        tz = ZoneInfo("Asia/Seoul")
        reports.append(ImageIngestionService(folder, vision, FixtureWeatherProvider(tz=tz), repo, tz).scan())
    assert reports[0] == reports[1]
    assert hashlib.sha256(photos[0].read_bytes()).hexdigest() in demo_map
