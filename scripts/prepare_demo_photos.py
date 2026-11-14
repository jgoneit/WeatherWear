#!/usr/bin/env python3
"""Copy rights-confirmed demo photos from ``ex_img/`` into ``data/demo/photos/``.

Only manifest entries with ``"rights_confirmed": true`` are copied, keeping the
original file name and extension. Files are copied byte for byte with
``shutil.copyfile`` (never re-encoded with Pillow), so the embedded EXIF
metadata and the SHA-256 hash stay the same as in ``ex_img/``. A destination
file whose content differs from the source (e.g. an older copy made before the
metadata was embedded) is replaced.

After copying, every confirmed photo is read with the backend's
``read_embedded_metadata``. If any photo has no valid embedded capture
time/location, the script prints the file name and error code and exits with 1.

Sidecars (``<stem>.metadata.json``) are optional and only carry seed votes
(design "데모 데이터 파일"). This script does not create, edit or delete them;
``scripts/select_scenarios.py`` rewrites them. Existing sidecars are only
counted in the report.

The script is idempotent: identical photos are skipped (SHA-256 compared) and
nothing is deleted. If no photo is confirmed, run ``scripts/make_placeholders.py``
instead.

Usage: python scripts/prepare_demo_photos.py [--manifest PATH] [--source DIR] [--dest DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "data" / "demo" / "photos.manifest.json"
DEFAULT_SOURCE = REPO_ROOT / "ex_img"
DEFAULT_DEST = REPO_ROOT / "data" / "demo" / "photos"

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.domain import CaptureMetadata  # noqa: E402
from app.ingestion.metadata import read_embedded_metadata  # noqa: E402

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SIDECAR_SUFFIX = ".metadata.json"


class ManifestError(ValueError):
    """The manifest is missing, malformed, or points outside the source folder."""


@dataclass(frozen=True)
class ConfirmedPhoto:
    file: str
    origin: Path
    sha256: str | None = None


@dataclass(frozen=True)
class MetadataProblem:
    file: str
    code: str


@dataclass
class PrepareReport:
    confirmed: int = 0
    copied: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    metadata_problems: list[MetadataProblem] = field(default_factory=list)
    sidecars_present: int = 0
    unlisted_in_dest: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_sources and not self.metadata_problems


def load_confirmed_photos(manifest_path: Path, source_dir: Path, repo_root: Path) -> list[ConfirmedPhoto]:
    """Read the manifest and return the ``rights_confirmed: true`` entries.

    Each entry's ``file`` must be a bare file name with a supported extension,
    and ``origin`` (relative to ``repo_root``) must resolve to a file of the
    same name directly inside ``source_dir``.
    """
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc

    entries = data.get("photos") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ManifestError('manifest must be an object with a "photos" list')

    source_root = source_dir.resolve()
    confirmed: list[ConfirmedPhoto] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ManifestError(f"photos[{index}] is not an object")
        if entry.get("rights_confirmed") is not True:
            continue
        file_name = entry.get("file")
        origin = entry.get("origin")
        if not isinstance(file_name, str) or not isinstance(origin, str):
            raise ManifestError(f'photos[{index}] needs string "file" and "origin"')
        if Path(file_name).name != file_name or file_name in {"", ".", ".."}:
            raise ManifestError(f"photos[{index}].file must be a bare file name: {file_name!r}")
        if Path(file_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ManifestError(f"photos[{index}].file has an unsupported extension: {file_name!r}")
        origin_path = (repo_root / origin).resolve()
        if origin_path.parent != source_root or origin_path.name != file_name:
            raise ManifestError(
                f"photos[{index}].origin must be {source_dir.name}/{file_name}: {origin!r}"
            )
        if file_name in seen:
            raise ManifestError(f"duplicate confirmed file in manifest: {file_name!r}")
        seen.add(file_name)
        digest = entry.get("sha256")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ManifestError(f"photos[{index}].sha256 must be a lowercase SHA-256 digest")
        confirmed.append(ConfirmedPhoto(file=file_name, origin=origin_path, sha256=digest))
    return confirmed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_content(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and _sha256(a) == _sha256(b)


def _atomic_copy(src: Path, dest: Path) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-", suffix=dest.suffix)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def check_embedded_metadata(data: bytes) -> str | None:
    """Return None when the image has valid embedded capture metadata, else an error code."""
    try:
        result = read_embedded_metadata(data)
    except Exception:  # noqa: BLE001 - an unreadable image is reported, not raised
        return "METADATA_READ_FAILED"
    if result is None:
        return "METADATA_NOT_FOUND"
    if isinstance(result, CaptureMetadata):
        return None
    return result.code


def prepare(manifest_path: Path, source_dir: Path, dest_dir: Path, repo_root: Path = REPO_ROOT) -> PrepareReport:
    photos = load_confirmed_photos(manifest_path, source_dir, repo_root)
    report = PrepareReport(confirmed=len(photos))
    if not photos:
        return report

    dest_dir.mkdir(parents=True, exist_ok=True)
    for photo in photos:
        target = dest_dir / photo.file
        if not photo.origin.is_file():
            # Generated assets already ship at the destination. Only accept
            # existing bytes when the manifest explicitly pins their digest.
            if photo.sha256 and target.is_file() and _sha256(target) == photo.sha256:
                report.unchanged.append(photo.file)
            else:
                report.missing_sources.append(photo.file)
                continue
        elif target.is_file():
            if _same_content(photo.origin, target):
                report.unchanged.append(photo.file)
            else:
                _atomic_copy(photo.origin, target)
                report.replaced.append(photo.file)
        else:
            _atomic_copy(photo.origin, target)
            report.copied.append(photo.file)

        code = check_embedded_metadata(target.read_bytes())
        if code is not None:
            report.metadata_problems.append(MetadataProblem(file=photo.file, code=code))

    listed = {photo.file for photo in photos}
    report.unlisted_in_dest = sorted(
        entry.name
        for entry in dest_dir.iterdir()
        if entry.is_file()
        and entry.suffix.lower() in SUPPORTED_EXTENSIONS
        and entry.name not in listed
    )
    report.sidecars_present = sum(
        1 for entry in dest_dir.iterdir() if entry.is_file() and entry.name.endswith(SIDECAR_SUFFIX)
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)

    try:
        report = prepare(args.manifest, args.source, args.dest, repo_root=REPO_ROOT)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if report.confirmed == 0:
        print("No rights-confirmed photos in the manifest. Run scripts/make_placeholders.py instead.")
        return 0

    print(f"confirmed photos : {report.confirmed}")
    print(f"copied           : {len(report.copied)}")
    print(f"replaced         : {len(report.replaced)}")
    print(f"unchanged        : {len(report.unchanged)}")
    print(f"metadata ok      : {report.confirmed - len(report.missing_sources) - len(report.metadata_problems)}")
    print(f"sidecars present : {report.sidecars_present} (not modified; select_scenarios.py rewrites them)")
    for name in report.unlisted_in_dest:
        print(f"warning: {name} is in {args.dest} but not confirmed in the manifest", file=sys.stderr)
    for name in report.missing_sources:
        print(f"error: source photo missing: {name}", file=sys.stderr)
    for problem in report.metadata_problems:
        print(f"error: no valid embedded metadata: {problem.file} ({problem.code})", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
