"""Image file discovery and validation (design.md §3, Req 1.1, 1.8, 1.12).

- ``list_image_files`` picks the scan targets in PHOTO_DIR.
- ``is_too_large`` checks the size from ``stat()`` so an oversized file is
  never read into memory (Req 1.12).
- ``check_image`` is pure: it decodes the bytes with Pillow and confirms the
  detected format matches the file extension (Req 1.8).
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from app.ingestion.sidecar import SIDECAR_SUFFIX

# Lower-case extension → Pillow format name.
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}

# Pillow format name → MIME type sent to the Vision provider.
FORMAT_MIME_TYPES: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

MAX_IMAGE_BYTES = 20 * 1024 * 1024

ImageCheckErrorCode = Literal["FILE_TOO_LARGE", "INVALID_IMAGE_FORMAT"]


@dataclass(frozen=True)
class ImageCheckError:
    """Why an image was rejected (stage ``validation``)."""

    code: ImageCheckErrorCode
    message: str  # Korean, never contains a server path


FILE_TOO_LARGE = ImageCheckError(
    "FILE_TOO_LARGE", "이미지 파일 크기가 20MB를 초과합니다."
)
INVALID_IMAGE_FORMAT = ImageCheckError(
    "INVALID_IMAGE_FORMAT", "이미지 파일을 해당 형식으로 디코딩할 수 없습니다."
)


def expected_format(path_or_ext: Path | str) -> str | None:
    """Pillow format for a path or extension (case-insensitive), or None if unsupported."""
    ext = path_or_ext.suffix if isinstance(path_or_ext, Path) else path_or_ext
    return SUPPORTED_EXTENSIONS.get(ext.lower())


def is_image_candidate(path: Path) -> bool:
    """Supported extension, not a sidecar file, regular file (symlinks excluded)."""
    if path.name.lower().endswith(SIDECAR_SUFFIX):
        return False
    if expected_format(path) is None:
        return False
    # Symlinks are skipped so a scan cannot read files outside PHOTO_DIR.
    return not path.is_symlink() and path.is_file()


def list_image_files(photo_dir: Path) -> list[Path]:
    """Top-level image files in ``photo_dir`` sorted by file name (Req 1.1).

    Sub-folders and their contents, sidecars and other extensions are skipped.
    """
    return sorted(
        (p for p in photo_dir.iterdir() if is_image_candidate(p)),
        key=lambda p: p.name,
    )


def is_too_large(path: Path) -> bool:
    """True when the file is over 20MB. Reads only metadata (Req 1.12)."""
    return path.stat().st_size > MAX_IMAGE_BYTES


def check_image(data: bytes, ext: str) -> ImageCheckError | None:
    """Return None when ``data`` decodes as the format implied by ``ext`` (Req 1.8).

    Uses Pillow ``verify()``; any decode error, an unsupported extension, or a
    detected format that differs from the extension yields INVALID_IMAGE_FORMAT.
    """
    if len(data) > MAX_IMAGE_BYTES:
        return FILE_TOO_LARGE
    expected = expected_format(ext)
    if expected is None:
        return INVALID_IMAGE_FORMAT
    try:
        with warnings.catch_warnings():
            # Treat decompression-bomb warnings as errors, not log noise.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=[expected]) as img:
                detected = img.format
                img.verify()
    except Exception:  # Pillow raises many types for corrupt input
        return INVALID_IMAGE_FORMAT
    if detected != expected:
        return INVALID_IMAGE_FORMAT
    return None


def mime_type_for(ext: str) -> str:
    """MIME type for a supported extension. Raises KeyError if unsupported."""
    fmt = expected_format(ext)
    if fmt is None:
        raise KeyError(ext)
    return FORMAT_MIME_TYPES[fmt]
