"""Capture_Simulator helpers: "I just took a photo" without a phone (design.md §10, Req 8).

Used by ``scripts/simulate_capture.py``. Everything is offline: no Gemini, no
Open-Meteo, no API server.

- ``encode_with_exif`` re-encodes an image with the capture time and place as
  Embedded_Metadata: the UserComment JSON (``synthetic: true``,
  ``source: "simulate_capture"``) that Phase 1 ``read_embedded_metadata``
  reads first, and for JPEG also the standard EXIF tags (DateTimeOriginal,
  OffsetTimeOriginal, GPS degrees/minutes/seconds, GPSProcessingMethod
  ``SYNTHETIC``) (Req 8.5, 8.9).
- ``overlay_entry`` / ``add_overlay_entry_atomic`` put an ``outfit`` decision
  for the new file's hash into the Vision_Fixture_Overlay *before* the file is
  published, so fixture mode stores it without a race (Req 8.6).
- ``publish_atomic`` writes a hidden ``.<name>.tmp`` in Gallery_Dir and links
  it to ``capture_<YYYYMMDD_HHMMSS>.<ext>`` without overwriting (``_2``,
  ``_3`` …, Req 8.7). The watcher ignores the hidden temp file.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw
from PIL.TiffImagePlugin import IFDRational

from app.domain import (
    ClothingTags,
    VisionAnalysis,
    normalize_lower_type,
    normalize_outer_type,
    normalize_upper_type,
)
from app.ingestion.metadata import (
    EXIF_IFD,
    GPS_IFD,
    GPS_LATITUDE,
    GPS_LATITUDE_REF,
    GPS_LONGITUDE,
    GPS_LONGITUDE_REF,
    GPS_PROCESSING_METHOD,
    HEADER_ASCII,
    TAG_DATETIME_ORIGINAL,
    TAG_OFFSET_TIME_ORIGINAL,
    TAG_USER_COMMENT,
)

ImageFormat = Literal["JPEG", "PNG"]

PLACEHOLDER_TAGS = ClothingTags(
    upper_type="knit", outer_type="none", lower_type="jeans",
    colors=["cream"], layers_visible=1, confidence=0.9,
)
OVERLAY_CONFIDENCE = 0.95
OVERLAY_REASON = "simulate_capture fixture"
JPEG_QUALITY = 92
PLACEHOLDER_SIZE = (768, 1024)
SOURCE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_GPS_SECONDS_DENOMINATOR = 10_000


def _offset_text(at: datetime) -> str:
    offset = at.utcoffset()
    if offset is None:
        raise ValueError("at must be timezone-aware")
    total = int(offset.total_seconds())
    if total % 60:
        raise ValueError("UTC offsets with seconds cannot be written to EXIF")
    sign = "-" if total < 0 else "+"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def user_comment_payload(at: datetime, lat: float, lon: float, tz_name: str) -> dict[str, Any]:
    """UserComment JSON (Req 8.5). ``utc_offset`` (``at``'s own offset) wins over ``timezone``."""
    local = at.replace(microsecond=0)
    return {
        "date": local.date().isoformat(),
        "time": local.time().replace(tzinfo=None).isoformat(),
        "timezone": tz_name,
        "utc_offset": _offset_text(at),
        "latitude": float(lat),
        "longitude": float(lon),
        "synthetic": True,
        "source": "simulate_capture",
    }


def _dms(degrees: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    value = abs(degrees)
    whole = int(value)
    minutes_full = (value - whole) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60 * _GPS_SECONDS_DENOMINATOR)
    if seconds >= 60 * _GPS_SECONDS_DENOMINATOR:  # rounding carried over
        seconds -= 60 * _GPS_SECONDS_DENOMINATOR
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        whole += 1
    return IFDRational(whole, 1), IFDRational(minutes, 1), IFDRational(seconds, _GPS_SECONDS_DENOMINATOR)


def build_exif(fmt: ImageFormat, at: datetime, lat: float, lon: float, tz_name: str) -> bytes:
    exif = Image.Exif()
    exif_ifd = exif.get_ifd(EXIF_IFD)
    payload = json.dumps(user_comment_payload(at, lat, lon, tz_name), ensure_ascii=True)
    exif_ifd[TAG_USER_COMMENT] = HEADER_ASCII + payload.encode("ascii")
    if fmt == "JPEG":
        exif_ifd[TAG_DATETIME_ORIGINAL] = at.strftime("%Y:%m:%d %H:%M:%S")
        exif_ifd[TAG_OFFSET_TIME_ORIGINAL] = _offset_text(at)
        gps = exif.get_ifd(GPS_IFD)
        gps[GPS_LATITUDE_REF] = "S" if lat < 0 else "N"
        gps[GPS_LATITUDE] = _dms(lat)
        gps[GPS_LONGITUDE_REF] = "W" if lon < 0 else "E"
        gps[GPS_LONGITUDE] = _dms(lon)
        gps[GPS_PROCESSING_METHOD] = HEADER_ASCII + b"SYNTHETIC"
    # An Exif holding only nested IFDs is falsy, so pass the encoded bytes.
    return exif.tobytes()


def encode_with_exif(
    image: Image.Image, fmt: ImageFormat, at: datetime, lat: float, lon: float, tz_name: str
) -> bytes:
    """Re-encode ``image`` with the simulator's Embedded_Metadata (Req 8.5, 8.9)."""
    if not (math.isfinite(lat) and -90 <= lat <= 90 and math.isfinite(lon) and -180 <= lon <= 180):
        raise ValueError("latitude/longitude out of range")
    exif = build_exif(fmt, at, lat, lon, tz_name)
    buffer = BytesIO()
    if fmt == "JPEG":
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY, exif=exif)
    else:
        target = image if image.mode in ("RGB", "RGBA", "L", "LA") else image.convert("RGBA")
        target.save(buffer, format="PNG", exif=exif)
    return buffer.getvalue()


def output_format(source_name: str) -> ImageFormat:
    """JPEG stays JPEG; PNG and WebP become PNG (design.md §10 step 4)."""
    return "JPEG" if source_name.lower().endswith((".jpg", ".jpeg")) else "PNG"


def capture_file_name(at: datetime, ext: str, existing: Iterable[str]) -> str:
    """``capture_YYYYMMDD_HHMMSS.<ext>``; ``_2``, ``_3`` … when the name is taken (Req 8.7).

    Names are compared case-insensitively (macOS volumes usually are).
    """
    taken = {name.lower() for name in existing}
    ext = ext if ext.startswith(".") else f".{ext}"
    base = f"capture_{at.strftime('%Y%m%d_%H%M%S')}"
    candidate, counter = f"{base}{ext}", 1
    while candidate.lower() in taken:
        counter += 1
        candidate = f"{base}_{counter}{ext}"
    return candidate


def is_demo_source(name: str, sha256: str, manifest_names: set[str], demo_hashes: set[str]) -> bool:
    """A demo photo (by manifest file name or a known demo hash) must not be reused (Req 8.3)."""
    return name in manifest_names or sha256.lower() in demo_hashes


def load_demo_identity(manifest_path: Path, vision_path: Path, sources_path: Path) -> tuple[set[str], set[str]]:
    """(manifest file names, demo hashes from vision.json keys and vision.sources.json)."""
    names: set[str] = set()
    hashes: set[str] = set()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = {str(p["file"]) for p in manifest.get("photos", []) if isinstance(p, dict) and "file" in p}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        vision = json.loads(vision_path.read_text(encoding="utf-8"))
        if isinstance(vision, dict):
            hashes |= {str(k).lower() for k in vision}
    except (OSError, ValueError):
        pass
    try:
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        if isinstance(sources, dict):
            for key, value in sources.items():
                hashes.add(str(key).lower())
                if isinstance(value, dict) and isinstance(value.get("source_sha256"), str):
                    hashes.add(value["source_sha256"].lower())
    except (OSError, ValueError):
        pass
    return names, hashes


def overlay_entry(tags: ClothingTags) -> VisionAnalysis:
    """The fixture decision written for a simulated capture (Req 8.6)."""
    return VisionAnalysis(
        photo_type="outfit",
        photo_type_confidence=OVERLAY_CONFIDENCE,
        reason=OVERLAY_REASON,
        tags=tags,
    )


def add_overlay_entry_atomic(path: Path, sha256: str, entry: VisionAnalysis) -> None:
    """Add ``{sha256: entry}`` to the overlay through a temp file + ``os.replace``.

    Existing entries are kept; an unreadable overlay is replaced by one that
    holds only the new entry (the provider ignored it anyway).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current[sha256.lower()] = entry.model_dump(mode="json")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def publish_atomic(gallery_dir: Path, data: bytes, at: datetime, ext: str) -> str:
    """Write ``data`` into Gallery_Dir under a free capture name and return that name.

    The bytes go to a hidden ``.<name>.tmp`` first; the final name is created
    with a hard link (fails instead of overwriting) and the temp file removed.
    """
    gallery_dir.mkdir(parents=True, exist_ok=True)
    existing = {entry.name for entry in os.scandir(gallery_dir)}
    name = capture_file_name(at, ext, existing)
    temp = gallery_dir / f".{name}.tmp"
    temp.write_bytes(data)
    try:
        for _ in range(1000):
            target = gallery_dir / name
            try:
                os.link(temp, target)
                return name
            except FileExistsError:
                existing.add(name)
                name = capture_file_name(at, ext, existing)
            except OSError:  # no hard links on this file system
                if not target.exists():
                    os.replace(temp, target)
                    return name
                existing.add(name)
                name = capture_file_name(at, ext, existing)
        raise OSError("no free capture file name")
    finally:
        if temp.exists():
            temp.unlink()


def tags_from_clothing_json(entry: Mapping[str, Any]) -> ClothingTags | None:
    """``ex_img/clothing_tags.json`` style tags (free-text garment names) → canonical tags."""
    raw = entry.get("tags", entry) if isinstance(entry, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    try:
        colors = [str(c) for c in raw.get("colors", []) if str(c).strip()][:5]
        return ClothingTags(
            upper_type=normalize_upper_type(str(raw.get("upper_type", "unknown"))),
            outer_type=normalize_outer_type(str(raw.get("outer_type", "unknown"))),
            lower_type=normalize_lower_type(str(raw.get("lower_type", "unknown"))),
            colors=colors,
            layers_visible=int(raw.get("layers_visible", 1)),
            confidence=float(raw.get("confidence", 0.9)),
        )
    except (TypeError, ValueError):
        return None


def pool_tags(pool_dir: Path, file_name: str) -> ClothingTags | None:
    """Tags for ``file_name`` from Capture_Pool_Dir's ``clothing_tags.json``, if any."""
    try:
        payload = json.loads((pool_dir / "clothing_tags.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    images = payload.get("images", payload)
    if not isinstance(images, dict) or file_name not in images:
        return None
    entry = images[file_name]
    return tags_from_clothing_json(entry) if isinstance(entry, Mapping) else None


def next_pool_source(pool_dir: Path) -> Path | None:
    """The pool image after the one named in ``.last_used`` (name order, wraps around)."""
    try:
        images = sorted(
            p for p in pool_dir.iterdir()
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
            and p.suffix.lower() in SOURCE_EXTENSIONS
        )
    except OSError:
        return None
    if not images:
        return None
    try:
        last = (pool_dir / ".last_used").read_text(encoding="utf-8").strip()
    except OSError:
        last = ""
    names = [p.name for p in images]
    for index, name in enumerate(names):
        if name > last:
            return images[index]
    return images[0]


def remember_pool_source(pool_dir: Path, name: str) -> None:
    (pool_dir / ".last_used").write_text(name, encoding="utf-8")


def draw_placeholder() -> Image.Image:
    """768×1024 cream knit + jeans silhouette, no text."""
    width, height = PLACEHOLDER_SIZE
    image = Image.new("RGB", PLACEHOLDER_SIZE, (226, 232, 238))
    draw = ImageDraw.Draw(image)
    cx = width // 2
    skin = (233, 201, 176)
    cream = (240, 228, 204)
    denim = (64, 96, 140)
    draw.ellipse((cx - 70, 90, cx + 70, 240), fill=skin)  # head
    draw.rectangle((cx - 28, 230, cx + 28, 270), fill=skin)  # neck
    draw.rounded_rectangle((cx - 170, 260, cx + 170, 600), radius=60, fill=cream)  # knit body
    draw.rounded_rectangle((cx - 250, 280, cx - 150, 590), radius=45, fill=cream)  # sleeves
    draw.rounded_rectangle((cx + 150, 280, cx + 250, 590), radius=45, fill=cream)
    for y in range(300, 590, 28):  # knit ribbing
        draw.line((cx - 150, y, cx + 150, y), fill=(225, 210, 182), width=4)
    draw.rectangle((cx - 160, 590, cx + 160, 650), fill=denim)  # waist
    draw.rounded_rectangle((cx - 160, 620, cx - 10, 960), radius=30, fill=denim)  # legs
    draw.rounded_rectangle((cx + 10, 620, cx + 160, 960), radius=30, fill=denim)
    draw.rounded_rectangle((cx - 175, 950, cx - 5, 990), radius=18, fill=(60, 60, 60))  # shoes
    draw.rounded_rectangle((cx + 5, 950, cx + 175, 990), radius=18, fill=(60, 60, 60))
    return image
