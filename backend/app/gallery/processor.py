"""GalleryImportService: one Stable_File through the Phase 1 pipeline (design.md §6).

``process()`` steps, stopping at the first failure:

1. Re-check the file in Gallery_Dir (``lstat``). Missing → ``failed``
   ``GALLERY_FILE_MISSING``; a changed signature → ``deferred`` (the tracker
   registers the new version once it is stable). Read the bytes (stat-only
   size check first, Req 1.12) and compute SHA-256.
2. Duplicates (Req 2.6, 2.7): an Outfit with the hash, or another Gallery_File
   with the hash that is ``imported``/``uncertain``/being collected →
   ``duplicate``; a stored ``non_outfit`` decision → ``skipped_non_outfit``.
   Neither calls Vision or weather.
3. Copy byte for byte into User_Photo_Dir (hidden ``.partial`` → ``fsync`` →
   ``os.replace``), re-hash the copy (Req 3.1). A name taken by different bytes
   becomes ``<stem>_<sha8><ext>`` (Req 3.2).
4. No valid Embedded_Metadata → Estimated_Capture sidecar next to the copy
   (Req 5.2, 5.3). Gallery_Dir sidecars are never read or copied (Req 5.4).
5. ``ingestion.ingest_file(copy)``: Vision once → Photo_Gate → (outfit)
   metadata → weather → one-transaction save.
6. ``Stored`` → ``imported``; ``NotOutfit`` → ``skipped_non_outfit``/``uncertain``
   and the copy, sidecar and ``user_photos`` row are removed (Req 3.4);
   ``Failed`` → ``failure_transition()``: ``retry_wait`` keeps the copy for the
   next attempt, final ``failed`` removes it.

Logs carry the file name, status, stage and code only (Req 6.10). Nothing is
written to Photo_Dir, the manifest or the demo fixtures (Req 4.1).
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from app.gallery.estimate import estimate_for_image, estimated_sidecar_json
from app.gallery.rules import Signature
from app.gallery.store import CaptureOrigin, GalleryFileRow, GalleryStore, ImportOutcome
from app.ingestion.image_check import MAX_IMAGE_BYTES
from app.ingestion.service import Duplicate, Failed, ImageIngestionService, NotOutfit, Stored
from app.ingestion.sidecar import SIDECAR_SUFFIX
from app.location import LatLon, LocationService
from app.repository import OutfitRepository
from app.schemas import ScanDecision

__all__ = [
    "MAX_ATTEMPTS",
    "RETRYABLE_CODES",
    "RETRY_DELAYS",
    "GalleryImportService",
    "ImportOutcome",
    "StagedCopy",
    "failure_transition",
]

logger = logging.getLogger(__name__)

RETRYABLE_CODES = frozenset(
    {
        "VISION_API_ERROR",
        "VISION_TIMEOUT",
        "VISION_SCHEMA_INVALID",
        "STORAGE_ERROR",
        "WEATHER_ERROR",
        "FILE_READ_ERROR",
        "GALLERY_COPY_ERROR",
    }
)
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
MAX_ATTEMPTS = 3


def failure_transition(
    code: str, attempts_done: int, now: datetime
) -> tuple[Literal["retry_wait", "failed"], datetime | None]:
    """Retry schedule (Req 6.5, 6.6, 6.11).

    A retryable code before the third attempt waits 1 minute after the first
    failure and 5 minutes after the second; anything else is final.
    """
    if code in RETRYABLE_CODES and 1 <= attempts_done < MAX_ATTEMPTS:
        return "retry_wait", now + RETRY_DELAYS[attempts_done - 1]
    return "failed", None


class _Stop(Exception):
    """Internal: ends ``process`` with an outcome."""

    def __init__(self, outcome: ImportOutcome) -> None:
        super().__init__(outcome.status)
        self.outcome = outcome


@dataclass(frozen=True)
class StagedCopy:
    """A verified byte-for-byte copy in User_Photo_Dir (+ its estimated sidecar, if any)."""

    path: Path
    sidecar: Path
    capture_origin: CaptureOrigin


def _signature_of(st: os.stat_result) -> Signature:
    return Signature(size=st.st_size, mtime_ns=st.st_mtime_ns)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write_atomic(folder: Path, name: str, data: bytes) -> Path:
    """Write ``data`` to ``folder/name`` through a hidden ``.partial`` file."""
    target = folder / name
    temp = folder / f".{name}.partial"
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        _unlink(temp)
        raise
    return target


class GalleryImportService:
    def __init__(
        self,
        gallery_dir: Path,
        user_photo_dir: Path,
        store: GalleryStore,
        repo: OutfitRepository,
        ingestion: ImageIngestionService,
        location: LocationService,
        tz: ZoneInfo,
    ) -> None:
        self._gallery_dir = Path(gallery_dir)
        self._user_photo_dir = Path(user_photo_dir)
        self._store = store
        self._repo = repo
        self._ingestion = ingestion
        self._location = location
        self._tz = tz

    @property
    def gallery_dir(self) -> Path:
        return self._gallery_dir

    @property
    def user_photo_dir(self) -> Path:
        return self._user_photo_dir

    # -- public ------------------------------------------------------------------

    def process(self, row: GalleryFileRow, now: datetime) -> ImportOutcome:
        """Process one claimed row (``row.attempts`` counts this attempt) and return its outcome."""
        state: dict[str, object] = {}
        try:
            outcome = self._process(row, now, state)
        except _Stop as stop:
            outcome = stop.outcome
        except Exception as exc:  # unexpected: one retryable storage failure (Req 1.8)
            logger.warning("갤러리 사진 처리 예외: %s (%s)", row.file_name, type(exc).__name__)
            outcome = self._failure(row, now, "storage", "STORAGE_ERROR", state)
        logger.info(
            "갤러리 사진 처리: %s → %s%s",
            row.file_name,
            outcome.status,
            f" ({outcome.stage}/{outcome.code})" if outcome.code else "",
        )
        return outcome

    # -- steps -------------------------------------------------------------------

    def _process(self, row: GalleryFileRow, now: datetime, state: dict[str, object]) -> ImportOutcome:
        source = self._gallery_dir / row.file_name
        data = self._read_source(row, source, now, state)
        sha256 = hashlib.sha256(data).hexdigest()
        state["sha256"] = sha256
        self._guard_storage(row, now, state, lambda: self._store.record_sha(row.id, sha256, now))

        self._check_duplicates(row, sha256, now, state)

        try:
            copy = self.stage_copy(row.file_name, data, sha256, row.mtime_ns, now)
        except OSError:
            raise _Stop(self._failure(row, now, "copy", "GALLERY_COPY_ERROR", state)) from None
        state["copy"] = copy
        origin = copy.capture_origin
        state["capture_origin"] = origin
        self._guard_storage(
            row, now, state,
            lambda: self._store.put_user_photo(sha256, copy.path.name, row.file_name, origin == "estimated", now),
        )

        result = self._ingestion.ingest_file(copy.path)
        if isinstance(result, Stored):
            confidence, reason = self._accepted(result.sha256)
            return ImportOutcome(
                status="imported", sha256=sha256, outfit_id=result.outfit_id,
                capture_origin=origin, accepted_confidence=confidence, accepted_reason=reason,
            )
        if isinstance(result, Duplicate):
            self.discard_copy(sha256, copy, keep_if_referenced=True)
            return ImportOutcome(status="duplicate", sha256=sha256, capture_origin=origin)
        if isinstance(result, NotOutfit):
            self.discard_copy(sha256, copy)
            analysis = result.analysis
            photo_type = "non_outfit" if analysis.photo_type == "non_outfit" else "uncertain"
            return ImportOutcome(
                status="skipped_non_outfit" if photo_type == "non_outfit" else "uncertain",
                sha256=sha256,
                capture_origin=origin,
                decision=ScanDecision(
                    file_name=row.file_name,
                    photo_type=photo_type,
                    confidence=analysis.photo_type_confidence,
                    reason=analysis.reason,
                ),
            )
        assert isinstance(result, Failed)
        return self._failure(row, now, result.stage, result.code, state)

    def _read_source(self, row: GalleryFileRow, source: Path, now: datetime, state: dict[str, object]) -> bytes:
        try:
            before = os.lstat(source)
        except FileNotFoundError:
            raise _Stop(ImportOutcome(status="failed", stage="validation", code="GALLERY_FILE_MISSING")) from None
        except OSError:
            raise _Stop(self._failure(row, now, "validation", "FILE_READ_ERROR", state)) from None
        if not stat.S_ISREG(before.st_mode):  # replaced by a symlink or a folder (Req 1.6)
            raise _Stop(ImportOutcome(status="failed", stage="validation", code="GALLERY_FILE_MISSING"))
        if _signature_of(before) != row.signature:
            raise _Stop(ImportOutcome(status="deferred"))
        if before.st_size > MAX_IMAGE_BYTES:  # stat only, not read (Req 1.12)
            raise _Stop(ImportOutcome(status="failed", stage="validation", code="FILE_TOO_LARGE"))
        try:
            data = source.read_bytes()
            after = os.lstat(source)
        except FileNotFoundError:
            raise _Stop(ImportOutcome(status="failed", stage="validation", code="GALLERY_FILE_MISSING")) from None
        except OSError:
            raise _Stop(self._failure(row, now, "validation", "FILE_READ_ERROR", state)) from None
        if _signature_of(after) != row.signature or len(data) != row.size_bytes:
            raise _Stop(ImportOutcome(status="deferred"))
        return data

    def _check_duplicates(self, row: GalleryFileRow, sha256: str, now: datetime, state: dict[str, object]) -> None:
        def lookup() -> ImportOutcome | None:
            if self._repo.exists_sha(sha256) or self._store.active_with_sha(sha256, exclude_id=row.id):
                return ImportOutcome(status="duplicate", sha256=sha256)
            stored = self._repo.get_photo_analysis(sha256)
            if stored is not None and stored.analysis.photo_type == "non_outfit":
                return ImportOutcome(
                    status="skipped_non_outfit",
                    sha256=sha256,
                    decision=ScanDecision(
                        file_name=row.file_name,
                        photo_type="non_outfit",
                        confidence=stored.analysis.photo_type_confidence,
                        reason=stored.analysis.reason,
                    ),
                )
            return None

        outcome = self._guard_storage(row, now, state, lookup)
        if outcome is not None:
            raise _Stop(outcome)

    def stage_copy(
        self, file_name: str, data: bytes, sha256: str, mtime_ns: int, now: datetime
    ) -> StagedCopy:
        """Copy ``data`` into User_Photo_Dir and add an estimated sidecar when needed.

        Steps 3–4 of ``process``; also used by the uncertain confirmation
        (Req 6.12). The copy is re-hashed after the rename (Req 3.1). Raises
        ``OSError`` (``GALLERY_COPY_ERROR``); a copy written by this call is
        removed again when the sidecar cannot be written.
        """
        self._user_photo_dir.mkdir(parents=True, exist_ok=True)
        name, reuse = self._copy_name(file_name, sha256)
        target = self._user_photo_dir / name
        if not reuse:
            _write_atomic(self._user_photo_dir, name, data)
        if _sha256_file(target) != sha256:
            _unlink(target)
            raise OSError("copy hash mismatch")
        sidecar = target.with_name(target.stem + SIDECAR_SUFFIX)
        try:
            origin = self._write_estimate(data, mtime_ns, sidecar, now)
        except OSError:
            if not reuse:
                _unlink(target)
            raise
        return StagedCopy(path=target, sidecar=sidecar, capture_origin=origin)

    def _copy_name(self, file_name: str, sha256: str) -> tuple[str, bool]:
        """(name in User_Photo_Dir, True when an identical copy is already there)."""
        stem, ext = os.path.splitext(file_name)
        for candidate in (file_name, f"{stem}_{sha256[:8]}{ext}", f"{stem}_{sha256}{ext}"):
            usable = self._name_state(candidate, sha256)
            if usable is not None:
                return candidate, usable
        raise OSError("no free copy name")

    def _name_state(self, name: str, sha256: str) -> bool | None:
        """None: taken by other bytes. False: free. True: the same bytes are already there."""
        target = self._user_photo_dir / name
        owner = self._store.user_photo_sha_for_file(name)
        if owner is not None and owner != sha256:
            return None
        if target.exists():
            if not target.is_file() or target.is_symlink():
                return None
            return True if _sha256_file(target) == sha256 else None
        # A sidecar without its image belongs to another photo with the same stem.
        if (self._user_photo_dir / (target.stem + SIDECAR_SUFFIX)).exists():
            return None
        return False

    def _write_estimate(self, data: bytes, mtime_ns: int, sidecar: Path, now: datetime) -> CaptureOrigin:
        """Step 4: Estimated_Capture sidecar unless the image has valid Embedded_Metadata (Req 5)."""
        last = self._location.last()
        estimate = estimate_for_image(
            data,
            datetime.fromtimestamp(mtime_ns / 1_000_000_000, UTC),
            now,
            None if last is None else LatLon(last.latitude, last.longitude),
            self._location.default,
            self._tz,
        )
        if estimate is None:
            return "exif"
        _write_atomic(sidecar.parent, sidecar.name, estimated_sidecar_json(estimate).encode("utf-8"))
        return "estimated"

    # -- outcomes and cleanup --------------------------------------------------

    def _guard_storage(self, row: GalleryFileRow, now: datetime, state: dict[str, object], action):  # noqa: ANN001, ANN202
        try:
            return action()
        except Exception as exc:
            logger.warning("갤러리 기록 저장 실패: %s (%s)", row.file_name, type(exc).__name__)
            raise _Stop(self._failure(row, now, "storage", "STORAGE_ERROR", state)) from None

    def _failure(
        self, row: GalleryFileRow, now: datetime, stage: str, code: str, state: dict[str, object]
    ) -> ImportOutcome:
        status, next_attempt_at = failure_transition(code, max(row.attempts, 1), now)
        sha256 = state.get("sha256")
        copy = state.get("copy")
        if status == "failed" and isinstance(copy, StagedCopy) and isinstance(sha256, str):
            try:
                self.discard_copy(sha256, copy)
            except Exception as exc:  # cleanup must not hide the failure
                logger.warning("복사본 정리 실패: %s (%s)", row.file_name, type(exc).__name__)
        return ImportOutcome(
            status=status,
            sha256=sha256 if isinstance(sha256, str) else None,
            stage=stage,
            code=code,
            capture_origin=state.get("capture_origin"),  # type: ignore[arg-type]
            next_attempt_at=next_attempt_at,
        )

    def discard_copy(self, sha256: str, copy: StagedCopy, *, keep_if_referenced: bool = False) -> None:
        """Remove this attempt's copy, its sidecar and its ``user_photos`` row (Req 3.4, 3.7).

        With ``keep_if_referenced`` (a ``Duplicate`` race: an Outfit with this
        hash appeared meanwhile) a ``user_photos`` row pointing at this copy is
        kept, because that Outfit may be served from it.
        """
        owned = self._store.user_photo_file(sha256) == copy.path.name
        if keep_if_referenced and owned:
            return
        if owned:
            self._store.delete_user_photo(sha256)
        if self._store.user_photo_sha_for_file(copy.path.name) is None:
            _unlink(copy.path)
            _unlink(copy.sidecar)

    def _accepted(self, sha256: str) -> tuple[float | None, str | None]:
        try:
            stored = self._repo.get_photo_analysis(sha256)
        except Exception:
            return None, None
        if stored is None:
            return None, None
        return stored.analysis.photo_type_confidence, stored.analysis.reason
