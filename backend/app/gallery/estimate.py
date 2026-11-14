"""Estimated capture time and location for watched photos (design.md §5, Req 5.1–5.3, 5.6, 5.7).

Pure functions. A watched photo with valid Embedded_Metadata keeps the Phase 1
reader's result (Req 5.1); ``estimate_for_image`` then returns ``None``.
Otherwise the photo is not failed (decision A3) but gets an Estimated_Capture:

- time: EXIF ``DateTimeOriginal`` when it parses (with ``OffsetTimeOriginal``,
  else APP_TIMEZONE's offset for that local time, ``fold=0``), otherwise the
  file's mtime in APP_TIMEZONE; an mtime later than the processing time is
  clamped to the processing time.
- location: EXIF GPS when both coordinates are valid, otherwise Last_Location,
  otherwise DEFAULT_LATITUDE/DEFAULT_LONGITUDE.

The estimate is written next to the copy in User_Photo_Dir as a sidecar, so
the Phase 1 sidecar fallback reads it (``source=demo_sidecar``,
``metadata_synthetic=true``). The extra keys are ignored by
``parse_sidecar_text`` (Req 5.3, 5.7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Literal
from zoneinfo import ZoneInfo

from PIL import Image

from app.domain import CaptureMetadata
from app.ingestion.metadata import (
    COORDINATE_RANGES,
    EXIF_IFD,
    GPS_IFD,
    GPS_LATITUDE,
    GPS_LATITUDE_REF,
    GPS_LONGITUDE,
    GPS_LONGITUDE_REF,
    TAG_DATETIME_ORIGINAL,
    TAG_OFFSET_TIME_ORIGINAL,
    _gps_coordinate,
    _parse_offset,
    _text,
    read_embedded_metadata,
)
from app.location import LatLon

TimeSource = Literal["exif_datetime", "file_mtime"]
LocationSource = Literal["exif_gps", "browser", "default"]

_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"


@dataclass(frozen=True)
class PartialExif:
    """Whatever usable pieces the EXIF holds, even when the whole is invalid."""

    date_time_original: datetime | None  # naive, when DateTimeOriginal parses
    offset: timezone | None  # OffsetTimeOriginal
    latitude: float | None  # decimal degrees, only when in range
    longitude: float | None


EMPTY_EXIF = PartialExif(None, None, None, None)


@dataclass(frozen=True)
class EstimatedCapture:
    captured_at: datetime  # always aware
    latitude: float
    longitude: float
    time_source: TimeSource
    location_source: LocationSource


def _in_range(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    low, high = COORDINATE_RANGES[name]
    return value if low <= value <= high else None


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, _EXIF_DATETIME_FORMAT)
    except ValueError:
        return None


def read_partial_exif(data: bytes) -> PartialExif:
    """Read DateTimeOriginal, OffsetTimeOriginal and GPS pieces. Never raises.

    Uses the Phase 1 DMS/offset helpers (read only). Each piece is independent:
    a bad offset does not discard a good time, a bad longitude does not
    discard the latitude (the caller only uses GPS when both are present).
    """
    try:
        with Image.open(BytesIO(data)) as image:
            exif = image.getexif()
            exif_ifd = dict(exif.get_ifd(EXIF_IFD))
            gps = dict(exif.get_ifd(GPS_IFD))
    except Exception:  # undecodable image or corrupt EXIF
        return EMPTY_EXIF
    try:
        when = _parse_datetime(exif_ifd.get(TAG_DATETIME_ORIGINAL))
    except Exception:
        when = None
    try:
        offset = _parse_offset(_text(exif_ifd.get(TAG_OFFSET_TIME_ORIGINAL)))
    except Exception:
        offset = None
    try:
        latitude = _in_range(_gps_coordinate(gps, GPS_LATITUDE, GPS_LATITUDE_REF, "S"), "latitude")
        longitude = _in_range(_gps_coordinate(gps, GPS_LONGITUDE, GPS_LONGITUDE_REF, "W"), "longitude")
    except Exception:
        latitude = longitude = None
    return PartialExif(date_time_original=when, offset=offset, latitude=latitude, longitude=longitude)


def estimate_capture(
    partial: PartialExif,
    file_mtime: datetime,
    now: datetime,
    last_location: LatLon | None,
    default_location: LatLon,
    tz: ZoneInfo,
) -> EstimatedCapture:
    """Apply the Req 5.2 priority. ``file_mtime`` and ``now`` must be aware."""
    if partial.date_time_original is not None:
        local = partial.date_time_original.replace(microsecond=0, tzinfo=None)
        if partial.offset is not None:
            captured_at = local.replace(tzinfo=partial.offset)
        else:
            # APP_TIMEZONE's offset for that local time, pinned as a fixed offset.
            zoned = local.replace(tzinfo=tz, fold=0)
            captured_at = local.replace(tzinfo=timezone(zoned.utcoffset()))  # type: ignore[arg-type]
        time_source: TimeSource = "exif_datetime"
    else:
        moment = min(file_mtime, now)  # a future mtime becomes the processing time
        captured_at = moment.astimezone(tz).replace(microsecond=0)
        time_source = "file_mtime"

    if partial.latitude is not None and partial.longitude is not None:
        latitude, longitude = partial.latitude, partial.longitude
        location_source: LocationSource = "exif_gps"
    elif last_location is not None:
        latitude, longitude = last_location.latitude, last_location.longitude
        location_source = "browser"
    else:
        latitude, longitude = default_location.latitude, default_location.longitude
        location_source = "default"
    return EstimatedCapture(
        captured_at=captured_at,
        latitude=float(latitude),
        longitude=float(longitude),
        time_source=time_source,
        location_source=location_source,
    )


def estimate_for_image(
    data: bytes,
    file_mtime: datetime,
    now: datetime,
    last_location: LatLon | None,
    default_location: LatLon,
    tz: ZoneInfo,
) -> EstimatedCapture | None:
    """``None`` when the image has valid Embedded_Metadata (Req 5.1), else the estimate."""
    if isinstance(read_embedded_metadata(data), CaptureMetadata):
        return None
    return estimate_capture(read_partial_exif(data), file_mtime, now, last_location, default_location, tz)


def estimated_sidecar_json(estimate: EstimatedCapture) -> str:
    """Sidecar text for ``<stem>.metadata.json`` next to the User_Photo_Dir copy (Req 5.3).

    ``estimated``, ``time_source`` and ``location_source`` are extra keys that
    Phase 1 ``parse_sidecar_text`` ignores (Req 5.7).
    """
    return json.dumps(
        {
            "captured_at": estimate.captured_at.isoformat(),
            "latitude": estimate.latitude,
            "longitude": estimate.longitude,
            "estimated": True,
            "time_source": estimate.time_source,
            "location_source": estimate.location_source,
        },
        ensure_ascii=False,
    )
