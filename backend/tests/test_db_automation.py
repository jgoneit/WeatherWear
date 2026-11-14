"""Phase 2 tables and the additive v2 -> v3 upgrade (Phase 2 Req 19, tasks 1.4).

Example test area (k) of Phase 2 Req 20.3.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from app.db import (
    REBUILD_BELOW_VERSION,
    SCHEMA_VERSION,
    Feedback,
    Outfit,
    PhotoAnalysisRow,
    WeatherSnapshot,
    create_db_engine,
    migrate_schema,
    session_scope,
)
from app.db_automation import (
    PHASE2_TABLES,
    DailyBriefRun,
    GalleryFile,
    Notification,
    NotificationDelivery,
    PhotoConfirmation,
    PushSubscription,
    UserLocation,
    UserPhoto,
)

NOW = "2026-11-14T00:00:00.000000+00:00"
PHASE1_TABLES = ("outfits", "weather_snapshots", "feedback", "photo_analyses")
SHA = "a" * 64


def _phase1_tables():
    return [Outfit.__table__, WeatherSnapshot.__table__, Feedback.__table__, PhotoAnalysisRow.__table__]


def make_v2_db(path: Path) -> None:
    """A Phase 1 + main database: four tables, one row each, ``user_version = 2``."""
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine, tables=_phase1_tables())
    with engine.begin() as conn:
        conn.execute(text("PRAGMA user_version = 2"))
        conn.execute(
            text(
                "INSERT INTO outfits (id, sha256, file_name, captured_at, captured_at_utc, latitude, "
                "longitude, source, metadata_synthetic, upper_type, outer_type, lower_type, colors_json, "
                "layers_visible, vision_confidence, vision_provider, created_at) VALUES (1, :sha, "
                "'015_loose_hair_front_cream.png', '2025-11-20T08:30:00+09:00', "
                "'2025-11-19T23:30:00.000000+00:00', 37.57, 126.98, 'exif', 0, 'knit', 'none', "
                "'unknown', :colors, 1, 0.8, 'fixture', :now)"
            ),
            {"sha": SHA, "colors": json.dumps(["cream"]), "now": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO weather_snapshots (outfit_id, observed_at, actual_temperature_c, provider, "
                "is_fixture, created_at) VALUES (1, '2025-11-20T08:00:00+09:00', 14.4, 'fixture', 1, :now)"
            ),
            {"now": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO feedback (outfit_id, thermal_vote, source, created_at) "
                "VALUES (1, 'comfortable', 'user_action', :now)"
            ),
            {"now": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO photo_analyses (sha256, file_name, photo_type, analysis_json, "
                "vision_provider, created_at) VALUES (:sha, '015_loose_hair_front_cream.png', 'outfit', "
                "'{}', 'fixture', :now)"
            ),
            {"sha": SHA, "now": NOW},
        )
    engine.dispose()


def _dump(path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in PHASE1_TABLES}
    finally:
        conn.close()


def _ddl(path: Path) -> dict[str, str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE tbl_name IN (?, ?, ?, ?) AND sql IS NOT NULL",
            PHASE1_TABLES,
        ).fetchall()
        return dict(rows)
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _backups(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if ".v1-backup-" in p.name)


# --- example area (k) -------------------------------------------------------


def test_v2_db_upgraded_to_v3_without_data_loss(tmp_path: Path, caplog) -> None:
    db = tmp_path / "weatherwear.db"
    make_v2_db(db)
    before = _dump(db)

    with caplog.at_level(logging.INFO, logger="app.db"):
        engine = create_db_engine(f"sqlite:///{db}")
    try:
        assert set(PHASE2_TABLES) <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION == 3
    finally:
        engine.dispose()

    assert _dump(db) == before
    assert _backups(tmp_path) == []
    assert "upgraded from version 2 to 3" in caplog.text


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "weatherwear.db"
    make_v2_db(db)
    for _ in range(2):
        create_db_engine(f"sqlite:///{db}").dispose()
    first = (_dump(db), _ddl(db), _user_version(db))
    engine = create_db_engine(f"sqlite:///{db}")
    try:
        assert migrate_schema(db, engine) == "current"
    finally:
        engine.dispose()
    assert (_dump(db), _ddl(db), _user_version(db)) == first
    assert _backups(tmp_path) == []


def test_existing_tables_unchanged(tmp_path: Path) -> None:
    db = tmp_path / "weatherwear.db"
    make_v2_db(db)
    ddl_before = _ddl(db)
    create_db_engine(f"sqlite:///{db}").dispose()
    assert _ddl(db) == ddl_before


def test_v1_db_still_backed_up(tmp_path: Path) -> None:
    db = tmp_path / "weatherwear.db"
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE outfits (id INTEGER PRIMARY KEY, source TEXT)")
    old.execute("INSERT INTO outfits (source) VALUES ('demo_sidecar')")
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()
    assert REBUILD_BELOW_VERSION == 2

    engine = create_db_engine(f"sqlite:///{db}")
    try:
        assert set(PHASE2_TABLES) <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert _user_version(db) == 3
    kept = sqlite3.connect(tmp_path / backups[0])
    try:
        assert kept.execute("SELECT COUNT(*) FROM outfits").fetchone()[0] == 1
    finally:
        kept.close()


def test_newer_db_version_not_downgraded(tmp_path: Path, caplog) -> None:
    db = tmp_path / "weatherwear.db"
    make_v2_db(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()
    before = _dump(db)

    with caplog.at_level(logging.WARNING, logger="app.db"):
        engine = create_db_engine(f"sqlite:///{db}")
    try:
        assert set(PHASE2_TABLES) <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert _user_version(db) == 7
    assert _dump(db) == before
    assert _backups(tmp_path) == []
    assert any(r.levelno == logging.WARNING and "newer" in r.getMessage() for r in caplog.records)


def test_migrate_schema_results(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    engine = create_db_engine(f"sqlite:///{db}")
    try:
        assert migrate_schema(db, engine) == "current"
        assert migrate_schema(None, engine) == "new"
    finally:
        engine.dispose()


# --- constraints --------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'phase2.db'}")
    yield eng
    eng.dispose()


def _outfit_id(engine) -> int:
    with session_scope(engine) as session:
        row = Outfit(
            sha256=SHA, file_name="x.png", captured_at="2025-11-20T08:30:00+09:00",
            captured_at_utc="2025-11-19T23:30:00.000000+00:00", latitude=37.57, longitude=126.98,
            upper_type="knit", outer_type="none", lower_type="unknown", colors_json="[]",
            layers_visible=1, vision_confidence=0.8, vision_provider="fixture", created_at=NOW,
        )
        session.add(row)
        session.flush()
        return row.id


def _gallery_file(**overrides) -> GalleryFile:
    values = dict(
        gallery_key="0123456789abcdef", file_name="IMG_0001.JPG", size_bytes=10, mtime_ns=1,
        status="pending", first_seen_at=NOW, updated_at=NOW,
    )
    values.update(overrides)
    return GalleryFile(**values)


def _add(engine, row) -> None:
    with session_scope(engine) as session:
        session.add(row)


def test_gallery_files_unique_and_checks(engine) -> None:
    _add(engine, _gallery_file())
    with pytest.raises(IntegrityError):
        _add(engine, _gallery_file())  # UNIQUE(gallery_key, file_name)
    for bad in (
        {"status": "done"},
        {"capture_origin": "guess"},
        {"photo_type": "selfie"},
        {"sha256": "abc"},
        {"attempts": -1},
        {"reason": "x" * 201},
    ):
        with pytest.raises(IntegrityError):
            _add(engine, _gallery_file(file_name=f"bad-{len(str(bad))}.jpg", **bad))


def test_gallery_file_outfit_set_null_on_delete(engine) -> None:
    outfit_id = _outfit_id(engine)
    _add(engine, _gallery_file(status="imported", outfit_id=outfit_id))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM outfits WHERE id = :id"), {"id": outfit_id})
        assert conn.execute(text("SELECT outfit_id FROM gallery_files")).scalar() is None


def test_user_photos_file_name_unique(engine) -> None:
    _add(engine, UserPhoto(sha256="b" * 64, file_name="a.jpg", gallery_file_name="a.jpg", created_at=NOW))
    with pytest.raises(IntegrityError):
        _add(engine, UserPhoto(sha256="c" * 64, file_name="a.jpg", gallery_file_name="a.jpg", created_at=NOW))


def test_photo_confirmation_once_per_gallery_file(engine) -> None:
    outfit_id = _outfit_id(engine)
    _add(engine, _gallery_file(status="uncertain", sha256=SHA))

    def confirmation() -> PhotoConfirmation:
        return PhotoConfirmation(
            sha256=SHA, gallery_file_id=1, outfit_id=outfit_id, previous_photo_type="uncertain",
            previous_confidence=0.0, previous_reason="unknown", confirmed_at=NOW,
        )

    _add(engine, confirmation())
    with pytest.raises(IntegrityError):
        _add(engine, confirmation())


def test_user_location_single_row(engine) -> None:
    _add(engine, UserLocation(latitude=37.57, longitude=126.98, received_at=NOW))
    with pytest.raises(IntegrityError):
        _add(engine, UserLocation(id=2, latitude=37.57, longitude=126.98, received_at=NOW))
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            row = session.get(UserLocation, 1)
            row.latitude = 91.0
            session.add(row)


def test_push_subscription_endpoint_unique(engine) -> None:
    def sub() -> PushSubscription:
        return PushSubscription(
            endpoint="https://push.example/abc", p256dh="k", auth="a", vapid_public_key="p", created_at=NOW
        )

    _add(engine, sub())
    with pytest.raises(IntegrityError):
        _add(engine, sub())


def test_notification_checks_and_delivery_cascade(engine) -> None:
    with pytest.raises(IntegrityError):
        _add(engine, Notification(kind="email", title="t", body="b", url="/", created_at=NOW))
    with pytest.raises(IntegrityError):
        _add(
            engine,
            Notification(kind="daily_brief", title="t", body="b", url="/", weather_provider="x", created_at=NOW),
        )
    _add(engine, Notification(kind="daily_brief", title="t", body="b", url="/", created_at=NOW))
    _add(engine, NotificationDelivery(notification_id=1, channel="log", status="sent", created_at=NOW))
    with pytest.raises(IntegrityError):
        _add(engine, NotificationDelivery(notification_id=1, channel="sms", status="sent", created_at=NOW))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM notifications WHERE id = 1"))
        assert conn.execute(text("SELECT COUNT(*) FROM notification_deliveries")).scalar() == 0


def test_daily_brief_scheduled_unique_per_date(engine) -> None:
    def run(trigger: str) -> DailyBriefRun:
        return DailyBriefRun(brief_date="2026-11-14", trigger=trigger, status="running", started_at=NOW)

    _add(engine, run("scheduled"))
    with pytest.raises(IntegrityError):
        _add(engine, run("scheduled"))
    _add(engine, run("manual"))
    _add(engine, run("manual"))  # manual runs are not limited
    with pytest.raises(IntegrityError):
        _add(engine, DailyBriefRun(brief_date="2026-11-15", trigger="cron", status="running", started_at=NOW))
