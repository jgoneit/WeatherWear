"""Fixture-mode end to end: simulated capture → watcher → Outfit → daily brief (tasks 7.3).

Requirement 20.1-20.3: the whole Phase 2 path runs offline. The app is built
without its lifespan threads; the test drives the watcher with ``poll_once()``.
All folders and the database are under ``tmp_path`` (the real
``data/weatherwear.db`` and ``ex_img/`` are never touched).
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import resolve_config
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-11-14T08:10:00+09:00"


def load_simulator():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location(
        "simulate_capture_e2e", REPO_ROOT / "scripts" / "simulate_capture.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fixture_end_to_end_simulate_to_brief(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    (tmp_path / "photos").mkdir()
    env = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'e2e.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        "GALLERY_DIR": str(gallery),
        "USER_PHOTO_DIR": str(tmp_path / "user" / "photos"),
        "VAPID_KEY_DIR": str(tmp_path / "secrets"),
        "VISION_PROVIDER": "fixture",
        "WEATHER_PROVIDER": "fixture",
        "GALLERY_WATCH_ENABLED": "true",
        "GALLERY_POLL_SECONDS": "0.5",
    }
    config, issues = resolve_config(env)
    assert issues == []
    app = create_app(config)
    client = TestClient(app)  # no lifespan: no background threads
    watcher = app.state.container.gallery_watcher
    assert watcher.poll_once().baseline_rows == 0  # first start: empty baseline

    # The capture simulator reads the same settings from the process environment.
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    simulator = load_simulator()
    assert simulator.main(["--at", AT, "--env-file", str(tmp_path / "absent.env")]) == 0
    captured = sorted(p.name for p in gallery.iterdir())
    assert len(captured) == 1 and captured[0].startswith("capture_20261114_081000")

    # Stable after two observations at least GALLERY_POLL_SECONDS apart (Req 2.3).
    imports: list[dict] = []
    for _ in range(6):
        watcher.poll_once()
        imports = client.get("/api/gallery/imports").json()
        if imports and imports[0]["status"] == "imported":
            break
        time.sleep(0.6)
    assert len(imports) == 1
    item = imports[0]
    assert item["status"] == "imported"
    assert item["file_name"] == captured[0]
    assert item["capture_origin"] == "exif"
    outfit_id = item["outfit_id"]
    assert outfit_id is not None

    outfits = client.get("/api/outfits").json()
    assert outfit_id in [o["id"] for o in outfits]
    photo = client.get(f"/api/outfits/{outfit_id}/photo")
    assert photo.status_code == 200 and photo.content  # served from User_Photo_Dir (Req 3.5)

    run = client.post("/api/daily-brief/run")
    assert run.status_code == 200, run.text
    notification = run.json()["notification"]
    assert run.json()["trigger"] == "manual"
    assert notification["weather_is_fixture"] is True
    assert notification["delivery"]["log_recorded"] is True

    listed = client.get("/api/notifications").json()
    assert [n["id"] for n in listed] == [notification["id"]]

    # The demo data folders were not touched; nothing was written outside tmp_path.
    assert sorted(p.name for p in (tmp_path / "photos").iterdir()) == []
