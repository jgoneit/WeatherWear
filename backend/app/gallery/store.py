"""GalleryStore: ``gallery_files``, ``app_state`` and ``user_photos`` (design.md §4).

Idempotency lives in the database, not in process memory (design "설계 원칙"):

- ``record_baseline`` writes the ``gallery.baseline:<key>`` marker and the
  baseline rows in one transaction; a second call for the same key is a no-op
  (Req 2.4).
- ``upsert_version`` ignores an unchanged signature, so a restart that
  re-observes every file never processes a version twice (Req 2.5).
- ``claim`` is a conditional ``UPDATE … WHERE status …``; only the process
  whose update touched the row processes it (Req 1.7). A claim holds a
  10-minute lease, so a crashed process's row becomes due again.

Times are fixed-width UTC ISO text (``to_utc_iso``), so SQL string comparison
is chronological. Rows are returned as frozen ``GalleryFileRow`` snapshots,
never as live ORM objects.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import and_, delete, func, or_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlmodel import Session, col, select

from app.db import from_iso, session_scope, to_utc_iso
from app.db_automation import GALLERY_STATUSES, AppState, GalleryFile, UserPhoto
from app.gallery.rules import Signature, StableEvent
from app.schemas import ScanDecision

logger = logging.getLogger(__name__)

GalleryStatus = Literal[
    "baseline", "pending", "processing", "imported", "duplicate",
    "skipped_non_outfit", "uncertain", "retry_wait", "failed",
]
# ``deferred`` is not stored: the file changed after the claim, so the row goes
# back to ``pending`` without counting the attempt (see ``GalleryStore.finish``).
OutcomeStatus = Literal[
    "imported", "duplicate", "skipped_non_outfit", "uncertain", "retry_wait", "failed", "deferred",
]
CaptureOrigin = Literal["exif", "estimated"]

ACTIVE_STATUSES: tuple[str, ...] = ("pending", "processing", "retry_wait")
REANALYZABLE_STATUSES: tuple[str, ...] = ("uncertain", "failed")
LEASE = timedelta(minutes=10)
BASELINE_STATE_PREFIX = "gallery.baseline:"
REASON_MAX_LENGTH = 200


def gallery_key(gallery_dir: Path) -> str:
    """``sha1(str(resolved Gallery_Dir))[:16]``: one baseline per folder (Req 2.4)."""
    try:
        resolved = Path(gallery_dir).resolve()
    except (OSError, RuntimeError):
        resolved = Path(gallery_dir).absolute()
    return hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:16]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _opt_iso(value: datetime | None) -> str | None:
    return None if value is None else to_utc_iso(value)


def _opt_dt(value: str | None) -> datetime | None:
    return None if value is None else from_iso(value)


@dataclass(frozen=True)
class GalleryFileRow:
    """Snapshot of one ``gallery_files`` row."""

    id: int
    gallery_key: str
    file_name: str
    size_bytes: int
    mtime_ns: int
    status: GalleryStatus
    sha256: str | None
    attempts: int
    next_attempt_at: datetime | None
    lease_until: datetime | None
    last_error_stage: str | None
    last_error_code: str | None
    outfit_id: int | None
    photo_type: str | None
    photo_type_confidence: float | None
    reason: str | None
    capture_origin: CaptureOrigin | None
    first_seen_at: datetime
    updated_at: datetime

    @property
    def signature(self) -> Signature:
        return Signature(size=self.size_bytes, mtime_ns=self.mtime_ns)


def _row(record: GalleryFile) -> GalleryFileRow:
    return GalleryFileRow(
        id=record.id,  # type: ignore[arg-type]
        gallery_key=record.gallery_key,
        file_name=record.file_name,
        size_bytes=record.size_bytes,
        mtime_ns=record.mtime_ns,
        status=record.status,  # type: ignore[arg-type]
        sha256=record.sha256,
        attempts=record.attempts,
        next_attempt_at=_opt_dt(record.next_attempt_at),
        lease_until=_opt_dt(record.lease_until),
        last_error_stage=record.last_error_stage,
        last_error_code=record.last_error_code,
        outfit_id=record.outfit_id,
        photo_type=record.photo_type,
        photo_type_confidence=record.photo_type_confidence,
        reason=record.reason,
        capture_origin=record.capture_origin,  # type: ignore[arg-type]
        first_seen_at=from_iso(record.first_seen_at),
        updated_at=from_iso(record.updated_at),
    )


@dataclass(frozen=True)
class ImportOutcome:
    """Result of one processing attempt, written by ``GalleryStore.finish`` (design.md §6).

    ``decision`` carries the Photo_Gate result for ``skipped_non_outfit`` and
    ``uncertain``; ``accepted_confidence``/``accepted_reason`` the accepted
    ``outfit`` decision for ``imported``.
    """

    status: OutcomeStatus
    sha256: str | None = None
    outfit_id: int | None = None
    stage: str | None = None
    code: str | None = None
    decision: ScanDecision | None = None
    capture_origin: CaptureOrigin | None = None
    next_attempt_at: datetime | None = None
    accepted_confidence: float | None = None
    accepted_reason: str | None = None


def _clip_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return reason[:REASON_MAX_LENGTH]


def _due_condition(now_iso: str):  # noqa: ANN202 - SQL expression
    return or_(
        col(GalleryFile.status) == "pending",
        and_(col(GalleryFile.status) == "retry_wait", col(GalleryFile.next_attempt_at) <= now_iso),
        and_(col(GalleryFile.status) == "processing", col(GalleryFile.lease_until) <= now_iso),
    )


_CLEARED_VERSION_FIELDS = {
    "sha256": None,
    "attempts": 0,
    "next_attempt_at": None,
    "lease_until": None,
    "last_error_stage": None,
    "last_error_code": None,
    "outfit_id": None,
    "photo_type": None,
    "photo_type_confidence": None,
    "reason": None,
    "capture_origin": None,
}


# Rows that still hold a hash: a same-byte duplicate of them is a real duplicate.
_HOLDING_STATUSES = ("imported", "uncertain", "retry_wait", "pending", "processing")
# Reopened duplicates start a fresh attempt (the hash stays; the bytes are the same).
_REOPENED_FIELDS = {key: value for key, value in _CLEARED_VERSION_FIELDS.items() if key != "sha256"}


class GalleryStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    # -- baseline (Req 2.4) ---------------------------------------------------

    def baseline_done(self, key: str) -> bool:
        with Session(self._engine) as session:
            return session.get(AppState, BASELINE_STATE_PREFIX + key) is not None

    def record_baseline(self, key: str, observed: Mapping[str, Signature], now: datetime) -> int:
        """Mark ``key`` as baselined and store every observed file as ``baseline``.

        One transaction. Returns the number of baseline rows written, or 0 when
        the marker already existed (nothing is written then).
        """
        now_iso = to_utc_iso(now)
        with session_scope(self._engine) as session:
            marker = (
                sqlite_insert(AppState)
                .values(key=BASELINE_STATE_PREFIX + key, value=now_iso, updated_at=now_iso)
                .on_conflict_do_nothing(index_elements=["key"])
            )
            if session.execute(marker).rowcount != 1:
                return 0
            written = 0
            for name in sorted(observed):
                signature = observed[name]
                statement = (
                    sqlite_insert(GalleryFile)
                    .values(
                        gallery_key=key,
                        file_name=name,
                        size_bytes=signature.size,
                        mtime_ns=signature.mtime_ns,
                        status="baseline",
                        attempts=0,
                        first_seen_at=now_iso,
                        updated_at=now_iso,
                    )
                    .on_conflict_do_nothing(index_elements=["gallery_key", "file_name"])
                )
                written += session.execute(statement).rowcount
            return written

    # -- versions and claims (Req 2.3, 2.5, 1.7) -------------------------------

    def upsert_version(self, key: str, event: StableEvent, now: datetime) -> GalleryFileRow | None:
        """Register a Stable_File version.

        No row → a new ``pending`` row. Same signature as the stored row (any
        status, baseline included) → ``None``. Different signature → the row
        becomes a new ``pending`` version: attempts 0, hash and results cleared.
        """
        now_iso = to_utc_iso(now)
        first_seen_iso = to_utc_iso(event.first_seen)
        with session_scope(self._engine) as session:
            record = session.exec(
                select(GalleryFile).where(
                    GalleryFile.gallery_key == key, GalleryFile.file_name == event.name
                )
            ).first()
            if record is None:
                record = GalleryFile(
                    gallery_key=key,
                    file_name=event.name,
                    size_bytes=event.signature.size,
                    mtime_ns=event.signature.mtime_ns,
                    status="pending",
                    attempts=0,
                    first_seen_at=first_seen_iso,
                    updated_at=now_iso,
                )
            elif record.size_bytes == event.signature.size and record.mtime_ns == event.signature.mtime_ns:
                return None
            else:
                record.size_bytes = event.signature.size
                record.mtime_ns = event.signature.mtime_ns
                record.status = "pending"
                for field_name, value in _CLEARED_VERSION_FIELDS.items():
                    setattr(record, field_name, value)
                record.first_seen_at = first_seen_iso
                record.updated_at = now_iso
            session.add(record)
            session.flush()
            return _row(record)

    def due_rows(self, key: str, now: datetime) -> list[GalleryFileRow]:
        """``pending``, due ``retry_wait`` and lease-expired ``processing`` rows,
        ordered by ``(first_seen_at, file_name)`` (Req 2.8)."""
        with Session(self._engine) as session:
            records = session.exec(
                select(GalleryFile)
                .where(GalleryFile.gallery_key == key, _due_condition(to_utc_iso(now)))
                .order_by(col(GalleryFile.first_seen_at), col(GalleryFile.file_name))
            ).all()
            return [_row(r) for r in records]

    def claim(self, row_id: int, now: datetime) -> bool:
        """Take the processing right for one due row (conditional UPDATE, Req 1.7).

        Counts the attempt (``attempts + 1``) and sets a ``LEASE``. True only
        for the caller whose update changed the row.
        """
        now_iso = to_utc_iso(now)
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, _due_condition(now_iso))
            .values(
                status="processing",
                lease_until=to_utc_iso(now + LEASE),
                attempts=GalleryFile.attempts + 1,
                updated_at=now_iso,
            )
        )
        with self._engine.begin() as conn:
            return conn.execute(statement).rowcount == 1

    def record_sha(self, row_id: int, sha256: str, now: datetime) -> None:
        """Store the hash of the version being processed, so other files with the
        same bytes see it as in progress (Req 2.6)."""
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status) == "processing")
            .values(sha256=sha256, updated_at=to_utc_iso(now))
        )
        with self._engine.begin() as conn:
            conn.execute(statement)

    def finish(self, row_id: int, outcome: ImportOutcome, now: datetime) -> bool:
        """Write an attempt's outcome to a row this process still holds.

        Returns False when the row is no longer ``processing`` (for example a
        newer file version replaced it meanwhile); nothing is written then.
        A ``deferred`` outcome releases the row to ``pending`` and uncounts the
        attempt; the stability tracker registers the new version later.

        A ``failed`` outcome also reopens same-byte ``duplicate`` rows in the
        same transaction (``_reopen_duplicates``): they were marked duplicate
        only because this row was collecting those bytes (``retry_wait`` counts
        as in progress), and would otherwise never be stored nor reanalyzable.
        """
        if outcome.status == "deferred":
            return self._release(row_id, now)
        values: dict[str, object] = {
            "status": outcome.status,
            "lease_until": None,
            "next_attempt_at": _opt_iso(outcome.next_attempt_at),
            "last_error_stage": outcome.stage,
            "last_error_code": outcome.code,
            "outfit_id": outcome.outfit_id,
            "updated_at": to_utc_iso(now),
        }
        if outcome.sha256 is not None:
            values["sha256"] = outcome.sha256
        if outcome.capture_origin is not None:
            values["capture_origin"] = outcome.capture_origin
        if outcome.decision is not None:
            values["photo_type"] = outcome.decision.photo_type
            values["photo_type_confidence"] = outcome.decision.confidence
            values["reason"] = _clip_reason(outcome.decision.reason)
        elif outcome.status == "imported":
            values["photo_type"] = "outfit"
            values["photo_type_confidence"] = outcome.accepted_confidence
            values["reason"] = _clip_reason(outcome.accepted_reason)
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status) == "processing")
            .values(**values)
        )
        with self._engine.begin() as conn:
            changed = conn.execute(statement).rowcount == 1
            if changed and outcome.status == "failed":
                self._reopen_duplicates(conn, row_id, to_utc_iso(now))
            return changed

    def _reopen_duplicates(self, conn: Connection, failed_id: int, now_iso: str) -> int:
        """After ``failed_id`` fails for good, put its same-byte ``duplicate`` rows back to ``pending``.

        Only when no other row still holds those bytes (``imported``,
        ``uncertain``, ``retry_wait``, ``pending``, ``processing``): then the
        duplicates have no file left that will store them. The lowest-id
        reopened row is processed first; the others become ``duplicate`` again
        through ``active_with_sha``. A duplicate of an already stored Outfit
        ends as ``duplicate`` again without a Vision call. Returns the number
        of reopened rows.
        """
        sha256 = conn.execute(
            select(GalleryFile.sha256).where(col(GalleryFile.id) == failed_id)
        ).scalar()
        if sha256 is None:
            return 0
        holder = conn.execute(
            select(GalleryFile.id)
            .where(
                GalleryFile.sha256 == sha256,
                col(GalleryFile.id) != failed_id,
                col(GalleryFile.status).in_(_HOLDING_STATUSES),
            )
            .limit(1)
        ).first()
        if holder is not None:
            return 0
        reopened = conn.execute(
            update(GalleryFile)
            .where(GalleryFile.sha256 == sha256, col(GalleryFile.status) == "duplicate")
            .values(status="pending", updated_at=now_iso, **_REOPENED_FIELDS)
        ).rowcount
        if reopened:
            logger.info("같은 사진의 중복 기록 %d건을 다시 처리 대기로 돌립니다.", reopened)
        return reopened

    def _release(self, row_id: int, now: datetime) -> bool:
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status) == "processing")
            .values(
                status="pending",
                lease_until=None,
                attempts=func.max(GalleryFile.attempts - 1, 0),
                updated_at=to_utc_iso(now),
            )
        )
        with self._engine.begin() as conn:
            return conn.execute(statement).rowcount == 1

    def active_with_sha(self, sha256: str, *, exclude_id: int) -> bool:
        """Another Gallery_File with these bytes is ``imported``, ``uncertain`` or
        being collected (Req 2.6).

        A ``pending``/``processing`` row only counts when its id is lower, so two
        same-byte files processed at the same moment cannot both give up.
        """
        with Session(self._engine) as session:
            hit = session.exec(
                select(GalleryFile.id)
                .where(
                    GalleryFile.sha256 == sha256,
                    col(GalleryFile.id) != exclude_id,
                    or_(
                        col(GalleryFile.status).in_(("imported", "uncertain", "retry_wait")),
                        and_(
                            col(GalleryFile.status).in_(("pending", "processing")),
                            col(GalleryFile.id) < exclude_id,
                        ),
                    ),
                )
                .limit(1)
            ).first()
            return hit is not None

    # -- user actions (Req 6.8, 6.12) -------------------------------------------

    def get(self, row_id: int) -> GalleryFileRow | None:
        with Session(self._engine) as session:
            record = session.get(GalleryFile, row_id)
            return None if record is None else _row(record)

    def reset_for_reanalyze(
        self, row_id: int, *, now: datetime | None = None
    ) -> Literal["ok", "not_found", "not_allowed"]:
        """``uncertain``/``failed`` → ``pending`` with attempts 0 (Req 6.8)."""
        now_iso = to_utc_iso(now or _utc_now())
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status).in_(REANALYZABLE_STATUSES))
            .values(
                status="pending",
                attempts=0,
                next_attempt_at=None,
                lease_until=None,
                last_error_stage=None,
                last_error_code=None,
                photo_type=None,
                photo_type_confidence=None,
                reason=None,
                updated_at=now_iso,
            )
        )
        with self._engine.begin() as conn:
            if conn.execute(statement).rowcount == 1:
                return "ok"
        return "not_allowed" if self.get(row_id) is not None else "not_found"

    def claim_uncertain(self, row_id: int, now: datetime) -> bool:
        """``uncertain`` → ``processing`` for the "착장 맞음" confirmation (Req 6.12).

        Attempts are not counted: confirming calls no Vision provider.
        """
        now_iso = to_utc_iso(now)
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status) == "uncertain")
            .values(status="processing", lease_until=to_utc_iso(now + LEASE), updated_at=now_iso)
        )
        with self._engine.begin() as conn:
            return conn.execute(statement).rowcount == 1

    def release_to_uncertain(self, row_id: int, now: datetime) -> None:
        """Undo ``claim_uncertain`` after a failed confirmation; the decision fields stay."""
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status) == "processing")
            .values(status="uncertain", lease_until=None, updated_at=to_utc_iso(now))
        )
        with self._engine.begin() as conn:
            conn.execute(statement)

    @staticmethod
    def mark_confirmed_in(
        session: Session,
        row_id: int,
        outfit_id: int,
        now: datetime,
        *,
        capture_origin: CaptureOrigin,
        confidence: float,
        reason: str,
    ) -> None:
        """Inside the confirmation transaction: the claimed row becomes ``imported``.

        Raises ``RuntimeError`` (rolling the transaction back) when the row is
        no longer held by this confirmation.
        """
        statement = (
            update(GalleryFile)
            .where(col(GalleryFile.id) == row_id, col(GalleryFile.status) == "processing")
            .values(
                status="imported",
                outfit_id=outfit_id,
                lease_until=None,
                next_attempt_at=None,
                last_error_stage=None,
                last_error_code=None,
                photo_type="outfit",
                photo_type_confidence=confidence,
                reason=_clip_reason(reason),
                capture_origin=capture_origin,
                updated_at=to_utc_iso(now),
            )
        )
        if session.execute(statement).rowcount != 1:
            raise RuntimeError("gallery file is no longer held by this confirmation")

    # -- listing and status (Req 17.1, 17.3) -------------------------------------

    def list_imports(self, limit: int, status: GalleryStatus | None = None) -> list[GalleryFileRow]:
        """Rows except baselines, ``updated_at DESC, id DESC`` (Req 17.3)."""
        with Session(self._engine) as session:
            query = select(GalleryFile).where(col(GalleryFile.status) != "baseline")
            if status is not None:
                query = query.where(GalleryFile.status == status)
            records = session.exec(
                query.order_by(col(GalleryFile.updated_at).desc(), col(GalleryFile.id).desc()).limit(limit)
            ).all()
            return [_row(r) for r in records]

    def counts(self, key: str) -> dict[str, int]:
        """Row count per Gallery_Status for one Gallery_Dir (every status present)."""
        result = dict.fromkeys(GALLERY_STATUSES, 0)
        with Session(self._engine) as session:
            rows = session.exec(
                select(GalleryFile.status, func.count())
                .where(GalleryFile.gallery_key == key)
                .group_by(GalleryFile.status)
            ).all()
        for status, count in rows:
            result[status] = count
        return result

    # -- user_photos (Req 3.2, 3.5) ----------------------------------------------

    def put_user_photo(
        self, sha256: str, file_name: str, gallery_file_name: str, estimated: bool, now: datetime
    ) -> None:
        values = {
            "sha256": sha256,
            "file_name": file_name,
            "gallery_file_name": gallery_file_name,
            "capture_estimated": estimated,
            "created_at": to_utc_iso(now),
        }
        statement = sqlite_insert(UserPhoto).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["sha256"],
            set_={k: statement.excluded[k] for k in values if k not in ("sha256", "created_at")},
        )
        with self._engine.begin() as conn:
            conn.execute(statement)

    def delete_user_photo(self, sha256: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(UserPhoto).where(col(UserPhoto.sha256) == sha256))

    def user_photo_file(self, sha256: str) -> str | None:
        with Session(self._engine) as session:
            record = session.get(UserPhoto, sha256)
            return None if record is None else record.file_name

    def user_photo_sha_for_file(self, file_name: str) -> str | None:
        """Which hash owns ``file_name`` in User_Photo_Dir, if any."""
        with Session(self._engine) as session:
            record = session.exec(select(UserPhoto).where(UserPhoto.file_name == file_name)).first()
            return None if record is None else record.sha256
