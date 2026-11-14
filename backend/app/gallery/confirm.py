""""착장 맞음": store an ``uncertain`` watched photo as an Outfit (design.md §6, Req 6.12–6.15).

``OutfitConfirmService.confirm()``:

1. Claim the row (``uncertain`` → ``processing``); any other status → ``NOT_CONFIRMABLE``.
2. Re-read the Gallery_Dir original and compare its SHA-256 with the record.
   Missing or different bytes → ``SOURCE_MISSING`` (409): the user must not
   confirm a photo other than the one they saw.
3. Copy it into User_Photo_Dir again (the ``uncertain`` copy was removed,
   Req 3.4), with an estimated sidecar when it has no Embedded_Metadata.
4. ``ingest_file(copy, analysis_override=confirmed_analysis(stored), before_commit=…)``:
   metadata → weather → one transaction. Vision is never called. Inside that
   transaction ``create_outfit_bundle`` turns the ``photo_analyses`` decision
   into ``outfit``; ``before_commit`` adds the ``photo_confirmations`` audit row
   and marks the record ``imported`` (Req 6.13). Any failure rolls all three back.
5. On failure the copy is removed and the record goes back to ``uncertain``.

No retry schedule applies: nothing calls Vision.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.db import Outfit, to_utc_iso
from app.db_automation import PhotoConfirmation
from app.domain import UNKNOWN_TAGS, VisionAnalysis
from app.gallery.processor import GalleryImportService, StagedCopy
from app.gallery.store import GalleryFileRow, GalleryStore, gallery_key
from app.ingestion.image_check import MAX_IMAGE_BYTES
from app.ingestion.service import Duplicate, ImageIngestionService, Stored
from app.repository import OutfitRepository

logger = logging.getLogger(__name__)

CONFIRMED_REASON = "user confirmed"


class ConfirmOutcome(enum.Enum):
    CONFIRMED = "confirmed"  # 200, record is imported
    NOT_FOUND = "not_found"  # 404 GALLERY_IMPORT_NOT_FOUND
    NOT_CONFIRMABLE = "not_confirmable"  # 409, not uncertain (or already confirmed)
    SOURCE_MISSING = "source_missing"  # 409, original missing or changed
    FAILED = "failed"  # 500 STORAGE_ERROR, record stays uncertain


def confirmed_analysis(stored: VisionAnalysis | None) -> VisionAnalysis:
    """The ``outfit`` decision built from the stored one; Photo_Gate is not applied.

    Confidence is the stored value (0.0 without one), the reason is
    ``"user confirmed"`` and the tags are the stored tags (``UNKNOWN_TAGS``
    without one). When all three garment types are ``unknown`` the tag
    confidence is stored as 0.0, so Phase 1 ``is_non_outfit`` keeps the Outfit
    out of recommendations and the client shows "태그가 없어 추천에는 쓰이지
    않아요" (Req 6.15).
    """
    tags = stored.tags.model_copy(deep=True) if stored is not None else UNKNOWN_TAGS.model_copy(deep=True)
    if tags.upper_type == tags.outer_type == tags.lower_type == "unknown":
        tags = tags.model_copy(update={"confidence": 0.0})
    return VisionAnalysis(
        photo_type="outfit",
        photo_type_confidence=stored.photo_type_confidence if stored is not None else 0.0,
        reason=CONFIRMED_REASON,
        tags=tags,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OutfitConfirmService:
    def __init__(
        self,
        importer: GalleryImportService,
        store: GalleryStore,
        repo: OutfitRepository,
        ingestion: ImageIngestionService,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._importer = importer
        self._store = store
        self._repo = repo
        self._ingestion = ingestion
        self._clock = clock
        self._key = gallery_key(importer.gallery_dir)

    def confirm(self, row_id: int, now: datetime | None = None) -> tuple[ConfirmOutcome, GalleryFileRow | None]:
        """Confirm one record; returns the outcome and the record as it is afterwards."""
        now = now or self._clock()
        row = self._store.get(row_id)
        if row is None:
            return ConfirmOutcome.NOT_FOUND, None
        if row.status != "uncertain" or not self._store.claim_uncertain(row_id, now):
            return ConfirmOutcome.NOT_CONFIRMABLE, self._store.get(row_id)
        try:
            outcome = self._confirm_claimed(row, now)
        except Exception as exc:
            logger.warning("착장 확정 예외: %s (%s)", row.file_name, type(exc).__name__)
            outcome = ConfirmOutcome.FAILED
        if outcome is not ConfirmOutcome.CONFIRMED:
            self._store.release_to_uncertain(row_id, self._clock())
        logger.info("착장 확정: %s → %s", row.file_name, outcome.value)
        return outcome, self._store.get(row_id)

    def _confirm_claimed(self, row: GalleryFileRow, now: datetime) -> ConfirmOutcome:
        data = self._read_original(row)
        if data is None:
            return ConfirmOutcome.SOURCE_MISSING
        sha256 = row.sha256
        assert sha256 is not None

        stored = self._repo.get_photo_analysis(sha256)
        previous_type, previous_confidence, previous_reason = self._previous(row, stored)
        analysis = confirmed_analysis(stored.analysis if stored is not None else None)

        copy = self._importer.stage_copy(row.file_name, data, sha256, row.mtime_ns, now)
        try:
            self._store.put_user_photo(
                sha256, copy.path.name, row.file_name, copy.capture_origin == "estimated", now
            )

            def write_audit(session: Session) -> None:
                outfit_id = session.exec(select(Outfit.id).where(Outfit.sha256 == sha256)).one()
                session.add(
                    PhotoConfirmation(
                        sha256=sha256,
                        gallery_file_id=row.id,
                        outfit_id=outfit_id,
                        previous_photo_type=previous_type,
                        previous_confidence=previous_confidence,
                        previous_reason=previous_reason,
                        source="user_action",
                        confirmed_at=to_utc_iso(now),
                    )
                )
                GalleryStore.mark_confirmed_in(
                    session, row.id, outfit_id, now,
                    capture_origin=copy.capture_origin,
                    confidence=analysis.photo_type_confidence,
                    reason=analysis.reason,
                )

            result = self._ingestion.ingest_file(
                copy.path, analysis_override=analysis, before_commit=write_audit
            )
        except Exception:
            self._discard(sha256, copy)
            raise
        if isinstance(result, Stored):
            return ConfirmOutcome.CONFIRMED
        if isinstance(result, Duplicate):
            # An Outfit with these bytes already exists: nothing new is stored.
            self._importer.discard_copy(sha256, copy, keep_if_referenced=True)
            return ConfirmOutcome.NOT_CONFIRMABLE
        self._discard(sha256, copy)
        code = getattr(result, "code", "UNKNOWN")
        logger.warning("착장 확정 저장 실패: %s (%s)", row.file_name, code)
        return ConfirmOutcome.FAILED

    def _discard(self, sha256: str, copy: StagedCopy) -> None:
        try:
            self._importer.discard_copy(sha256, copy)
        except Exception as exc:
            logger.warning("확정 복사본 정리 실패 (%s)", type(exc).__name__)

    def _read_original(self, row: GalleryFileRow) -> bytes | None:
        """Gallery_Dir bytes with the recorded hash, or None (missing, replaced or changed)."""
        if row.sha256 is None or row.gallery_key != self._key:
            return None
        source = self._importer.gallery_dir / row.file_name
        try:
            info = os.lstat(source)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
                return None
            data = source.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != row.sha256:
            return None
        return data

    @staticmethod
    def _previous(row: GalleryFileRow, stored) -> tuple[str, float | None, str | None]:  # noqa: ANN001
        if stored is not None:
            analysis = stored.analysis
            return analysis.photo_type, analysis.photo_type_confidence, analysis.reason
        return row.photo_type or "uncertain", row.photo_type_confidence, row.reason
