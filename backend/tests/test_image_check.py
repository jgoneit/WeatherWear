"""Unit tests for image discovery and validation (Req 1.1, 1.8, 1.12)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.ingestion.image_check import (
    MAX_IMAGE_BYTES,
    SUPPORTED_EXTENSIONS,
    check_image,
    is_too_large,
    list_image_files,
    mime_type_for,
)

DEMO_PHOTOS = Path(__file__).resolve().parents[2] / "data" / "demo" / "photos"


def encode(fmt: str, size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buf, format=fmt)
    return buf.getvalue()


# --- list_image_files (Req 1.1) -------------------------------------------


def test_list_image_files_filters_and_sorts(tmp_path: Path) -> None:
    for name in [
        "b.PNG", "a.jpg", "c.JpEg", "d.webp",           # supported, mixed case
        "a.metadata.json", "notes.txt", "e.gif", "noext",  # skipped
    ]:
        (tmp_path / name).write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inner.png").write_bytes(b"x")
    (tmp_path / "folder.png").mkdir()  # a directory with an image-like name

    names = [p.name for p in list_image_files(tmp_path)]

    assert names == ["a.jpg", "b.PNG", "c.JpEg", "d.webp"]


def test_list_image_files_skips_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    photo_dir = tmp_path / "photos"
    photo_dir.mkdir()
    (photo_dir / "real.png").write_bytes(b"x")
    (photo_dir / "link.png").symlink_to(outside)

    assert [p.name for p in list_image_files(photo_dir)] == ["real.png"]


def test_list_image_files_empty_dir(tmp_path: Path) -> None:
    assert list_image_files(tmp_path) == []


# --- size limit (Req 1.12) --------------------------------------------------


def test_max_image_bytes_is_20mb() -> None:
    assert MAX_IMAGE_BYTES == 20 * 1024 * 1024


def test_is_too_large_boundary(tmp_path: Path) -> None:
    exact = tmp_path / "exact.png"
    over = tmp_path / "over.png"
    # Sparse files: no 20MB actually written to disk.
    with exact.open("wb") as f:
        f.truncate(MAX_IMAGE_BYTES)
    with over.open("wb") as f:
        f.truncate(MAX_IMAGE_BYTES + 1)

    assert is_too_large(exact) is False
    assert is_too_large(over) is True


# --- check_image (Req 1.8) --------------------------------------------------


@pytest.mark.parametrize(
    ("ext", "fmt"),
    [(".jpg", "JPEG"), (".JPEG", "JPEG"), (".png", "PNG"), (".WebP", "WEBP")],
)
def test_check_image_accepts_matching_format(ext: str, fmt: str) -> None:
    assert check_image(encode(fmt), ext) is None


@pytest.mark.parametrize(
    ("ext", "fmt"),
    [(".png", "JPEG"), (".jpg", "PNG"), (".webp", "PNG"), (".png", "WEBP")],
)
def test_check_image_rejects_format_mismatch(ext: str, fmt: str) -> None:
    result = check_image(encode(fmt), ext)
    assert result is not None and result.code == "INVALID_IMAGE_FORMAT"


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", encode("PNG")[:40], b"\x89PNG\r\n\x1a\n" + b"\x00" * 64],
)
def test_check_image_rejects_undecodable_bytes(data: bytes) -> None:
    result = check_image(data, ".png")
    assert result is not None and result.code == "INVALID_IMAGE_FORMAT"


def test_check_image_rejects_unsupported_extension() -> None:
    result = check_image(encode("PNG"), ".gif")
    assert result is not None and result.code == "INVALID_IMAGE_FORMAT"


def test_check_image_error_message_has_no_path() -> None:
    result = check_image(b"junk", ".png")
    assert result is not None and "/" not in result.message


def test_mime_type_for_supported_extensions() -> None:
    assert mime_type_for(".JPG") == "image/jpeg"
    assert mime_type_for(".jpeg") == "image/jpeg"
    assert mime_type_for(".png") == "image/png"
    assert mime_type_for(".webp") == "image/webp"
    assert set(SUPPORTED_EXTENSIONS) == {".jpg", ".jpeg", ".png", ".webp"}


# --- real demo photos --------------------------------------------------------


def demo_images_present() -> bool:
    # The sidecar *.metadata.json files are tracked but the photos are
    # git-ignored, so a fresh clone has the folder without any images.
    return DEMO_PHOTOS.is_dir() and any(
        p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        for p in DEMO_PHOTOS.iterdir()
    )


@pytest.mark.skipif(
    not demo_images_present(),
    reason=(
        "data/demo/photos has no images (they are git-ignored); "
        "run scripts/prepare_demo_photos.py to copy the 159 demo photos"
    ),
)
def test_all_demo_photos_pass_check_image() -> None:
    files = list_image_files(DEMO_PHOTOS)
    failures = [
        p.name
        for p in files
        if is_too_large(p) or check_image(p.read_bytes(), p.suffix) is not None
    ]
    assert failures == []
    assert len(files) == 159
    suffixes = {p.suffix.lower() for p in files}
    assert suffixes == {".png", ".jpg"}
