"""Unit tests for the embedded metadata reader, area (g) (Req 2.1~2.17).

Test images are 8×8 PNG/JPEG files built with Pillow in ``tmp_path``, carrying
either a UserComment JSON (8-byte ``ASCII\\0\\0\\0`` header, same keys as the
``ex_img/`` photos) or standard EXIF (DateTimeOriginal + OffsetTimeOriginal +
GPS). One optional test reads a real ``ex_img/`` photo and skips if absent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.domain import CaptureMetadata
from app.ingestion.metadata import (
    MetadataError,
    ScanFailure,
    metadata_source,
    read_embedded_metadata,
    resolve_capture_metadata,
)
from app.ingestion.sidecar import SidecarError, parse_sidecar_text

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
USER_COMMENT = 0x9286
DATETIME_ORIGINAL = 0x9003
OFFSET_TIME_ORIGINAL = 0x9011
ASCII_HEADER = b"ASCII\x00\x00\x00"

REPO_ROOT = Path(__file__).resolve().parents[2]

# Same key set as the ex_img/ UserComment JSON (design.md research summary).
EX_IMG_COMMENT = {
    "date": "2025-01-08",
    "time": "08:15:00",
    "timezone": "Asia/Seoul",
    "utc_offset": "+09:00",
    "latitude": 37.556,
    "longitude": 126.924,
    "location": "서울 마포구 연남동 골목",
    "outfit": "네이비 울 코트, 회색 니트, 검정 슬랙스",
    "lighting": "흐린 아침 자연광",
    "synthetic": True,
    "source": "generated-for-demo",
    "file": "2025-01-08_081500_KST.jpg",
    "index": 1,
    "prompt": "a person wearing a navy wool coat on a Seoul street",
    "original_prompt": "winter commute outfit",
    "original_source": "ex_img",
}
TEXT_KEYS = ("location", "outfit", "lighting", "source", "file", "prompt", "original_prompt", "original_source")
KST = timezone(timedelta(hours=9))


# -- image builders -------------------------------------------------------------


def _rational(value: float) -> IFDRational:
    return IFDRational(round(value * 10000), 10000)


def _dms(degrees: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    d = int(degrees)
    minutes_full = (degrees - d) * 60
    m = int(minutes_full)
    s = (minutes_full - m) * 60
    return (IFDRational(d, 1), IFDRational(m, 1), _rational(s))


def make_image(
    tmp_path: Path,
    *,
    fmt: str = "PNG",
    comment: dict | bytes | None = None,
    standard: dict | None = None,
    name: str | None = None,
) -> bytes:
    """Write an 8×8 image with the given EXIF into ``tmp_path`` and return its bytes."""
    exif = Image.Exif()
    if comment is not None:
        raw = comment if isinstance(comment, bytes) else ASCII_HEADER + json.dumps(comment).encode("utf-8")
        exif.get_ifd(EXIF_IFD)[USER_COMMENT] = raw
    if standard is not None:
        exif_ifd = exif.get_ifd(EXIF_IFD)
        exif_ifd[DATETIME_ORIGINAL] = standard["datetime"]
        exif_ifd[OFFSET_TIME_ORIGINAL] = standard["offset"]
        gps = exif.get_ifd(GPS_IFD)
        lat, lon = standard["latitude"], standard["longitude"]
        gps[1] = "S" if lat < 0 else "N"
        gps[2] = _dms(abs(lat))
        gps[3] = "W" if lon < 0 else "E"
        gps[4] = _dms(abs(lon))
        if standard.get("synthetic"):
            gps[0x1B] = ASCII_HEADER + b"SYNTHETIC"
    suffix = ".jpg" if fmt == "JPEG" else ".png"
    path = tmp_path / (name or f"img{len(list(tmp_path.iterdir()))}{suffix}")
    Image.new("RGB", (8, 8), (120, 160, 200)).save(path, fmt, exif=exif.tobytes())
    return path.read_bytes()


STANDARD_SEOUL = {
    "datetime": "2025:01:08 08:15:00",
    "offset": "+09:00",
    "latitude": 37.556,
    "longitude": 126.924,
    "synthetic": True,
}


def sidecar(obj: dict, *, require_location: bool):
    return parse_sidecar_text(json.dumps(obj), require_location=require_location)


# -- (g) tests ------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_user_comment_json_parsed(tmp_path: Path, fmt: str) -> None:
    result = read_embedded_metadata(make_image(tmp_path, fmt=fmt, comment=EX_IMG_COMMENT))

    assert isinstance(result, CaptureMetadata), result
    assert result.origin == "user_comment"
    assert result.captured_at == datetime(2025, 1, 8, 8, 15, tzinfo=KST)
    assert result.captured_at.utcoffset() == timedelta(hours=9)
    assert result.captured_at.isoformat() == "2025-01-08T08:15:00+09:00"
    assert result.latitude == pytest.approx(37.556)
    assert result.longitude == pytest.approx(126.924)
    assert result.synthetic is True


def test_user_comment_json_parsed_from_real_ex_img_photo() -> None:
    photos = sorted((REPO_ROOT / "ex_img").glob("*.png")) if (REPO_ROOT / "ex_img").is_dir() else []
    if not photos:
        pytest.skip("ex_img/ photos not present")
    result = read_embedded_metadata(photos[0].read_bytes())

    assert isinstance(result, CaptureMetadata), result
    assert result.origin == "user_comment"
    assert result.captured_at.utcoffset() is not None
    assert -90 <= result.latitude <= 90 and -180 <= result.longitude <= 180
    assert result.synthetic is True


def test_standard_exif_jpeg_parsed(tmp_path: Path) -> None:
    result = read_embedded_metadata(make_image(tmp_path, fmt="JPEG", standard=STANDARD_SEOUL))

    assert isinstance(result, CaptureMetadata), result
    assert result.origin == "exif_standard"
    assert result.captured_at == datetime(2025, 1, 8, 8, 15, tzinfo=KST)
    assert result.captured_at.utcoffset() == timedelta(hours=9)
    assert result.latitude == pytest.approx(37.556, abs=1e-5)
    assert result.longitude == pytest.approx(126.924, abs=1e-5)
    assert result.synthetic is True


def test_standard_exif_south_west_and_not_synthetic(tmp_path: Path) -> None:
    standard = {"datetime": "2024:07:01 23:05:09", "offset": "-03:00",
                "latitude": -23.5505, "longitude": -46.6333}
    result = read_embedded_metadata(make_image(tmp_path, fmt="JPEG", standard=standard))

    assert isinstance(result, CaptureMetadata), result
    assert result.captured_at.isoformat() == "2024-07-01T23:05:09-03:00"
    assert result.latitude == pytest.approx(-23.5505, abs=1e-5)
    assert result.longitude == pytest.approx(-46.6333, abs=1e-5)
    assert result.synthetic is False


def test_standard_exif_without_offset_is_invalid(tmp_path: Path) -> None:
    # Req 2.14: DateTimeOriginal is used only together with OffsetTimeOriginal.
    exif = Image.Exif()
    exif.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = "2025:01:08 08:15:00"
    path = tmp_path / "no_offset.jpg"
    Image.new("RGB", (8, 8)).save(path, "JPEG", exif=exif.tobytes())

    result = read_embedded_metadata(path.read_bytes())

    assert isinstance(result, MetadataError)
    assert result.code == "METADATA_CAPTURED_AT_INVALID"


def test_user_comment_preferred_over_standard_exif(tmp_path: Path) -> None:
    comment = {**EX_IMG_COMMENT, "date": "2025-03-02", "time": "14:00:00",
               "latitude": 37.5, "longitude": 127.0}
    data = make_image(tmp_path, fmt="JPEG", comment=comment, standard=STANDARD_SEOUL)

    result = read_embedded_metadata(data)

    assert isinstance(result, CaptureMetadata), result
    assert result.origin == "user_comment"
    assert result.captured_at == datetime(2025, 3, 2, 14, 0, tzinfo=KST)
    assert (result.latitude, result.longitude) == (37.5, 127.0)


def test_invalid_user_comment_falls_back_to_valid_standard_exif(tmp_path: Path) -> None:
    data = make_image(tmp_path, fmt="JPEG", comment=ASCII_HEADER + b"{not json", standard=STANDARD_SEOUL)

    result = read_embedded_metadata(data)

    assert isinstance(result, CaptureMetadata), result
    assert result.origin == "exif_standard"


def test_sidecar_fallback_when_no_exif(tmp_path: Path) -> None:
    data = make_image(tmp_path)
    embedded = read_embedded_metadata(data)
    assert embedded is None
    side = sidecar(
        {"captured_at": "2025-11-20T08:30:00+09:00", "latitude": 37.5665,
         "longitude": 126.978, "thermal_vote": "hot"},
        require_location=True,
    )

    result = resolve_capture_metadata(embedded, side)

    assert not isinstance(result, ScanFailure), result
    capture, vote = result
    assert capture.origin == "sidecar"
    assert metadata_source(capture) == "demo_sidecar"
    assert capture.synthetic is True
    assert capture.captured_at.isoformat() == "2025-11-20T08:30:00+09:00"
    assert (capture.latitude, capture.longitude) == (37.5665, 126.978)
    assert vote == "hot"


def test_sidecar_fallback_requires_location_fields(tmp_path: Path) -> None:
    embedded = read_embedded_metadata(make_image(tmp_path))
    side = sidecar({"thermal_vote": "cold"}, require_location=True)

    assert isinstance(side, SidecarError)
    assert side.code == "SIDECAR_MISSING_FIELD"
    result = resolve_capture_metadata(embedded, side)
    assert isinstance(result, ScanFailure)
    assert result.code == "SIDECAR_MISSING_FIELD"


def test_sidecar_vote_only_with_exif(tmp_path: Path) -> None:
    embedded = read_embedded_metadata(make_image(tmp_path, comment=EX_IMG_COMMENT))
    assert isinstance(embedded, CaptureMetadata)
    side = sidecar({"thermal_vote": "comfortable", "note": "seed"}, require_location=False)
    assert not isinstance(side, SidecarError), side

    result = resolve_capture_metadata(embedded, side)

    assert not isinstance(result, ScanFailure), result
    capture, vote = result
    assert capture == embedded
    assert metadata_source(capture) == "exif"
    assert vote == "comfortable"


def test_sidecar_location_ignored_when_exif_valid(tmp_path: Path) -> None:
    embedded = read_embedded_metadata(make_image(tmp_path, comment=EX_IMG_COMMENT))
    side = sidecar(
        {"captured_at": "2020-01-01T00:00:00+00:00", "latitude": 0.0, "longitude": 0.0},
        require_location=False,
    )

    capture, vote = resolve_capture_metadata(embedded, side)  # type: ignore[misc]

    assert capture == embedded
    assert vote is None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("{broken", "SIDECAR_PARSE_ERROR"),
        ("[1, 2]", "SIDECAR_PARSE_ERROR"),
        (json.dumps({"thermal_vote": "COLD"}), "SIDECAR_THERMAL_VOTE_INVALID"),
    ],
)
def test_broken_sidecar_fails_even_with_exif(tmp_path: Path, text: str, code: str) -> None:
    embedded = read_embedded_metadata(make_image(tmp_path, comment=EX_IMG_COMMENT))
    assert isinstance(embedded, CaptureMetadata)
    side = parse_sidecar_text(text, require_location=False)

    result = resolve_capture_metadata(embedded, side)

    assert isinstance(result, ScanFailure)
    assert result.code == code


def test_no_metadata_fails_metadata_not_found(tmp_path: Path) -> None:
    for fmt in ("PNG", "JPEG"):
        embedded = read_embedded_metadata(make_image(tmp_path, fmt=fmt))
        assert embedded is None

        result = resolve_capture_metadata(embedded, None)

        assert isinstance(result, ScanFailure)
        assert result.code == "METADATA_NOT_FOUND"


def test_invalid_embedded_without_sidecar_reports_metadata_error(tmp_path: Path) -> None:
    comment = {k: v for k, v in EX_IMG_COMMENT.items() if k != "latitude"}
    embedded = read_embedded_metadata(make_image(tmp_path, comment=comment))
    assert isinstance(embedded, MetadataError)
    assert (embedded.code, embedded.field) == ("METADATA_MISSING_FIELD", "latitude")

    result = resolve_capture_metadata(embedded, None)

    assert isinstance(result, ScanFailure)
    assert (result.code, result.field) == ("METADATA_MISSING_FIELD", "latitude")


@pytest.mark.parametrize(
    ("zone", "date", "expected_offset"),
    [
        ("Asia/Seoul", "2025-01-08", timedelta(hours=9)),
        ("America/New_York", "2025-01-08", timedelta(hours=-5)),  # EST
        ("America/New_York", "2025-07-08", timedelta(hours=-4)),  # EDT
    ],
)
def test_timezone_used_when_utc_offset_missing(
    tmp_path: Path, zone: str, date: str, expected_offset: timedelta
) -> None:
    comment = {k: v for k, v in EX_IMG_COMMENT.items() if k != "utc_offset"}
    comment.update(timezone=zone, date=date)

    result = read_embedded_metadata(make_image(tmp_path, comment=comment))

    assert isinstance(result, CaptureMetadata), result
    assert result.captured_at.utcoffset() == expected_offset
    assert result.captured_at.replace(tzinfo=None) == datetime.fromisoformat(f"{date}T08:15:00")


def test_missing_offset_and_timezone_is_invalid(tmp_path: Path) -> None:
    comment = {k: v for k, v in EX_IMG_COMMENT.items() if k not in ("utc_offset", "timezone")}

    result = read_embedded_metadata(make_image(tmp_path, comment=comment))

    assert isinstance(result, MetadataError)
    assert result.code == "METADATA_CAPTURED_AT_INVALID"


def test_text_fields_not_stored(tmp_path: Path) -> None:
    # Req 2.11, 2.17: only time, location and the synthetic flag are kept.
    minimal = {k: EX_IMG_COMMENT[k] for k in ("date", "time", "utc_offset", "latitude", "longitude", "synthetic")}
    full = read_embedded_metadata(make_image(tmp_path, comment=EX_IMG_COMMENT))
    bare = read_embedded_metadata(make_image(tmp_path, comment=minimal))

    assert isinstance(full, CaptureMetadata) and isinstance(bare, CaptureMetadata)
    assert full == bare
    assert set(full.model_dump()) == {"captured_at", "latitude", "longitude", "synthetic", "origin"}
    dumped = full.model_dump_json()
    for key in TEXT_KEYS:
        assert str(EX_IMG_COMMENT[key]) not in dumped

    capture, _ = resolve_capture_metadata(full, None)  # type: ignore[misc]
    assert capture == full
