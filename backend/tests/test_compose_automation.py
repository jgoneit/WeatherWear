"""``compose.yaml`` carries the Phase 2 settings and volumes (tasks 7.6, Req 18.10).

Text checks only; docker is not called.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "compose.yaml"
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"


def backend_block(text: str) -> str:
    start = text.index("  backend:\n")
    end = text.index("  frontend:\n")
    return text[start:end]


def test_compose_has_automation_env_and_volumes() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    backend = backend_block(text)
    for line in (
        "GALLERY_WATCH_ENABLED: ${GALLERY_WATCH_ENABLED:-true}",
        "DAILY_BRIEF_ENABLED: ${DAILY_BRIEF_ENABLED:-true}",
        "DAILY_BRIEF_TIME: ${DAILY_BRIEF_TIME:-07:30}",
        "NOTIFIER: ${NOTIFIER:-log}",
        "VAPID_SUBJECT: ${VAPID_SUBJECT:-mailto:weatherwear@example.com}",
        "GALLERY_DIR: /data/gallery",
        "USER_PHOTO_DIR: /data/user/photos",
        "VAPID_KEY_DIR: /data/secrets",
        "- ${GALLERY_HOST_DIR:-./ex_img}:/data/gallery:ro",
        "- ./data/user:/data/user",
        "- vapid-keys:/data/secrets",
        # Phase 1 hardening and mounts stay.
        "read_only: true",
        "- db-data:/data/db",
        "- ${PHOTO_HOST_DIR:-./data/demo/photos}:/data/photos:ro",
    ):
        assert line in backend, line
    top_volumes = text[text.index("\nvolumes:\n"):]
    assert "  db-data:" in top_volumes and "  vapid-keys:" in top_volumes


def test_dockerfile_creates_phase2_mount_points_for_the_app_user() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for path in ("/data/gallery", "/data/user/photos", "/data/secrets"):
        assert path in text, path
    assert "chown -R app:app /data" in text
