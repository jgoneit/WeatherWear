"""Lifespan wiring of the Phase 2 background work (tasks 7.1, 7.3, Req 1.1-1.3, 12.1, 15.1, 18.7).

All Phase 2 folders and the database are under ``tmp_path``; with fixture
weather and the log notifier nothing leaves the machine (socket guard).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import resolve_config
from app.main import create_app

THREAD_NAMES = {"gallery-watcher", "daily-brief"}


def make_config(tmp_path: Path, **overrides: str):
    (tmp_path / "photos").mkdir(exist_ok=True)
    raw = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'lifespan.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        "GALLERY_DIR": str(tmp_path / "gallery"),
        "USER_PHOTO_DIR": str(tmp_path / "user" / "photos"),
        "VAPID_KEY_DIR": str(tmp_path / "secrets"),
        **overrides,
    }
    config, issues = resolve_config(raw)
    assert issues == []
    return config


def automation_threads() -> set[str]:
    return {t.name for t in threading.enumerate() if t.name in THREAD_NAMES and t.is_alive()}


def test_lifespan_starts_and_stops_threads(tmp_path: Path) -> None:
    (tmp_path / "gallery").mkdir()
    config = make_config(
        tmp_path,
        GALLERY_WATCH_ENABLED="true",
        DAILY_BRIEF_ENABLED="true",
        GALLERY_POLL_SECONDS="0.5",
    )
    app = create_app(config)
    assert automation_threads() == set()
    with TestClient(app) as client:
        assert automation_threads() == THREAD_NAMES
        assert client.get("/api/automation/status").json()["gallery"]["enabled"] is True
    assert automation_threads() == set()  # joined on shutdown (Req 1.2)


def test_no_threads_and_no_gallery_access_by_default(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    with TestClient(app) as client:
        assert automation_threads() == set()
        assert client.get("/api/automation/status").status_code == 200
    assert not (tmp_path / "gallery").exists()
    assert not (tmp_path / "user").exists()
    assert not (tmp_path / "secrets").exists()  # NOTIFIER=log: no key is made


def test_webpush_lifespan_prepares_keys_and_redacts_them(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path, NOTIFIER="webpush"))
    with TestClient(app) as client:
        keys = app.state.container.vapid_keys
        assert keys.ready  # ensure() ran at startup (Req 15.1)
        assert client.get("/api/push/vapid-public-key").status_code == 200
        secrets = keys.secret_strings()
        assert secrets

        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(self.format(record))

        handler = Capture()
        root = logging.getLogger()
        for existing in root.handlers:
            for flt in existing.filters:
                handler.addFilter(flt)
        root.addHandler(handler)
        try:
            logging.getLogger("test.vapid").warning("leak attempt %s", secrets[0])
        finally:
            root.removeHandler(handler)
        assert records
        assert all(secrets[0] not in line for line in records)
        assert any("***" in line for line in records)


def test_unwritable_key_dir_does_not_stop_startup(tmp_path: Path) -> None:
    blocker = tmp_path / "secrets"
    blocker.write_text("not a folder", encoding="utf-8")
    app = create_app(make_config(tmp_path, NOTIFIER="webpush"))
    with TestClient(app) as client:
        assert app.state.container.vapid_keys.ready is False
        response = client.get("/api/push/vapid-public-key")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "VAPID_KEY_UNAVAILABLE"
        assert client.get("/api/meta").status_code == 200  # the rest of the app still works
