"""Estimated capture info for watched photos (tasks 3.5, Req 5.1–5.4)."""

from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.domain import SidecarMetadata
from app.gallery.estimate import (
    EMPTY_EXIF,
    EstimatedCapture,
    PartialExif,
    estimate_capture,
    estimate_for_image,
    estimated_sidecar_json,
    read_partial_exif,
)
from app.ingestion.sidecar import parse_sidecar_text
from app.location import LatLon

TZ = ZoneInfo("Asia/Seoul")
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # 12:00 KST
DEFAULT = LatLon(37.5665, 126.978)
LAST = LatLon(35.18, 129.08)


def _dms(degrees: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    d = int(degrees)
    minutes = (degrees - d) * 60
    m = int(minutes)
    return (IFDRational(d, 1), IFDRational(m, 1), IFDRational(round((minutes - m) * 60 * 10000), 10000))


def jpeg_with_exif(*, when: str | None = None, offset: str | None = None,
                   gps: tuple[float, float] | None = None, comment: bytes | None = None) -> bytes:
    exif = Image.Exif()
    exif_ifd = exif.get_ifd(0x8769)
    if when is not None:
        exif_ifd[0x9003] = when
    if offset is not None:
        exif_ifd[0x9011] = offset
    if comment is not None:
        exif_ifd[0x9286] = comment
    if gps is not None:
        gps_ifd = exif.get_ifd(0x8825)
        lat, lon = gps
        gps_ifd[1] = "S" if lat < 0 else "N"
        gps_ifd[2] = _dms(abs(lat))
        gps_ifd[3] = "W" if lon < 0 else "E"
        gps_ifd[4] = _dms(abs(lon))
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def plain_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


def mtime_of(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime_ns / 1e9, UTC)


def test_no_exif_uses_mtime_and_default_location(tmp_path: Path) -> None:
    path = tmp_path / "x.png"
    path.write_bytes(plain_png())
    stamp = datetime(2026, 11, 13, 22, 15, 30, tzinfo=KST).timestamp()
    os.utime(path, (stamp, stamp))

    estimate = estimate_for_image(path.read_bytes(), mtime_of(path), NOW, None, DEFAULT, TZ)
    assert estimate == EstimatedCapture(
        captured_at=datetime(2026, 11, 13, 22, 15, 30, tzinfo=KST),
        latitude=DEFAULT.latitude, longitude=DEFAULT.longitude,
        time_source="file_mtime", location_source="default",
    )
    assert estimate.captured_at.utcoffset() == timedelta(hours=9)


def test_future_mtime_clamped_to_now() -> None:
    estimate = estimate_capture(EMPTY_EXIF, NOW + timedelta(days=3), NOW, None, DEFAULT, TZ)
    assert estimate.captured_at == NOW
    assert estimate.captured_at.utcoffset() == timedelta(hours=9)
    assert estimate.time_source == "file_mtime"


def test_no_exif_uses_last_location() -> None:
    estimate = estimate_capture(EMPTY_EXIF, NOW - timedelta(hours=1), NOW, LAST, DEFAULT, TZ)
    assert (estimate.latitude, estimate.longitude, estimate.location_source) == (35.18, 129.08, "browser")


def test_datetime_without_offset_uses_app_tz() -> None:
    data = jpeg_with_exif(when="2026:11:10 08:30:00")
    partial = read_partial_exif(data)
    assert partial.date_time_original == datetime(2026, 11, 10, 8, 30)
    assert partial.offset is None
    estimate = estimate_for_image(data, NOW, NOW, None, DEFAULT, TZ)
    assert estimate.captured_at == datetime(2026, 11, 10, 8, 30, tzinfo=KST)
    assert estimate.captured_at.utcoffset() == timedelta(hours=9)
    assert estimate.time_source == "exif_datetime"
    assert estimate.location_source == "default"


def test_datetime_with_offset_kept_when_gps_out_of_range() -> None:
    # Longitude 200° makes the standard EXIF invalid as a whole (Phase 1
    # METADATA_OUT_OF_RANGE, and the UserComment is broken JSON); the usable
    # time and offset still drive the estimate, the location falls back.
    data = jpeg_with_exif(when="2026:11:10 08:30:00", offset="-05:00", gps=(35.18, 200.0),
                          comment=b"ASCII\x00\x00\x00{broken")
    partial = read_partial_exif(data)
    assert partial.latitude is not None and partial.longitude is None
    estimate = estimate_for_image(data, NOW, NOW, LAST, DEFAULT, TZ)
    assert estimate.captured_at == datetime(2026, 11, 10, 8, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert estimate.time_source == "exif_datetime"
    assert (estimate.latitude, estimate.longitude, estimate.location_source) == (35.18, 129.08, "browser")


def test_full_valid_standard_exif_with_broken_comment_is_not_estimated() -> None:
    # Phase 1 falls back to standard EXIF when only the UserComment is broken.
    data = jpeg_with_exif(when="2026:11:10 08:30:00", offset="-05:00", gps=(35.18, 129.08),
                          comment=b"ASCII\x00\x00\x00{broken")
    assert estimate_for_image(data, NOW, NOW, LAST, DEFAULT, TZ) is None


def test_gps_without_time_uses_mtime_and_gps() -> None:
    data = jpeg_with_exif(gps=(37.5, -122.25))
    estimate = estimate_for_image(data, NOW - timedelta(minutes=5), NOW, LAST, DEFAULT, TZ)
    assert estimate.time_source == "file_mtime" and estimate.location_source == "exif_gps"
    assert abs(estimate.longitude + 122.25) < 1e-5


def test_partial_gps_is_not_used() -> None:
    estimate = estimate_capture(PartialExif(None, None, 37.5, None), NOW, NOW, None, DEFAULT, TZ)
    assert estimate.location_source == "default"


def test_valid_embedded_metadata_not_estimated() -> None:
    data = jpeg_with_exif(when="2026:11:10 08:30:00", offset="+09:00", gps=(37.55, 126.92))
    assert estimate_for_image(data, NOW, NOW, LAST, DEFAULT, TZ) is None


def test_garbage_bytes_give_empty_exif() -> None:
    assert read_partial_exif(b"not an image") == EMPTY_EXIF


def test_sidecar_json_round_trips_through_phase1_parser() -> None:
    estimate = EstimatedCapture(
        captured_at=datetime(2026, 11, 13, 22, 15, 30, tzinfo=KST),
        latitude=37.57, longitude=126.98, time_source="file_mtime", location_source="browser",
    )
    text = estimated_sidecar_json(estimate)
    payload = json.loads(text)
    assert payload["estimated"] is True
    assert payload["time_source"] == "file_mtime" and payload["location_source"] == "browser"
    parsed = parse_sidecar_text(text, require_location=True)
    assert isinstance(parsed, SidecarMetadata)
    assert parsed.captured_at == estimate.captured_at
    assert parsed.captured_at.utcoffset() == timedelta(hours=9)
    assert (parsed.latitude, parsed.longitude) == (37.57, 126.98)
