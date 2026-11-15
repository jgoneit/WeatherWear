"""GalleryWatcher, runtime builder and CLI (tasks 3.13, Req 1.1, 1.2, 1.4–1.8, 2.4, 2.5, 2.8)."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AutomationConfig, resolve_config
from app.gallery.rules import Signature, StableEvent
from app.gallery.runtime import build_gallery_runtime
from app.gallery.store import ImportOutcome
from tests.gallery_support import OUTFIT, FakeVision, GalleryEnv, make_engine, png_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(tmp_path: Path) -> GalleryEnv:
    return GalleryEnv(tmp_path, FakeVision(OUTFIT))


def test_first_poll_records_baseline_without_analysis(env: GalleryEnv) -> None:
    env.add("old1.png", png_bytes((1, 0, 0)))
    env.add("old2.jpg", png_bytes((2, 0, 0)))
    (env.gallery / "clothing_tags.json").write_text("{}", encoding="utf-8")
    result = env.poll()
    assert result.baseline_rows == 2 and result.processed == []
    env.poll(5)
    assert env.vision.calls == 0
    assert env.row("old1.png").status == "baseline" and env.row("old2.jpg").status == "baseline"
    assert env.row("clothing_tags.json") is None
    assert env.store.list_imports(20) == []
    assert env.user_files() == []


def test_new_file_after_baseline_is_imported(env: GalleryEnv) -> None:
    env.poll()  # empty folder: baseline marker only
    env.add("capture_1.png", png_bytes((3, 0, 0)))
    first = env.poll()
    assert first.processed == [] and env.row("capture_1.png") is None  # seen once
    second = env.poll()
    assert second.processed == [("capture_1.png", "imported")]
    row = env.row("capture_1.png")
    assert row.status == "imported" and row.outfit_id is not None
    assert env.vision.calls == 1
    env.poll(3)
    assert env.vision.calls == 1  # a handled version is never processed again


def test_file_added_while_stopped_is_processed_on_restart(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    first = GalleryEnv(tmp_path, FakeVision(OUTFIT), engine=engine)
    first.add("before.png", png_bytes((4, 0, 0)))
    first.poll(3)
    assert first.row("before.png").status == "baseline"

    # Server stopped; a photo arrives; a new watcher (fresh trackers) starts.
    first.add("while_stopped.png", png_bytes((5, 0, 0)))
    restarted = GalleryEnv(tmp_path, FakeVision(OUTFIT), engine=engine)
    restarted.clock.now = first.clock.now
    assert restarted.poll().baseline_rows is None  # baseline is not redone
    restarted.poll()
    assert restarted.row("while_stopped.png").status == "imported"
    assert restarted.row("before.png").status == "baseline"
    assert restarted.vision.calls == 1


def test_growing_file_not_read_until_stable(env: GalleryEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    env.poll()
    path = env.gallery / "big.png"
    full = png_bytes((6, 0, 0), size=(64, 64))
    reads: list[str] = []
    original = Path.read_bytes

    def tracking_read_bytes(self: Path) -> bytes:
        if self.parent == env.gallery:
            reads.append(self.name)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", tracking_read_bytes)
    for size in (10, 40, 90):  # still being copied: the signature changes every check
        path.write_bytes(full[:size])
        os.utime(path, ns=(size, size * 1_000_000_000))
        env.poll()
    assert reads == [] and env.row("big.png") is None
    path.write_bytes(full)
    os.utime(path, ns=(1, 999 * 1_000_000_000))
    env.poll()
    assert reads == []
    env.poll()
    assert reads == ["big.png"]
    assert env.row("big.png").status == "imported"


def test_zero_byte_file_never_processed(env: GalleryEnv) -> None:
    env.poll()
    (env.gallery / "empty.jpg").write_bytes(b"")
    env.poll(4)
    assert env.row("empty.jpg") is None and env.vision.calls == 0


def test_hidden_tmp_then_rename_processed_once(env: GalleryEnv) -> None:
    env.poll()
    temp = env.gallery / ".capture_20261114_090000.png.tmp"
    temp.write_bytes(png_bytes((7, 0, 0)))
    env.poll(3)
    assert env.store.list_imports(20) == []
    os.replace(temp, env.gallery / "capture_20261114_090000.png")
    env.poll(4)
    rows = env.store.list_imports(20)
    assert [(r.file_name, r.status) for r in rows] == [("capture_20261114_090000.png", "imported")]
    assert env.vision.calls == 1


def test_symlink_and_subfolder_ignored(env: GalleryEnv, tmp_path: Path) -> None:
    env.poll()
    outside = tmp_path / "outside.png"
    outside.write_bytes(png_bytes((8, 0, 0)))
    (env.gallery / "link.png").symlink_to(outside)
    (env.gallery / "sub").mkdir()
    (env.gallery / "sub" / "inner.png").write_bytes(png_bytes((9, 0, 0)))
    env.poll(4)
    assert env.store.list_imports(20) == [] and env.vision.calls == 0


def test_processing_order_first_seen_then_name(env: GalleryEnv) -> None:
    env.poll()
    env.add("b.png", png_bytes((10, 0, 0)))
    env.add("a.png", png_bytes((11, 0, 0)))
    env.poll()
    env.add("0_late.png", png_bytes((12, 0, 0)))
    env.poll()
    result = env.poll()
    assert [name for name, _ in result.processed] == ["0_late.png"]
    first = env.store.list_imports(20)
    assert {r.file_name for r in first} == {"a.png", "b.png", "0_late.png"}


def test_same_poll_events_processed_in_name_order(env: GalleryEnv) -> None:
    env.poll()
    env.add("b.png", png_bytes((13, 0, 0)))
    env.add("a.png", png_bytes((14, 0, 0)))
    env.poll()
    result = env.poll()
    assert [name for name, _ in result.processed] == ["a.png", "b.png"]


def test_missing_gallery_dir_warns_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = GalleryEnv(tmp_path, FakeVision(OUTFIT), gallery=tmp_path / "gallery")
    env.gallery.rmdir()
    with caplog.at_level(logging.WARNING, logger="app.gallery.watcher"):
        for _ in range(4):
            assert env.poll().dir_available is False
    warnings = [r for r in caplog.records if r.name == "app.gallery.watcher" and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and str(tmp_path) not in warnings[0].getMessage()
    assert env.watcher.status().dir_available is False

    env.gallery.mkdir()
    assert env.poll().dir_available is True
    assert env.watcher.status().dir_available is True
    caplog.clear()
    env.gallery.rmdir()
    env.gallery.write_text("not a folder", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.gallery.watcher"):
        env.poll(2)
    assert len([r for r in caplog.records if r.name == "app.gallery.watcher" and r.levelno == logging.WARNING]) == 1


def test_unexpected_exception_does_not_kill_watcher(env: GalleryEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    env.poll()
    env.add("x.png", png_bytes((15, 0, 0)))

    def explode(row, now):  # noqa: ANN001, ANN202
        raise RuntimeError("bug")

    monkeypatch.setattr(env.importer, "process", explode)
    env.poll(2)
    row = env.row("x.png")
    assert (row.status, row.last_error_stage, row.last_error_code) == ("retry_wait", "storage", "STORAGE_ERROR")
    monkeypatch.undo()
    env.clock.advance(minutes=1)
    env.poll()
    assert env.row("x.png").status == "imported"


def test_stop_checked_before_each_row(env: GalleryEnv) -> None:
    env.poll()
    env.add("a.png", png_bytes((16, 0, 0)))
    env.add("b.png", png_bytes((17, 0, 0)))
    env.poll()
    stop = threading.Event()
    original = env.importer.process

    def process_then_stop(row, now):  # noqa: ANN001, ANN202
        outcome = original(row, now)
        stop.set()
        return outcome

    env.importer.process = process_then_stop  # type: ignore[method-assign]
    result = env.watcher.poll_once(stop)
    assert [name for name, _ in result.processed] == ["a.png"]
    assert env.row("b.png").status == "pending"


def test_status_counts(env: GalleryEnv) -> None:
    env.poll()
    status = env.watcher.status()
    assert (status.enabled, status.running, status.pending, status.uncertain) == (True, False, 0, 0)
    assert status.last_poll_at is not None
    row = env.store.upsert_version(env.watcher.key, StableEvent("q.png", Signature(1, 1), env.clock()), env.clock())
    env.store.claim(row.id, env.clock())
    env.store.finish(row.id, ImportOutcome(status="uncertain"), env.clock())
    assert env.watcher.status().uncertain == 1


def test_run_stops_on_event(env: GalleryEnv) -> None:
    stop = threading.Event()
    stop.set()
    env.watcher.run(stop)  # returns at once
    assert env.watcher.status().running is False


def test_runtime_without_watching_reads_nothing(tmp_path: Path) -> None:
    base, _ = resolve_config({})
    config = replace(base, automation=AutomationConfig(gallery_dir=tmp_path / "nope", user_photo_dir=tmp_path / "u"))
    engine = make_engine(tmp_path)
    from app.repository import OutfitRepository
    container = SimpleNamespace(engine=engine, repository=OutfitRepository(engine), vision_provider=FakeVision(),
                                fixture_weather=None, location=None)
    runtime = build_gallery_runtime(config, container)  # type: ignore[arg-type]
    assert runtime.watcher is None
    assert runtime.store is not None and runtime.importer is not None and runtime.outfit_confirm is not None
    assert not (tmp_path / "u").exists()


def _load_script(name: str):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_watch_gallery_once_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    (gallery / "old.png").write_bytes(png_bytes((20, 0, 0)))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("USER_PHOTO_DIR", str(tmp_path / "user" / "photos"))
    monkeypatch.setenv("GALLERY_POLL_SECONDS", "0.5")
    monkeypatch.setenv("VISION_PROVIDER", "fixture")
    monkeypatch.setenv("WEATHER_PROVIDER", "fixture")
    monkeypatch.setenv("GALLERY_WATCH_ENABLED", "false")  # the CLI watches anyway
    script = _load_script("watch_gallery")
    args = ["--once", "--gallery-dir", str(gallery), "--env-file", str(tmp_path / "none.env")]

    assert script.main(args) == 0
    assert "기준선 기록: 1개 파일" in capsys.readouterr().out

    new = png_bytes((21, 0, 0))
    (gallery / "new.png").write_bytes(new)
    overlay = tmp_path / "user" / "fixtures" / "vision.overlay.json"
    overlay.parent.mkdir(parents=True)
    import hashlib
    overlay.write_text(json.dumps({hashlib.sha256(new).hexdigest(): OUTFIT.model_dump()}), encoding="utf-8")
    assert script.main(args) == 0
    out = capsys.readouterr().out
    assert "new.png: imported" in out
    assert str(tmp_path) not in out
    assert (tmp_path / "user" / "photos" / "new.png").read_bytes() == new


# --- review fix (1): a same-byte duplicate is reopened when the holder fails ------


def test_duplicate_reopened_when_same_bytes_holder_fails(tmp_path: Path) -> None:
    """a.png and b.png have the same bytes. a is collected first and keeps
    failing Vision (retry_wait counts as in progress), so b ends as duplicate.
    When a finally fails, b must go back to pending and be stored."""
    from datetime import timedelta

    from sqlalchemy import func
    from sqlmodel import Session, select

    from app.db import Outfit
    from app.providers.vision.base import VisionError

    vision = FakeVision(VisionError("timeout"))
    env = GalleryEnv(tmp_path, vision)
    env.poll()  # baseline of the empty gallery
    data = png_bytes((21, 22, 23))
    env.add("a.png", data)
    env.add("b.png", data)
    env.poll(2)  # stable -> a: Vision timeout (retry_wait), b: duplicate of a
    assert env.row("a.png").status == "retry_wait"
    assert env.row("b.png").status == "duplicate"

    for wait in (timedelta(minutes=1), timedelta(minutes=5)):
        env.clock.advance(wait.total_seconds())
        env.poll()
    assert env.row("a.png").status == "failed"  # 3 attempts used
    reopened = env.row("b.png")
    assert reopened.status == "pending"
    assert reopened.attempts == 0 and reopened.last_error_code is None

    vision.default = OUTFIT  # Vision works again
    env.poll()
    stored = env.row("b.png")
    assert stored.status == "imported" and stored.outfit_id is not None
    with Session(env.engine) as session:
        assert session.exec(select(func.count()).select_from(Outfit)).one() == 1


def test_duplicate_not_reopened_while_another_row_holds_the_bytes(tmp_path: Path) -> None:
    from app.gallery.rules import Signature, StableEvent
    from app.gallery.store import ImportOutcome, gallery_key

    env = GalleryEnv(tmp_path, FakeVision(OUTFIT))
    key = gallery_key(env.gallery)
    sha = "c" * 64
    rows = {}
    for name in ("a.png", "b.png", "c.png"):
        rows[name] = env.store.upsert_version(
            key, StableEvent(name=name, signature=Signature(size=10, mtime_ns=1), first_seen=env.clock()), env.clock()
        )
    now = env.clock()
    for name, status in (("c.png", "imported"), ("b.png", "duplicate")):
        assert env.store.claim(rows[name].id, now)
        env.store.finish(rows[name].id, ImportOutcome(status=status, sha256=sha), now)
    assert env.store.claim(rows["a.png"].id, now)
    env.store.finish(rows["a.png"].id, ImportOutcome(status="failed", sha256=sha, code="VISION_TIMEOUT"), now)
    assert env.row("b.png").status == "duplicate"  # c.png still holds the bytes
