"""ImageIngestionService: scans PHOTO_DIR into Outfit records (design.md §3).

Per-file pipeline. The first failing step stops the file, records one
``ScanError`` and leaves no database rows (Req 1.6):

1. size from ``stat()`` (> 20MB → ``FILE_TOO_LARGE``; the file is not read, Req 1.12)
2. read the bytes and compute SHA-256 (Req 1.3)
3. duplicate check against the DB and against hashes *successfully stored* in
   this scan. Duplicates call no provider (Req 1.4). A hash whose earlier file
   failed in this scan is not in the set, so a later file is processed again;
   this is also why a rescan retries failed files (Req 1.7).
4. ``check_image``: Pillow decode + extension/format match (Req 1.8)
5. ``vision.analyze`` exactly once, no retry; non-outfit and uncertain images
   are reported without reading capture metadata or querying weather
6. capture metadata for accepted outfit photos: embedded EXIF (UserComment
   JSON, then standard EXIF), then the optional sidecar, combined by
   ``resolve_capture_metadata`` (Req 2.1, 2.10, 2.12, 2.16). Failures use
   ``METADATA_*``/``SIDECAR_*`` codes.
7. ``weather.get_weather`` (Req 4.1; Open-Meteo falls back to fixture internally)
8. ``repo.create_outfit_bundle`` in one transaction (Req 1.10)

Error messages and logs carry the file name only, never a server path.
The scan lock (409) lives in the API layer, not here.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain import UNKNOWN_TAGS, CaptureMetadata, ThermalVote, VisionAnalysis, WeatherReading
from app.ingestion.image_check import check_image, is_too_large, list_image_files, mime_type_for
from app.ingestion.metadata import (
    ScanFailure,
    metadata_source,
    read_embedded_metadata,
    resolve_capture_metadata,
)
from app.ingestion.sidecar import read_sidecar
from app.providers.vision.base import VisionError, VisionProvider
from app.providers.weather.base import WeatherProvider
from app.repository import OutfitCreate, OutfitRepository, StoredPhotoAnalysis
from app.schemas import FailureStage, ScanDecision, ScanError, ScanReport

logger = logging.getLogger(__name__)

PHOTO_DIR_UNAVAILABLE_MESSAGE = "사진 폴더에 접근할 수 없습니다. 설정을 확인해 주세요."


class PhotoDirUnavailable(Exception):
    """PHOTO_DIR is missing, not a directory, or not readable (Req 1.11).

    The message never contains the configured path. The API maps this to
    503 ``PHOTO_DIR_UNAVAILABLE``.
    """

    code = "PHOTO_DIR_UNAVAILABLE"

    def __init__(self, message: str = PHOTO_DIR_UNAVAILABLE_MESSAGE) -> None:
        super().__init__(message)


class _FileFailed(Exception):
    """Internal: stops processing of one file at a given stage."""

    def __init__(self, stage: FailureStage, code: str, message: str) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.message = message


class _Duplicate(Exception):
    """Internal: the file's hash is already stored."""


MIN_PHOTO_TYPE_CONFIDENCE = 0.70


def apply_confidence_gate(analysis: VisionAnalysis) -> VisionAnalysis:
    """The photo type the scan acts on; only ``outfit`` is stored as an Outfit.

    An ``outfit`` without garment evidence (``UNKNOWN_TAGS``) and an
    ``outfit``/``non_outfit`` below ``MIN_PHOTO_TYPE_CONFIDENCE`` become
    ``uncertain``. Public so the demo data scripts apply the same rule.
    """
    if analysis.photo_type == "outfit" and analysis.tags == UNKNOWN_TAGS:
        return analysis.model_copy(
            update={
                "photo_type": "uncertain",
                "reason": "The photo was labeled outfit but contains no garment evidence.",
            }
        )
    if (
        analysis.photo_type != "uncertain"
        and analysis.photo_type_confidence < MIN_PHOTO_TYPE_CONFIDENCE
    ):
        return analysis.model_copy(
            update={
                "photo_type": "uncertain",
                "reason": (
                    f"Photo-type confidence {analysis.photo_type_confidence:.2f} is below "
                    f"the {MIN_PHOTO_TYPE_CONFIDENCE:.2f} acceptance threshold."
                ),
            }
        )
    return analysis


class _NotOutfit(Exception):
    """Internal: a valid image should not be persisted as an Outfit."""

    def __init__(self, sha256: str, analysis: VisionAnalysis) -> None:
        self.sha256 = sha256
        self.analysis = analysis
        super().__init__(analysis.photo_type)


@dataclass
class _ScanState:
    report_errors: list[ScanError] = field(default_factory=list)
    report_decisions: list[ScanDecision] = field(default_factory=list)
    processed_hashes: set[str] = field(default_factory=set)
    scanned: int = 0
    succeeded: int = 0
    duplicate: int = 0
    skipped_non_outfit: int = 0
    uncertain: int = 0
    failed: int = 0

    def to_report(self) -> ScanReport:
        return ScanReport(
            scanned=self.scanned,
            succeeded=self.succeeded,
            duplicate=self.duplicate,
            skipped_non_outfit=self.skipped_non_outfit,
            uncertain=self.uncertain,
            failed=self.failed,
            errors=list(self.report_errors),
            decisions=list(self.report_decisions),
        )


_VISION_MESSAGES: dict[str, str] = {
    "VISION_SCHEMA_INVALID": "의류 분석 결과가 스키마를 만족하지 않습니다.",
    "VISION_API_ERROR": "의류 분석 API 호출에 실패했습니다.",
    "VISION_TIMEOUT": "의류 분석 시간이 초과되었습니다.",
}


def ensure_photo_dir(photo_dir: Path) -> None:
    """Raise ``PhotoDirUnavailable`` unless ``photo_dir`` is a readable directory."""
    try:
        ok = (
            photo_dir.exists()
            and photo_dir.is_dir()
            and os.access(photo_dir, os.R_OK | os.X_OK)
        )
    except OSError:
        ok = False
    if not ok:
        raise PhotoDirUnavailable()


class ImageIngestionService:
    def __init__(
        self,
        photo_dir: Path,
        vision: VisionProvider,
        weather: WeatherProvider,
        repo: OutfitRepository,
        tz: ZoneInfo,
    ) -> None:
        self._photo_dir = Path(photo_dir)
        self._vision = vision
        self._weather = weather
        self._repo = repo
        self._tz = tz

    def scan(self) -> ScanReport:
        """Scan the top level of PHOTO_DIR once and return the counts (Req 1.1, 1.2).

        Raises ``PhotoDirUnavailable`` before touching any data when the folder
        is not usable (Req 1.11). Per-file failures never abort the scan.
        """
        ensure_photo_dir(self._photo_dir)
        try:
            images = list_image_files(self._photo_dir)
        except OSError as exc:
            raise PhotoDirUnavailable() from exc

        state = _ScanState()
        for image in images:
            state.scanned += 1
            try:
                sha256 = self._process(image, state.processed_hashes)
            except _Duplicate:
                state.duplicate += 1
            except _NotOutfit as skipped:
                analysis = skipped.analysis
                state.processed_hashes.add(skipped.sha256)
                if analysis.photo_type == "non_outfit":
                    state.skipped_non_outfit += 1
                else:
                    state.uncertain += 1
                state.report_decisions.append(
                    ScanDecision(
                        file_name=image.name,
                        photo_type=analysis.photo_type,
                        confidence=analysis.photo_type_confidence,
                        reason=analysis.reason,
                    )
                )
            except _FileFailed as failure:
                state.failed += 1
                state.report_errors.append(
                    ScanError(
                        file_name=image.name,
                        stage=failure.stage,
                        code=failure.code,
                        message=failure.message,
                    )
                )
                logger.warning(
                    "스캔 실패: %s (%s/%s)", image.name, failure.stage, failure.code
                )
            else:
                state.succeeded += 1
                state.processed_hashes.add(sha256)

        report = state.to_report()
        logger.info(
            "스캔 완료: scanned=%d succeeded=%d duplicate=%d non_outfit=%d uncertain=%d failed=%d",
            report.scanned,
            report.succeeded,
            report.duplicate,
            report.skipped_non_outfit,
            report.uncertain,
            report.failed,
        )
        return report

    # -- one file -------------------------------------------------------------

    def _process(self, image: Path, processed_hashes: set[str]) -> str:
        """Run the pipeline for one file and return its SHA-256 on success."""
        data = self._read_bytes(image)
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 in processed_hashes:
            raise _Duplicate()

        format_error = check_image(data, image.suffix)
        if format_error is not None:
            raise _FileFailed("validation", format_error.code, format_error.message)

        stored = self._get_stored_analysis(sha256)
        outfit_exists = self._exists(sha256)
        analysis: VisionAnalysis | None = None

        if stored is not None:
            if stored.analysis.photo_type == "non_outfit":
                raise _NotOutfit(sha256, stored.analysis)
            if stored.analysis.photo_type == "outfit":
                if outfit_exists:
                    raise _Duplicate()
                # A previous sidecar/weather/storage failure happened after a
                # successful Vision call. Reuse that result on retry.
                analysis = stored.analysis
        elif outfit_exists:
            # Defensive fallback for an Outfit inserted outside the repository
            # or before startup backfill completed.
            raise _Duplicate()

        if analysis is None:
            # Explicitly retry persisted uncertain decisions; a later model or
            # clearer result may promote them to outfit/non_outfit.
            analysis = apply_confidence_gate(self._analyze(image, data, sha256))
            self._save_analysis(image, sha256, analysis)

            if outfit_exists and analysis.photo_type == "outfit":
                self._update_existing_outfit(image, sha256, analysis)
                raise _Duplicate()

        if analysis.photo_type != "outfit":
            raise _NotOutfit(sha256, analysis)

        capture, seed_vote = self._capture_metadata(image, data)

        reading = self._get_weather(image, capture)

        outfit = OutfitCreate(
            sha256=sha256,
            file_name=image.name,
            captured_at=capture.captured_at,
            latitude=capture.latitude,
            longitude=capture.longitude,
            tags=analysis.tags,
            vision_provider=self._vision.name,
            photo_type_confidence=analysis.photo_type_confidence,
            photo_type_reason=analysis.reason,
            source=metadata_source(capture),
            metadata_synthetic=capture.synthetic,
        )
        try:
            self._repo.create_outfit_bundle(outfit, reading, seed_vote)
        except Exception as exc:  # repository already rolled back
            logger.warning("저장 실패: %s (%s)", image.name, type(exc).__name__)
            raise _FileFailed(
                "storage", "STORAGE_ERROR", "착장 기록을 저장하지 못했습니다."
            ) from None
        return sha256

    def _capture_metadata(
        self, image: Path, data: bytes
    ) -> tuple[CaptureMetadata, ThermalVote | None]:
        """Capture time/location and seed vote (Req 2.1, 2.12, 2.16).

        Embedded EXIF first; the optional sidecar supplies the seed vote and,
        only when the embedded metadata is missing or invalid, the location.
        """
        embedded = read_embedded_metadata(data)
        sidecar = read_sidecar(
            image, require_location=not isinstance(embedded, CaptureMetadata)
        )
        resolved = resolve_capture_metadata(embedded, sidecar)
        if isinstance(resolved, ScanFailure):
            raise _FileFailed("validation", resolved.code, resolved.message)
        return resolved

    def _read_bytes(self, image: Path) -> bytes:
        try:
            if is_too_large(image):  # stat only, content not read (Req 1.12)
                raise _FileFailed(
                    "validation", "FILE_TOO_LARGE", "이미지 파일 크기가 20MB를 초과합니다."
                )
            return image.read_bytes()
        except OSError:
            raise _FileFailed(
                "validation", "FILE_READ_ERROR", "이미지 파일을 읽을 수 없습니다."
            ) from None

    def _exists(self, sha256: str) -> bool:
        try:
            return self._repo.exists_sha(sha256)
        except Exception as exc:
            logger.warning("중복 확인 실패 (%s)", type(exc).__name__)
            raise _FileFailed(
                "storage", "STORAGE_ERROR", "중복 여부를 확인하지 못했습니다."
            ) from None

    def _get_stored_analysis(self, sha256: str) -> StoredPhotoAnalysis | None:
        try:
            return self._repo.get_photo_analysis(sha256)
        except Exception as exc:
            logger.warning("사진 판정 조회 실패 (%s)", type(exc).__name__)
            raise _FileFailed(
                "storage", "STORAGE_ERROR", "기존 사진 판정을 확인하지 못했습니다."
            ) from None

    def _save_analysis(
        self, image: Path, sha256: str, analysis: VisionAnalysis
    ) -> None:
        try:
            self._repo.save_photo_analysis(
                sha256, image.name, analysis, self._vision.name
            )
        except Exception as exc:
            logger.warning("사진 판정 저장 실패: %s (%s)", image.name, type(exc).__name__)
            raise _FileFailed(
                "storage", "STORAGE_ERROR", "사진 판정 결과를 저장하지 못했습니다."
            ) from None

    def _update_existing_outfit(
        self, image: Path, sha256: str, analysis: VisionAnalysis
    ) -> None:
        try:
            self._repo.update_existing_outfit_analysis(
                sha256, analysis, self._vision.name
            )
        except Exception as exc:
            logger.warning("기존 착장 갱신 실패: %s (%s)", image.name, type(exc).__name__)
            raise _FileFailed(
                "storage", "STORAGE_ERROR", "기존 착장 분석을 갱신하지 못했습니다."
            ) from None

    def _analyze(self, image: Path, data: bytes, sha256: str) -> VisionAnalysis:
        """Classify and analyze with exactly one provider call and no retry."""
        try:
            return self._vision.analyze(data, mime_type_for(image.suffix), sha256)
        except VisionError as exc:
            code = exc.code
        except Exception as exc:  # unexpected provider bug: treat as API error
            logger.warning("Vision 예외: %s (%s)", image.name, type(exc).__name__)
            code = "VISION_API_ERROR"
        raise _FileFailed("analysis", code, _VISION_MESSAGES[code])

    def _get_weather(self, image: Path, metadata: CaptureMetadata) -> WeatherReading:
        """Weather at capture time (Req 4.1).

        Providers are expected not to raise (Open-Meteo falls back to fixture).
        An unexpected exception still fails only this file.
        """
        try:
            return self._weather.get_weather(
                metadata.captured_at, metadata.latitude, metadata.longitude
            )
        except Exception as exc:
            logger.warning("날씨 조회 예외: %s (%s)", image.name, type(exc).__name__)
            raise _FileFailed(
                "analysis", "WEATHER_ERROR", "촬영 시점 날씨를 가져오지 못했습니다."
            ) from None
