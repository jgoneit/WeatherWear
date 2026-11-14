"""Phase 2 settings: AutomationConfig, parsing helpers and path resolution.

Example test area (l) of Phase 2 Req 20.3 (tasks 1.2): defaults, invalid
values falling back with an issue, relative paths resolved against the
repository root, and the ``.gitignore`` entries for user data and secrets.
"""

from __future__ import annotations

import logging
from datetime import time
from pathlib import Path

import pytest

from app.config import (
    AUTOMATION_VARS,
    AutomationConfig,
    parse_bool,
    parse_hhmm,
    resolve_automation_config,
    resolve_config,
)
from app.main import REPO_ROOT, resolve_automation_paths, resolve_paths

GITIGNORE = Path(__file__).resolve().parents[2] / ".gitignore"
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_phase2_defaults():
    cfg, issues = resolve_config({})
    assert issues == []
    auto = cfg.automation
    assert auto == AutomationConfig()
    assert auto.gallery_watch_enabled is False
    assert auto.daily_brief_enabled is False
    assert auto.gallery_dir == Path("./ex_img")
    assert auto.gallery_poll_seconds == 2.0
    assert auto.user_photo_dir == Path("./data/user/photos")
    assert auto.daily_brief_time == time(7, 30)
    assert auto.notifier == "log"
    assert auto.vapid_key_dir == Path("./data/secrets")
    assert auto.vapid_subject == "mailto:weatherwear@example.com"
    assert auto.vision_overlay_path == Path("./data/user/fixtures/vision.overlay.json")


def test_valid_phase2_values_are_used():
    raw = {
        "GALLERY_WATCH_ENABLED": "Yes",
        "DAILY_BRIEF_ENABLED": " ON ",
        "GALLERY_DIR": "/tmp/gallery",
        "GALLERY_POLL_SECONDS": "0.5",
        "USER_PHOTO_DIR": "/tmp/user/photos",
        "DAILY_BRIEF_TIME": "23:59",
        "NOTIFIER": "webpush",
        "VAPID_KEY_DIR": "/tmp/keys",
        "VAPID_SUBJECT": "https://weatherwear.example",
    }
    auto, issues = resolve_automation_config(raw, photo_dir=Path("/tmp/demo/photos"))
    assert issues == []
    assert auto.gallery_watch_enabled is True and auto.daily_brief_enabled is True
    assert auto.gallery_dir == Path("/tmp/gallery")
    assert auto.gallery_poll_seconds == 0.5
    assert auto.user_photo_dir == Path("/tmp/user/photos")
    assert auto.daily_brief_time == time(23, 59)
    assert auto.notifier == "webpush"
    assert auto.vapid_key_dir == Path("/tmp/keys")
    assert auto.vapid_subject == "https://weatherwear.example"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GALLERY_WATCH_ENABLED", "maybe"),
        ("DAILY_BRIEF_ENABLED", "2"),
        ("GALLERY_POLL_SECONDS", "0.49"),
        ("GALLERY_POLL_SECONDS", "61"),
        ("GALLERY_POLL_SECONDS", "nan"),
        ("GALLERY_POLL_SECONDS", "abc"),
        ("DAILY_BRIEF_TIME", "7:5"),
        ("DAILY_BRIEF_TIME", "24:00"),
        ("DAILY_BRIEF_TIME", "07:60"),
        ("NOTIFIER", "fcm"),
        ("VAPID_SUBJECT", "http://insecure.example"),
        ("VAPID_SUBJECT", "someone@example.com"),
    ],
)
def test_invalid_phase2_values_fall_back_with_issue(name: str, value: str):
    cfg, issues = resolve_config({name: value})
    assert cfg.automation == AutomationConfig()
    assert [i.variable for i in issues] == [name]
    # The message names the variable and never echoes the input value.
    assert name in issues[0].message
    assert value.strip() not in issues[0].message


def test_blank_phase2_values_use_defaults_without_issue():
    cfg, issues = resolve_config({name: "  " for name in AUTOMATION_VARS})
    assert issues == []
    assert cfg.automation == AutomationConfig()


@pytest.mark.parametrize(
    ("word", "expected"),
    [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("On", True),
     ("false", False), ("0", False), ("No", False), ("off", False), ("", None), ("y", None)],
)
def test_parse_bool(word: str, expected: bool | None):
    assert parse_bool(word) is expected


def test_parse_hhmm():
    assert parse_hhmm("00:00") == time(0, 0)
    assert parse_hhmm("07:30") == time(7, 30)
    assert parse_hhmm("23:59") == time(23, 59)
    for bad in ("7:30", "07:3", "24:00", "07:60", "0730", "07:30:00", ""):
        assert parse_hhmm(bad) is None


@pytest.mark.parametrize(
    "user_photo_dir",
    ["./data/demo/photos", "./data/demo", "./data/demo/photos/user", "./ex_img", "./ex_img/sub", "."],
)
def test_user_photo_dir_conflict_falls_back_with_issue(user_photo_dir: str):
    cfg, issues = resolve_config({"USER_PHOTO_DIR": user_photo_dir})
    assert [i.variable for i in issues] == ["USER_PHOTO_DIR"]
    assert cfg.automation.user_photo_dir == Path("./data/user/photos")


def test_relative_paths_resolved_to_repo_root():
    cfg, issues = resolve_config({})
    assert issues == []
    resolved = resolve_automation_paths(resolve_paths(cfg))
    auto = resolved.automation
    assert auto.gallery_dir == (REPO_ROOT / "ex_img").resolve()
    assert auto.user_photo_dir == (REPO_ROOT / "data/user/photos").resolve()
    assert auto.vapid_key_dir == (REPO_ROOT / "data/secrets").resolve()
    assert auto.vision_overlay_path == (REPO_ROOT / "data/user/fixtures/vision.overlay.json").resolve()
    # Everything else is unchanged.
    assert auto.notifier == cfg.automation.notifier
    assert auto.daily_brief_time == cfg.automation.daily_brief_time


def test_absolute_conflict_found_after_resolution(tmp_path: Path, caplog):
    # A relative USER_PHOTO_DIR and an absolute PHOTO_DIR only collide once both are absolute.
    photo_dir = tmp_path / "photos"
    cfg, issues = resolve_config({"PHOTO_DIR": str(photo_dir), "USER_PHOTO_DIR": "photos/user"})
    assert issues == []
    collected: list = []
    with caplog.at_level(logging.ERROR, logger="app.main"):
        resolved = resolve_automation_paths(cfg, root=tmp_path, issues=collected)
    assert [i.variable for i in collected] == ["USER_PHOTO_DIR"]
    assert resolved.automation.user_photo_dir == (tmp_path / "data/user/photos").resolve()
    assert resolved.automation.gallery_watch_enabled is False  # default value, unchanged
    assert "USER_PHOTO_DIR" in caplog.text


def test_watch_turned_off_when_default_user_photo_dir_also_conflicts(tmp_path: Path):
    cfg, _ = resolve_config(
        {
            "GALLERY_WATCH_ENABLED": "true",
            "PHOTO_DIR": str(tmp_path / "data"),  # contains the default ./data/user/photos
            "USER_PHOTO_DIR": str(tmp_path / "data" / "x" / ".." / "u"),
        }
    )
    collected: list = []
    resolved = resolve_automation_paths(cfg, root=tmp_path, issues=collected)
    assert [i.variable for i in collected] == ["USER_PHOTO_DIR", "USER_PHOTO_DIR"]
    assert resolved.automation.gallery_watch_enabled is False


def test_gitignore_has_user_and_secrets():
    lines = {line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()}
    assert "data/user/" in lines
    assert "data/secrets/" in lines


def test_env_example_turns_automation_on_and_documents_defaults():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    lines = text.splitlines()
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in lines
        if line and not line.startswith("#") and "=" in line
    }
    assert values["GALLERY_WATCH_ENABLED"] == "true"
    assert values["DAILY_BRIEF_ENABLED"] == "true"
    assert values["GALLERY_DIR"] == "./ex_img"
    assert values["GALLERY_POLL_SECONDS"] == "2"
    assert values["USER_PHOTO_DIR"] == "./data/user/photos"
    assert values["DAILY_BRIEF_TIME"] == "07:30"
    assert values["NOTIFIER"] == "log"
    assert values["VAPID_KEY_DIR"] == "./data/secrets"
    assert values["VAPID_SUBJECT"] == "mailto:weatherwear@example.com"

    def comment_before(name: str) -> str:
        index = next(i for i, line in enumerate(lines) if line.startswith(f"{name}="))
        block = []
        for line in reversed(lines[:index]):
            if not line.startswith("#"):
                break
            block.append(line)
        return "\n".join(block)

    for name in ("GALLERY_WATCH_ENABLED", "DAILY_BRIEF_ENABLED"):
        assert "코드 기본값은 false" in comment_before(name)
    assert "`ex_img/`를 휴대폰 갤러리로 본다" in comment_before("GALLERY_DIR")
    assert "개인키가 저장되므로 커밋하지 않는다" in comment_before("VAPID_KEY_DIR")
    for name in AUTOMATION_VARS:
        assert comment_before(name), name
