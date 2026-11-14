#!/usr/bin/env python3
"""Import ``ex_img/clothing_tags.json`` into the WeatherWear vision fixture.

Design "의류 태그 fixture" (Req 15.12):

1. Check that ``schema == "WeatherWear ClothingTags"`` and that the number of
   ``images`` entries equals ``image_count``.
2. Link each entry to ``data/demo/photos/<file>`` by file name and validate its
   ``tags`` with ``ClothingTags``. Every photo in ``data/demo/photos`` must have
   an entry.
3. Key the result by the SHA-256 of the current photo bytes (the value the
   ingestion service computes). The ``sha256`` in ``clothing_tags.json`` was
   taken before the metadata was embedded, so it is only kept as
   ``source_sha256`` in ``vision.sources.json``.

If any entry is missing, fails validation, or two photos share a hash, the
problems are printed, nothing is written, and the exit code is 1.

Usage: backend/.venv/bin/python scripts/import_clothing_tags.py
       [--source FILE] [--photos DIR] [--fixtures DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.domain import ClothingTags  # noqa: E402

DEFAULT_SOURCE = REPO_ROOT / "ex_img" / "clothing_tags.json"
DEFAULT_PHOTOS = REPO_ROOT / "data" / "demo" / "photos"
DEFAULT_FIXTURES = REPO_ROOT / "data" / "demo" / "fixtures"
EXPECTED_SCHEMA = "WeatherWear ClothingTags"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_photos(photo_dir: Path) -> list[Path]:
    return sorted(
        p for p in photo_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def build_vision_fixture(
    source: Path, photo_dir: Path
) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """Return ``(vision, sources, problems)``. Writes nothing."""
    problems: list[str] = []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {}, [f"cannot read {source.name}: {exc}"]

    if not isinstance(payload, dict) or payload.get("schema") != EXPECTED_SCHEMA:
        return {}, {}, [f"{source.name}: schema is not {EXPECTED_SCHEMA!r}"]
    images = payload.get("images")
    if not isinstance(images, dict):
        return {}, {}, [f"{source.name}: 'images' is not an object"]
    if len(images) != payload.get("image_count"):
        problems.append(
            f"{source.name}: {len(images)} images but image_count={payload.get('image_count')}"
        )

    vision: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for file_name in sorted(images):
        entry = images[file_name]
        photo = photo_dir / file_name
        if not photo.is_file():
            problems.append(f"{file_name}: photo not found in {photo_dir.name}/")
            continue
        if not isinstance(entry, dict) or "tags" not in entry:
            problems.append(f"{file_name}: entry has no tags")
            continue
        try:
            tags = ClothingTags.model_validate(entry["tags"])
        except ValidationError as exc:
            problems.append(f"{file_name}: invalid tags ({exc.error_count()} errors)")
            continue
        digest = sha256_of(photo)
        if digest in vision:
            problems.append(f"{file_name}: same SHA-256 as {sources[digest]['file']}")
            continue
        vision[digest] = tags.model_dump(mode="json")
        sources[digest] = {"file": file_name, "source_sha256": entry.get("sha256")}

    untagged = sorted(p.name for p in list_photos(photo_dir) if p.name not in images)
    problems.extend(f"{name}: photo has no entry in {source.name}" for name in untagged)
    return vision, sources, problems


def _write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def run(source: Path, photo_dir: Path, fixtures_dir: Path) -> int:
    vision, sources, problems = build_vision_fixture(source, photo_dir)
    if problems:
        print(f"{len(problems)} problem(s); nothing written:")
        for p in problems:
            print(f"  - {p}")
        return 1
    _write_json_atomic(fixtures_dir / "vision.json", vision)
    _write_json_atomic(fixtures_dir / "vision.sources.json", sources)
    print(f"wrote {len(vision)} entries to {fixtures_dir.name}/vision.json "
          f"and {fixtures_dir.name}/vision.sources.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--photos", type=Path, default=DEFAULT_PHOTOS)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    args = parser.parse_args(argv)
    return run(args.source, args.photos, args.fixtures)


if __name__ == "__main__":
    sys.exit(main())
