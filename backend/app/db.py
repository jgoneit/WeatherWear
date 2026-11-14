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

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import CheckConstraint, Index, event, text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Field, Session, SQLModel, create_engine

from app.domain import (
    UNKNOWN_TAGS,
    ClothingTags,
    VisionAnalysis,
    normalize_lower_type,
    normalize_outer_type,
    normalize_upper_type,
)

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


class PhotoAnalysisRow(SQLModel, table=True):
    """Persisted Vision decision for every successfully analyzed image hash."""

    __tablename__ = "photo_analyses"
    __table_args__ = (
        CheckConstraint(
            "photo_type IN ('outfit','non_outfit','uncertain')",
            name="ck_photo_analyses_type",
        ),
        CheckConstraint(
            "vision_provider IN ('gemini','fixture')",
            name="ck_photo_analyses_provider",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    sha256: str = Field(unique=True, index=True)
    file_name: str
    photo_type: str
    analysis_json: str
    vision_provider: str
    created_at: str


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


SCHEMA_VERSION = 3  # PRAGMA user_version of the current table layout (Phase 2)
# Phase 1 rule: a file below this version is backed up and recreated. Version 2
# (Phase 1 + main) is upgraded in place by adding the Phase 2 tables (Req 19.2).
REBUILD_BELOW_VERSION = 2
_SQLITE_SIDE_FILES = ("-wal", "-shm")

MigrationResult = Literal["new", "rebuilt", "upgraded", "current", "newer"]


def _read_user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def backup_outdated_db(path: Path, *, now: datetime | None = None) -> Path | None:
    """Rename an old-schema SQLite file out of the way before tables are created.

    ``create_all()`` cannot change existing CHECK constraints or add columns,
    so a file with ``user_version < REBUILD_BELOW_VERSION`` is renamed (together with
    its ``-wal``/``-shm`` files) to ``<name>.v1-backup-<UTC yyyymmddHHMMSS>``.
    Nothing is deleted. Returns the backup path, or None if no backup was needed.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return None
    if _read_user_version(path) >= REBUILD_BELOW_VERSION:
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
        REBUILD_BELOW_VERSION,
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

    is_sqlite = make_url(database_url).drivername.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args)
    if is_sqlite:
        event.listen(engine, "connect", _set_sqlite_pragmas)

    # The engine connects lazily, so a backup inside migrate_schema still
    # happens before any connection opens the file.
    migrate_schema(file_path, engine)
    migrate_legacy_garment_types(engine)
    backfill_photo_analyses(engine)
    return engine


def migrate_schema(path: Path | None, engine: Engine) -> MigrationResult:
    """Bring a database to SCHEMA_VERSION without losing data (Req 19.2-19.4, 19.6).

    By the ``user_version`` of an existing file:

    - ``< REBUILD_BELOW_VERSION`` (2): Phase 1 rule, ``backup_outdated_db`` moves the
      file to ``.v1-backup-…`` and a new file is created -> ``"rebuilt"``.
    - ``== 2``: ``create_all`` adds only the Phase 2 tables and indexes
      (``CREATE … IF NOT EXISTS``), then ``user_version = 3`` -> ``"upgraded"``.
    - ``== 3``: ``create_all`` is a no-op -> ``"current"``.
    - ``> 3``: tables are created if missing, ``user_version`` is kept and a
      WARNING is logged -> ``"newer"``.

    A missing or empty file (or a non-file database) is ``"new"``. Running it
    again gives the same result, because the upgrade is one ``create_all`` and
    one ``PRAGMA user_version``.
    """
    before: int | None = None
    if path is not None and path.is_file() and path.stat().st_size > 0:
        before = _read_user_version(path)

    result: MigrationResult
    if before is None:
        result = "new"
    elif before < REBUILD_BELOW_VERSION:
        backup_outdated_db(path)  # type: ignore[arg-type]  # path is a file here
        result = "rebuilt"
    elif before < SCHEMA_VERSION:
        result = "upgraded"
    elif before == SCHEMA_VERSION:
        result = "current"
    else:
        result = "newer"

    init_db(engine)
    if result == "upgraded":
        logger.info(
            "Database schema upgraded from version %d to %d (Phase 2 tables added)",
            before,
            SCHEMA_VERSION,
        )
    elif result == "newer":
        logger.warning(
            "Database schema version %d is newer than this app (%d); keeping it as is",
            before,
            SCHEMA_VERSION,
        )
    return result


def init_db(engine: Engine) -> None:
    """Create all WeatherWear tables if they do not exist and mark the schema version.

    A database already marked with a newer ``user_version`` keeps its mark.
    """
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            current = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
            if current <= SCHEMA_VERSION:
                conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))


def migrate_legacy_garment_types(engine: Engine) -> int:
    """Normalize garment labels written before the canonical taxonomy.

    Existing databases used free-form strings. Known aliases are mapped to a
    canonical identifier and unrecognized values become ``unknown`` so a
    legacy row cannot make history or recommendation reads fail validation.
    The migration is idempotent and returns the number of changed Outfit rows.
    """
    changed = 0
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, upper_type, outer_type, lower_type FROM outfits")
        ).mappings().all()
        for row in rows:
            upper = normalize_upper_type(row["upper_type"])
            outer = normalize_outer_type(row["outer_type"])
            lower = normalize_lower_type(row["lower_type"])
            if (
                upper == row["upper_type"]
                and outer == row["outer_type"]
                and lower == row["lower_type"]
            ):
                continue
            connection.execute(
                text(
                    "UPDATE outfits SET upper_type = :upper, outer_type = :outer, "
                    "lower_type = :lower WHERE id = :id"
                ),
                {"id": row["id"], "upper": upper, "outer": outer, "lower": lower},
            )
            changed += 1
    if changed:
        logger.info("기존 의류 카테고리를 canonical taxonomy로 변환했습니다: %d건", changed)
    return changed


def backfill_photo_analyses(engine: Engine) -> int:
    """Create classification records for Outfit rows written by older releases.

    Rows with no garment evidence are marked ``uncertain`` and therefore stop
    appearing in history and recommendations until a later scan reclassifies
    them. Other legacy Outfit rows retain their prior accepted status.
    """
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT o.sha256, o.file_name, o.upper_type, o.outer_type, o.lower_type, "
                "o.colors_json, o.layers_visible, o.vision_confidence, o.vision_provider "
                "FROM outfits o LEFT JOIN photo_analyses p ON p.sha256 = o.sha256 "
                "WHERE p.id IS NULL"
            )
        ).mappings().all()
        for row in rows:
            try:
                tags = ClothingTags(
                    upper_type=row["upper_type"],
                    outer_type=row["outer_type"],
                    lower_type=row["lower_type"],
                    colors=json.loads(row["colors_json"]),
                    layers_visible=row["layers_visible"],
                    confidence=row["vision_confidence"],
                )
            except Exception:
                tags = UNKNOWN_TAGS
            uncertain = tags == UNKNOWN_TAGS
            analysis = VisionAnalysis(
                photo_type="uncertain" if uncertain else "outfit",
                photo_type_confidence=0.0 if uncertain else 1.0,
                reason=(
                    "Legacy record has no visible garment evidence."
                    if uncertain
                    else "Backfilled from a previously accepted outfit record."
                ),
                tags=tags,
            )
            connection.execute(
                text(
                    "INSERT INTO photo_analyses "
                    "(sha256, file_name, photo_type, analysis_json, vision_provider, created_at) "
                    "VALUES (:sha256, :file_name, :photo_type, :analysis_json, :provider, :created_at)"
                ),
                {
                    "sha256": row["sha256"],
                    "file_name": row["file_name"],
                    "photo_type": analysis.photo_type,
                    "analysis_json": analysis.model_dump_json(),
                    "provider": row["vision_provider"],
                    "created_at": to_utc_iso(datetime.now(UTC)),
                },
            )
    if rows:
        logger.info("기존 착장에 사진 유형 판정을 추가했습니다: %d건", len(rows))
    return len(rows)


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


# Register the Phase 2 tables on SQLModel.metadata (Req 19.1). Imported last
# because db_automation only needs SQLModel, not the names above.
from app import db_automation as _db_automation  # noqa: E402, F401
