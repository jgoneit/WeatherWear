"""SQLite tables, engine and sessions (design.md "Data Models", owned by the lead).

Times are stored as ISO 8601 TEXT, never as SQLAlchemy ``DateTime``: SQLite
has no timezone type, so ``DateTime`` would drop the original UTC offset
(Req 2.2). ``outfits.captured_at`` keeps the sidecar's original offset and
``outfits.captured_at_utc`` holds a fixed-width UTC string used for sorting.

Constraints that back the requirements:

- ``outfits.sha256 UNIQUE``: last line of defence against duplicates (Req 1.4, 1.5).
- ``weather_snapshots.outfit_id UNIQUE ... ON DELETE CASCADE``: at most one
  snapshot per Outfit (Req 4.4).
- ``feedback`` is append-only; this module has no update/delete helpers (Req 9.5).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint, Index, event, text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Field, Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ISO 8601 TEXT helpers
# ---------------------------------------------------------------------------


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def to_iso(value: datetime) -> str:
    """ISO 8601 text that keeps the value's own UTC offset (Req 2.2)."""
    return _require_aware(value).isoformat()


def to_utc_iso(value: datetime) -> str:
    """Fixed-width UTC ISO 8601 text (``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``).

    Every value has the same length and offset, so lexicographic order in SQL
    equals chronological order.
    """
    return _require_aware(value).astimezone(UTC).isoformat(timespec="microseconds")


def from_iso(value: str) -> datetime:
    """Parse stored ISO 8601 text back into an aware datetime."""
    parsed = datetime.fromisoformat(value)
    return _require_aware(parsed)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class Outfit(SQLModel, table=True):
    __tablename__ = "outfits"
    __table_args__ = (
        CheckConstraint("length(sha256) = 64", name="ck_outfits_sha256_len"),
        CheckConstraint("source IN ('demo_sidecar','exif')", name="ck_outfits_source"),
        CheckConstraint("metadata_synthetic IN (0, 1)", name="ck_outfits_metadata_synthetic"),
        CheckConstraint("vision_provider IN ('gemini','fixture')", name="ck_outfits_vision_provider"),
        CheckConstraint("layers_visible BETWEEN 0 AND 5", name="ck_outfits_layers_visible"),
        CheckConstraint(
            "vision_confidence >= 0.0 AND vision_confidence <= 1.0",
            name="ck_outfits_vision_confidence",
        ),
        Index("ix_outfits_captured_at_utc_id", text("captured_at_utc DESC"), text("id DESC")),
    )

    id: int | None = Field(default=None, primary_key=True)
    sha256: str = Field(unique=True)
    file_name: str  # file name relative to PHOTO_DIR, never an absolute path
    captured_at: str  # ISO 8601 with the original offset
    captured_at_utc: str  # to_utc_iso(captured_at), for ordering
    latitude: float
    longitude: float
    source: str = "demo_sidecar"  # 'exif' | 'demo_sidecar' (Req 2.7)
    metadata_synthetic: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("1")}
    )  # INTEGER NOT NULL DEFAULT 1 (Req 2.15)
    upper_type: str
    outer_type: str
    lower_type: str
    colors_json: str  # JSON array, order preserved
    layers_visible: int
    vision_confidence: float
    vision_provider: str
    created_at: str  # UTC ISO 8601


class WeatherSnapshot(SQLModel, table=True):
    __tablename__ = "weather_snapshots"
    __table_args__ = (
        CheckConstraint("provider IN ('open_meteo','fixture')", name="ck_weather_snapshots_provider"),
        CheckConstraint("is_fixture IN (0, 1)", name="ck_weather_snapshots_is_fixture"),
    )

    id: int | None = Field(default=None, primary_key=True)
    outfit_id: int = Field(foreign_key="outfits.id", ondelete="CASCADE", unique=True)
    observed_at: str  # ISO 8601 with offset
    actual_temperature_c: float
    apparent_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_speed_kmh: float | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None
    provider: str
    is_fixture: bool
    created_at: str  # UTC ISO 8601


class Feedback(SQLModel, table=True):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "thermal_vote IN ('cold','comfortable','hot')", name="ck_feedback_thermal_vote"
        ),
        CheckConstraint("source IN ('user_action','demo_seed')", name="ck_feedback_source"),
        Index("ix_feedback_outfit_created_id", "outfit_id", "created_at", "id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    outfit_id: int = Field(foreign_key="outfits.id")
    thermal_vote: str
    source: str
    created_at: str  # UTC ISO 8601 with microseconds


# ---------------------------------------------------------------------------
# Engine and sessions
# ---------------------------------------------------------------------------


def _sqlite_file_path(database_url: str) -> Path | None:
    """File path of a SQLite URL, or None for in-memory / non-SQLite URLs."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database)


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


SCHEMA_VERSION = 2  # PRAGMA user_version of the current table layout
_SQLITE_SIDE_FILES = ("-wal", "-shm")


def _read_user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def backup_outdated_db(path: Path, *, now: datetime | None = None) -> Path | None:
    """Rename an old-schema SQLite file out of the way before tables are created.

    ``create_all()`` cannot change existing CHECK constraints or add columns,
    so a file with ``user_version < SCHEMA_VERSION`` is renamed (together with
    its ``-wal``/``-shm`` files) to ``<name>.v1-backup-<UTC yyyymmddHHMMSS>``.
    Nothing is deleted. Returns the backup path, or None if no backup was needed.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return None
    if _read_user_version(path) >= SCHEMA_VERSION:
        return None
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.v1-backup-{stamp}")
    n = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.v1-backup-{stamp}-{n}")
        n += 1
    path.rename(backup)
    for suffix in _SQLITE_SIDE_FILES:
        side = path.with_name(path.name + suffix)
        if side.exists():
            side.rename(backup.with_name(backup.name + suffix))
    logger.warning(
        "Database schema is older than version %d; moved it to %s and creating a new one",
        SCHEMA_VERSION,
        backup.name,
    )
    return backup


def create_db_engine(database_url: str) -> Engine:
    """Create the engine, its parent directory, the pragmas and all tables.

    Every new connection runs ``PRAGMA foreign_keys=ON`` and
    ``PRAGMA journal_mode=WAL``. Tables are created with ``create_all()``;
    there is no migration tool in Phase 1. An existing file with an older
    ``PRAGMA user_version`` is backed up first (``backup_outdated_db``).
    """
    file_path = _sqlite_file_path(database_url)
    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        backup_outdated_db(file_path)

    is_sqlite = make_url(database_url).drivername.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args)
    if is_sqlite:
        event.listen(engine, "connect", _set_sqlite_pragmas)

    init_db(engine)
    return engine


def init_db(engine: Engine) -> None:
    """Create all WeatherWear tables if they do not exist and mark the schema version."""
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One transaction: commit on success, rollback on any exception."""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
