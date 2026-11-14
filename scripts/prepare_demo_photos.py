#!/usr/bin/env python3
"""Copy rights-confirmed demo photos from ``ex_img/`` into ``data/demo/photos/``.

Only manifest entries with ``"rights_confirmed": true`` are copied, keeping the
original file name and extension. For each copied photo a sidecar template
(``<stem>.metadata.json``) is created when it does not exist yet. Existing
sidecars are never touched, so values filled in later (task 2.3) survive reruns.

Sidecar templates:
- ``YYYY-MM-DD_HHMMSS_KST.jpg`` photos get ``captured_at`` from the file name
  as KST (``+09:00``), e.g. ``2025-01-08_081500_KST.jpg`` ->
  ``2025-01-08T08:15:00+09:00``.
- Other photos get ``captured_at: null``; the real value is written by hand
  (design "사이드카 작성 규칙"). Until then the scanner rejects the photo with
  ``SIDECAR_MISSING_FIELD`` instead of storing an invented timestamp.
- ``latitude``/``longitude`` default to the Seoul coordinates.

The script is idempotent: identical photos are skipped (SHA-256 compared),
existing sidecars are kept, and nothing is deleted. If no photo is confirmed,
run ``scripts/make_placeholders.py`` instead.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "data" / "demo" / "photos.manifest.json"
DEFAULT_SOURCE = REPO_ROOT / "ex_img"
DEFAULT_DEST = REPO_ROOT / "data" / "demo" / "photos"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SIDECAR_SUFFIX = ".metadata.json"
KST = timezone(timedelta(hours=9))
SEOUL_LATITUDE = 37.5665
SEOUL_LONGITUDE = 126.9780

_KST_NAME = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})_(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})_KST\.jpe?g$",
    re.IGNORECASE,
)


class ManifestError(ValueError):
    """The manifest is missing, malformed, or points outside the source folder."""


@dataclass(frozen=True)
class ConfirmedPhoto:
    file: str
    origin: Path


@dataclass
class PrepareReport:
    confirmed: int = 0
    copied: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    sidecars_created: list[str] = field(default_factory=list)
    sidecars_kept: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    unlisted_in_dest: list[str] = field(default_factory=list)


def sidecar_path_for(image: Path) -> Path:
    """``001_front.png`` -> ``001_front.metadata.json``."""
    return image.with_name(image.stem + SIDECAR_SUFFIX)


def captured_at_from_kst_name(file_name: str) -> str | None:
    """Return the ISO 8601 KST timestamp encoded in a ``YYYY-MM-DD_HHMMSS_KST.jpg`` name.

    Returns None for names that do not follow the pattern or encode an invalid
    date/time.
    """
    match = _KST_NAME.match(file_name)
    if match is None:
        return None
    try:
        value = datetime(
            int(match["y"]), int(match["mo"]), int(match["d"]),
            int(match["h"]), int(match["mi"]), int(match["s"]),
            tzinfo=KST,
        )
    except ValueError:
        return None
    return value.isoformat()


def sidecar_template(file_name: str) -> dict[str, object]:
    return {
        "captured_at": captured_at_from_kst_name(file_name),
        "latitude": SEOUL_LATITUDE,
        "longitude": SEOUL_LONGITUDE,
    }


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
        confirmed.append(ConfirmedPhoto(file=file_name, origin=origin_path))
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


def _write_sidecar_if_missing(path: Path, content: dict[str, object]) -> bool:
    """Create the sidecar only if no file exists at ``path``. Returns True when created."""
    text = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        return False
    return True


def prepare(manifest_path: Path, source_dir: Path, dest_dir: Path, repo_root: Path = REPO_ROOT) -> PrepareReport:
    photos = load_confirmed_photos(manifest_path, source_dir, repo_root)
    report = PrepareReport(confirmed=len(photos))
    if not photos:
        return report

    dest_dir.mkdir(parents=True, exist_ok=True)
    for photo in photos:
        if not photo.origin.is_file():
            report.missing_sources.append(photo.file)
            continue
        target = dest_dir / photo.file
        if target.is_file():
            if _same_content(photo.origin, target):
                report.unchanged.append(photo.file)
            else:
                _atomic_copy(photo.origin, target)
                report.replaced.append(photo.file)
        else:
            _atomic_copy(photo.origin, target)
            report.copied.append(photo.file)

        sidecar = sidecar_path_for(target)
        if _write_sidecar_if_missing(sidecar, sidecar_template(photo.file)):
            report.sidecars_created.append(sidecar.name)
        else:
            report.sidecars_kept.append(sidecar.name)

    listed = {photo.file for photo in photos}
    report.unlisted_in_dest = sorted(
        entry.name
        for entry in dest_dir.iterdir()
        if entry.is_file()
        and entry.suffix.lower() in SUPPORTED_EXTENSIONS
        and entry.name not in listed
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)

    try:
        report = prepare(args.manifest, args.source, args.dest)
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
    print(f"sidecars created : {len(report.sidecars_created)}")
    print(f"sidecars kept    : {len(report.sidecars_kept)}")
    for name in report.replaced:
        print(f"  replaced (content differed from source): {name}")
    for name in report.unlisted_in_dest:
        print(f"warning: {name} is in {args.dest} but not confirmed in the manifest", file=sys.stderr)
    if report.missing_sources:
        for name in report.missing_sources:
            print(f"error: source photo missing: {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
