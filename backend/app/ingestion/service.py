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
5. sidecar read and validation (Req 2.1, 2.10)
6. ``vision.analyze`` exactly once, no retry (Req 3.1, 3.5)
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

from app.domain import ClothingTags, SidecarMetadata, WeatherReading
from app.ingestion.image_check import check_image, is_too_large, list_image_files, mime_type_for
from app.ingestion.sidecar import SidecarError, read_sidecar
from app.providers.vision.base import VisionError, VisionProvider
from app.providers.weather.base import WeatherProvider
from app.repository import OutfitCreate, OutfitRepository
from app.schemas import FailureStage, ScanError, ScanReport

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


@dataclass
class _ScanState:
    report_errors: list[ScanError] = field(default_factory=list)
    stored_hashes: set[str] = field(default_factory=set)
    scanned: int = 0
    succeeded: int = 0
    duplicate: int = 0
    failed: int = 0

    def to_report(self) -> ScanReport:
        return ScanReport(
            scanned=self.scanned,
            succeeded=self.succeeded,
            duplicate=self.duplicate,
            failed=self.failed,
            errors=list(self.report_errors),
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
                sha256 = self._process(image, state.stored_hashes)
            except _Duplicate:
                state.duplicate += 1
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
                state.stored_hashes.add(sha256)

        report = state.to_report()
        logger.info(
            "스캔 완료: scanned=%d succeeded=%d duplicate=%d failed=%d",
            report.scanned,
            report.succeeded,
            report.duplicate,
            report.failed,
        )
        return report

    # -- one file -------------------------------------------------------------

    def _process(self, image: Path, stored_hashes: set[str]) -> str:
        """Run the pipeline for one file and return its SHA-256 on success."""
        data = self._read_bytes(image)
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 in stored_hashes or self._exists(sha256):
            raise _Duplicate()

        format_error = check_image(data, image.suffix)
        if format_error is not None:
            raise _FileFailed("validation", format_error.code, format_error.message)

        metadata = read_sidecar(image)
        if isinstance(metadata, SidecarError):
            raise _FileFailed("validation", metadata.code, metadata.message)

        tags = self._analyze(image, data, sha256)
        reading = self._get_weather(image, metadata)

        outfit = OutfitCreate(
            sha256=sha256,
            file_name=image.name,
            captured_at=metadata.captured_at,
            latitude=metadata.latitude,
            longitude=metadata.longitude,
            tags=tags,
            vision_provider=self._vision.name,
        )
        try:
            self._repo.create_outfit_bundle(outfit, reading, metadata.thermal_vote)
        except Exception as exc:  # repository already rolled back
            logger.warning("저장 실패: %s (%s)", image.name, type(exc).__name__)
            raise _FileFailed(
                "storage", "STORAGE_ERROR", "착장 기록을 저장하지 못했습니다."
            ) from None
        return sha256

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

    def _analyze(self, image: Path, data: bytes, sha256: str) -> ClothingTags:
        """Call the Vision provider exactly once; never retry (Req 3.1, 3.5)."""
        try:
            return self._vision.analyze(data, mime_type_for(image.suffix), sha256)
        except VisionError as exc:
            code = exc.code
        except Exception as exc:  # unexpected provider bug: treat as API error
            logger.warning("Vision 예외: %s (%s)", image.name, type(exc).__name__)
            code = "VISION_API_ERROR"
        raise _FileFailed("analysis", code, _VISION_MESSAGES[code])

    def _get_weather(self, image: Path, metadata: SidecarMetadata) -> WeatherReading:
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
