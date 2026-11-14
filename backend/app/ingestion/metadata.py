"""Embedded capture metadata reader (design.md "Metadata_Reader", Req 2.1, 2.8, 2.12~2.17).

Pure functions, no file or network I/O:

- ``read_embedded_metadata(data)`` reads the capture time and location stored
  in the image's own EXIF. Sources in priority order:

  1. EXIF UserComment (Exif IFD 0x8769, tag 0x9286): an 8-byte character code
     header (``ASCII\\0\\0\\0``) followed by a JSON object with ``date``,
     ``time``, ``utc_offset`` or ``timezone``, ``latitude``, ``longitude`` and
     ``synthetic``. Every other key (``location``, ``outfit``, ``prompt`` ...)
     is discarded (Req 2.11, 2.17).
  2. Standard EXIF: ``DateTimeOriginal`` + ``OffsetTimeOriginal`` and the GPS
     IFD (degrees/minutes/seconds, N/S/E/W refs). ``GPSProcessingMethod``
     ``SYNTHETIC`` marks synthetic metadata.

  Returns ``None`` when neither source exists, a ``MetadataError`` when a
  source exists but is invalid (the first-priority error wins), and the first
  valid source otherwise.

- ``resolve_capture_metadata(embedded, sidecar)`` combines the embedded result
  with the optional sidecar following the table in design.md.

Pillow's ``getexif()`` returns JPEG APP1, PNG ``eXIf`` and WebP EXIF data
through the same API, so there is no per-format branch.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from io import BytesIO
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image

from app.domain import CaptureMetadata, MetadataSource, SidecarMetadata, ThermalVote
from app.ingestion.sidecar import SidecarError

logger = logging.getLogger(__name__)

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_USER_COMMENT = 0x9286
TAG_DATETIME_ORIGINAL = 0x9003
TAG_OFFSET_TIME_ORIGINAL = 0x9011
GPS_LATITUDE_REF = 1
GPS_LATITUDE = 2
GPS_LONGITUDE_REF = 3
GPS_LONGITUDE = 4
GPS_PROCESSING_METHOD = 0x1B

HEADER_ASCII = b"ASCII\x00\x00\x00"
HEADER_UNICODE = b"UNICODE\x00"
HEADER_LENGTH = 8

MetadataErrorCode = Literal[
    "METADATA_NOT_FOUND",
    "METADATA_PARSE_ERROR",
    "METADATA_MISSING_FIELD",
    "METADATA_CAPTURED_AT_INVALID",
    "METADATA_OUT_OF_RANGE",
]
EmbeddedOrigin = Literal["user_comment", "exif_standard"]

COORDINATE_RANGES: dict[str, tuple[int, int]] = {
    "latitude": (-90, 90),
    "longitude": (-180, 180),
}
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):(\d{2})$")
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"


@dataclass(frozen=True)
class MetadataError:
    """Embedded metadata exists but is invalid. ``field`` is set for field codes."""

    code: MetadataErrorCode
    message: str  # Korean, never contains the input value
    origin: EmbeddedOrigin
    field: str | None = None


@dataclass(frozen=True)
class ScanFailure:
    """No usable capture time/location for a file (validation stage)."""

    code: str  # METADATA_* or SIDECAR_*
    message: str
    field: str | None = None


# -- small helpers -------------------------------------------------------------


def _is_number(value: object) -> bool:
    # bool is a subclass of int; JSON true/false must be rejected.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coordinate(value: object, low: int, high: int) -> float | None:
    if not _is_number(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not (low <= value <= high):  # type: ignore[operator]
        return None
    return float(value)  # type: ignore[arg-type]


def _parse_offset(value: object) -> timezone | None:
    """``±HH:MM`` → fixed-offset tzinfo. Anything else → None."""
    if not isinstance(value, str):
        return None
    match = _OFFSET_RE.match(value.strip())
    if match is None:
        return None
    sign, hours, minutes = match.groups()
    h, m = int(hours), int(minutes)
    if h > 23 or m > 59:
        return None
    delta = timedelta(hours=h, minutes=m)
    return timezone(-delta if sign == "-" else delta)


def _text(value: object) -> str | None:
    """EXIF ASCII values come back as ``str`` (sometimes ``bytes``); strip NULs."""
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    return value.strip("\x00").strip()


def _error(origin: EmbeddedOrigin, code: MetadataErrorCode, field: str | None = None) -> MetadataError:
    messages: dict[MetadataErrorCode, str] = {
        "METADATA_NOT_FOUND": "사진에 촬영 시각·위치 메타데이터가 없습니다.",
        "METADATA_PARSE_ERROR": "사진 내장 메타데이터를 해석할 수 없습니다.",
        "METADATA_MISSING_FIELD": f"사진 내장 메타데이터에 필수 필드 {field}이(가) 없습니다.",
        "METADATA_CAPTURED_AT_INVALID": "사진 내장 메타데이터의 촬영 시각이 올바르지 않습니다.",
        "METADATA_OUT_OF_RANGE": f"사진 내장 메타데이터의 {field} 값이 허용 범위를 벗어났습니다.",
    }
    return MetadataError(code, messages[code], origin, field)


# -- 1st priority: UserComment JSON ---------------------------------------------


def _decode_user_comment(raw: object) -> str | None:
    """Strip the 8-byte character code header. Returns None for an empty comment."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or not raw:
        return None
    if raw.lstrip()[:1] == b"{":  # no header at all
        body, encodings = raw, ("utf-8",)
    elif raw[:HEADER_LENGTH] == HEADER_UNICODE:
        body = raw[HEADER_LENGTH:]
        encodings = ("utf-16",) if body[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-16-be", "utf-16-le")
    else:  # ASCII header, all-NUL "undefined" header, or anything else: try UTF-8
        body, encodings = raw[HEADER_LENGTH:], ("utf-8",)
    for encoding in encodings:
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            continue
        text = text.strip("\x00").strip()
        if not text:
            return None
        if len(encodings) == 1 or text.startswith("{"):
            return text
    # Undecodable bytes: report as a parse error rather than "absent".
    return "\x00invalid"


def _captured_at_from_json(obj: dict[str, Any]) -> datetime | MetadataError:
    origin: EmbeddedOrigin = "user_comment"
    for name in ("date", "time"):
        if obj.get(name) is None:
            return _error(origin, "METADATA_MISSING_FIELD", name)
    raw_date, raw_time = obj["date"], obj["time"]
    if not isinstance(raw_date, str) or not isinstance(raw_time, str):
        return _error(origin, "METADATA_CAPTURED_AT_INVALID", "captured_at")
    try:
        day = date.fromisoformat(raw_date)
        moment = time.fromisoformat(raw_time)
    except ValueError:
        return _error(origin, "METADATA_CAPTURED_AT_INVALID", "captured_at")
    if moment.tzinfo is not None:  # the offset belongs in utc_offset/timezone
        return _error(origin, "METADATA_CAPTURED_AT_INVALID", "captured_at")
    naive = datetime.combine(day, moment)

    offset: tzinfo | None = None
    if obj.get("utc_offset") is not None:
        offset = _parse_offset(obj["utc_offset"])
    elif isinstance(obj.get("timezone"), str):
        try:
            zone = ZoneInfo(obj["timezone"])
        except (ZoneInfoNotFoundError, ValueError, OSError):
            zone = None
        if zone is not None:
            delta = naive.replace(tzinfo=zone).utcoffset()
            offset = timezone(delta) if delta is not None else None
    if offset is None:
        return _error(origin, "METADATA_CAPTURED_AT_INVALID", "captured_at")
    return naive.replace(tzinfo=offset)


def _parse_user_comment(raw: object) -> CaptureMetadata | MetadataError | None:
    origin: EmbeddedOrigin = "user_comment"
    text = _decode_user_comment(raw)
    if text is None:
        return None
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        return _error(origin, "METADATA_PARSE_ERROR")
    if not isinstance(obj, dict):
        return _error(origin, "METADATA_PARSE_ERROR")

    captured_at = _captured_at_from_json(obj)
    if isinstance(captured_at, MetadataError):
        return captured_at

    coords: dict[str, float] = {}
    for name, (low, high) in COORDINATE_RANGES.items():
        if obj.get(name) is None:
            return _error(origin, "METADATA_MISSING_FIELD", name)
        value = _coordinate(obj[name], low, high)
        if value is None:
            return _error(origin, "METADATA_OUT_OF_RANGE", name)
        coords[name] = value

    # Everything else (location, outfit, prompt, source, file, index ...) is dropped.
    return CaptureMetadata(
        captured_at=captured_at,
        latitude=coords["latitude"],
        longitude=coords["longitude"],
        synthetic=obj.get("synthetic") is True,
        origin="user_comment",
    )


# -- 2nd priority: standard EXIF ------------------------------------------------


def _dms_to_degrees(value: object) -> float | None:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    try:
        d, m, s = (float(part) for part in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not all(math.isfinite(x) for x in (d, m, s)) or min(d, m, s) < 0:
        return None
    return d + m / 60 + s / 3600


def _gps_coordinate(gps: dict[int, Any], tag: int, ref_tag: int, negative_ref: str) -> float | None:
    degrees = _dms_to_degrees(gps.get(tag))
    if degrees is None:
        return None
    ref = _text(gps.get(ref_tag)) if gps.get(ref_tag) is not None else ""
    if ref is None:
        return None
    ref = ref.upper()
    if ref == negative_ref:
        return -degrees
    if ref in ("", "N", "E"):
        return degrees
    return None


def _is_synthetic_processing_method(value: object) -> bool:
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    if not isinstance(value, bytes):
        return False
    body = value[HEADER_LENGTH:] if len(value) > HEADER_LENGTH else value
    return body.strip(b"\x00 ").upper() == b"SYNTHETIC"


def _parse_standard_exif(exif_ifd: dict[int, Any], gps: dict[int, Any]) -> CaptureMetadata | MetadataError | None:
    origin: EmbeddedOrigin = "exif_standard"
    has_time = exif_ifd.get(TAG_DATETIME_ORIGINAL) is not None
    has_gps = gps.get(GPS_LATITUDE) is not None or gps.get(GPS_LONGITUDE) is not None
    if not has_time and not has_gps:
        return None
    if not has_time:
        return _error(origin, "METADATA_MISSING_FIELD", "captured_at")

    # Req 2.14: use the time only when both DateTimeOriginal and its offset exist.
    raw_dt = _text(exif_ifd.get(TAG_DATETIME_ORIGINAL))
    offset = _parse_offset(_text(exif_ifd.get(TAG_OFFSET_TIME_ORIGINAL)))
    if raw_dt is None or offset is None:
        return _error(origin, "METADATA_CAPTURED_AT_INVALID", "captured_at")
    try:
        captured_at = datetime.strptime(raw_dt, _EXIF_DATETIME_FORMAT).replace(tzinfo=offset)
    except ValueError:
        return _error(origin, "METADATA_CAPTURED_AT_INVALID", "captured_at")

    coords: dict[str, float] = {}
    for name, tag, ref_tag, negative in (
        ("latitude", GPS_LATITUDE, GPS_LATITUDE_REF, "S"),
        ("longitude", GPS_LONGITUDE, GPS_LONGITUDE_REF, "W"),
    ):
        if gps.get(tag) is None:
            return _error(origin, "METADATA_MISSING_FIELD", name)
        value = _gps_coordinate(gps, tag, ref_tag, negative)
        low, high = COORDINATE_RANGES[name]
        if value is None or not (low <= value <= high):
            return _error(origin, "METADATA_OUT_OF_RANGE", name)
        coords[name] = value

    return CaptureMetadata(
        captured_at=captured_at,
        latitude=coords["latitude"],
        longitude=coords["longitude"],
        synthetic=_is_synthetic_processing_method(gps.get(GPS_PROCESSING_METHOD)),
        origin="exif_standard",
    )


# -- public API -----------------------------------------------------------------


def read_embedded_metadata(data: bytes) -> CaptureMetadata | MetadataError | None:
    """Read capture time/location from the image's EXIF. Pure; never raises.

    ``None`` means the image carries no capture metadata at all.
    """
    try:
        with Image.open(BytesIO(data)) as image:
            exif = image.getexif()
            exif_ifd = dict(exif.get_ifd(EXIF_IFD))
            gps = dict(exif.get_ifd(GPS_IFD))
    except Exception:  # undecodable image or corrupt EXIF; check_image reports the image
        return None

    user_comment = _parse_user_comment(exif_ifd.get(TAG_USER_COMMENT))
    if isinstance(user_comment, CaptureMetadata):
        return user_comment
    try:
        standard = _parse_standard_exif(exif_ifd, gps)
    except Exception:  # unexpected EXIF value types
        standard = _error("exif_standard", "METADATA_PARSE_ERROR")
    if isinstance(standard, CaptureMetadata):
        return standard
    # Neither source is valid: the first-priority source's error wins.
    return user_comment if user_comment is not None else standard


def metadata_source(capture: CaptureMetadata) -> MetadataSource:
    """``Outfit.source`` for a resolved capture (Req 2.7)."""
    return "demo_sidecar" if capture.origin == "sidecar" else "exif"


def _failure(error: MetadataError | SidecarError) -> ScanFailure:
    return ScanFailure(error.code, error.message, error.field)


def resolve_capture_metadata(
    embedded: CaptureMetadata | MetadataError | None,
    sidecar: SidecarMetadata | SidecarError | None,
) -> tuple[CaptureMetadata, ThermalVote | None] | ScanFailure:
    """Pick the capture time/location and seed vote (design.md table, Req 2.12, 2.16).

    The caller parses the sidecar with ``require_location`` set to whether
    ``embedded`` is a valid ``CaptureMetadata``.
    """
    if isinstance(embedded, CaptureMetadata):
        if sidecar is None:
            return embedded, None
        if isinstance(sidecar, SidecarError):
            return _failure(sidecar)
        if sidecar.captured_at is not None and (
            sidecar.captured_at != embedded.captured_at
            or sidecar.latitude != embedded.latitude
            or sidecar.longitude != embedded.longitude
        ):
            logger.info("사이드카의 촬영 시각·위치는 무시하고 사진 내장 메타데이터를 사용합니다.")
        return embedded, sidecar.thermal_vote

    # No valid embedded metadata: the sidecar is the fallback source.
    if isinstance(sidecar, SidecarMetadata):
        if sidecar.captured_at is not None and sidecar.latitude is not None and sidecar.longitude is not None:
            capture = CaptureMetadata(
                captured_at=sidecar.captured_at,
                latitude=sidecar.latitude,
                longitude=sidecar.longitude,
                synthetic=True,
                origin="sidecar",
            )
            return capture, sidecar.thermal_vote
        if isinstance(embedded, MetadataError):
            return _failure(embedded)
        missing = next(
            name for name in ("captured_at", "latitude", "longitude") if getattr(sidecar, name) is None
        )
        return ScanFailure(
            "SIDECAR_MISSING_FIELD", f"사이드카에 필수 필드 {missing}이(가) 없습니다.", missing
        )
    if isinstance(embedded, MetadataError):
        return _failure(embedded)
    if isinstance(sidecar, SidecarError):
        return _failure(sidecar)
    return ScanFailure("METADATA_NOT_FOUND", "사진에 촬영 시각·위치 메타데이터가 없습니다.")
