"""``GET /api/automation/status`` (Phase 2 tasks 7.2, Req 17.1, 17.2, 1.5).

The app is built without entering the TestClient context, so the lifespan
(and its watcher / scheduler threads) never starts; the watcher is driven by
calling ``poll_once()`` directly. Every Phase 2 folder is under ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import resolve_config
from app.main import create_app


def make_app(tmp_path: Path, **overrides: str):
    (tmp_path / "photos").mkdir(exist_ok=True)
    raw = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'status.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        "GALLERY_DIR": str(tmp_path / "gallery"),
        "USER_PHOTO_DIR": str(tmp_path / "user" / "photos"),
        "VAPID_KEY_DIR": str(tmp_path / "secrets"),
        **overrides,
    }
    config, issues = resolve_config(raw)
    assert issues == []
    return create_app(config)


def test_status_reflects_disabled_defaults(tmp_path: Path) -> None:
    client = TestClient(make_app(tmp_path))
    body = client.get("/api/automation/status").json()
    assert body["gallery"] == {
        "enabled": False, "running": False, "dir_available": False,
        "last_poll_at": None, "pending": 0, "uncertain": 0,
    }
    assert body["daily_brief"] == {
        "enabled": False, "time": "07:30", "timezone": "Asia/Seoul",
        "next_run_at": None, "last_scheduled": None,
    }
    assert body["notifier"] == {"name": "log", "vapid_ready": False, "subscription_count": 0}
    assert body["location"] == {"has_last": False, "received_at": None, "place_label": None}
    # Watching is off: the gallery folder was never created or read (Req 1.3).
    assert not (tmp_path / "gallery").exists()
    assert not (tmp_path / "secrets").exists()


def test_status_with_watcher_brief_and_location(tmp_path: Path) -> None:
    (tmp_path / "gallery").mkdir()
    app = make_app(
        tmp_path, GALLERY_WATCH_ENABLED="true", DAILY_BRIEF_ENABLED="true", DAILY_BRIEF_TIME="06:45"
    )
    client = TestClient(app)
    app.state.container.gallery_watcher.poll_once()
    client.post("/api/location", json={"latitude": 35.1796, "longitude": 129.0756})

    body = client.get("/api/automation/status").json()
    assert body["gallery"]["enabled"] is True
    assert body["gallery"]["dir_available"] is True
    assert body["gallery"]["running"] is False  # no lifespan thread in this test
    assert body["gallery"]["last_poll_at"] is not None
    assert body["daily_brief"]["enabled"] is True
    assert body["daily_brief"]["time"] == "06:45"
    assert body["daily_brief"]["next_run_at"] is not None
    assert body["location"]["has_last"] is True
    assert body["location"]["place_label"] == "부산"


def test_status_dir_unavailable_without_path(tmp_path: Path) -> None:
    app = make_app(tmp_path, GALLERY_WATCH_ENABLED="true")  # tmp_path/gallery does not exist
    client = TestClient(app)
    app.state.container.gallery_watcher.poll_once()
    response = client.get("/api/automation/status")
    assert response.json()["gallery"]["dir_available"] is False
    assert str(tmp_path) not in response.text


def test_automation_status_fields_no_secrets(tmp_path: Path) -> None:
    (tmp_path / "gallery").mkdir()
    app = make_app(tmp_path, GALLERY_WATCH_ENABLED="true", NOTIFIER="webpush")
    client = TestClient(app)
    container = app.state.container
    keys = container.vapid_keys.ensure()
    client.post(
        "/api/location", json={"latitude": 35.1796, "longitude": 129.0756, "accuracy_m": 12.0}
    )

    response = client.get("/api/automation/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"gallery", "daily_brief", "notifier", "location"}
    assert body["notifier"]["name"] == "webpush"
    assert body["notifier"]["vapid_ready"] is True
    text = response.text
    assert str(tmp_path) not in text  # no server path
    for secret in container.vapid_keys.secret_strings():
        assert secret not in text
    assert keys.public_key_b64url not in text  # the status never carries key material
    for raw in ("35.18", "129.08", "35.1796", "129.0756", "12.0"):
        assert raw not in text  # Place_Label only, no coordinates
