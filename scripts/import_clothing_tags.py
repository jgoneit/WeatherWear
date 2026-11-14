#!/usr/bin/env python3
"""Add missing photos from ``ex_img/clothing_tags.json`` to the vision fixture.

``data/demo/fixtures/vision.json`` maps the SHA-256 of each demo photo to a
``VisionAnalysis`` (photo type, confidence, reason and canonical
``ClothingTags``). This script only supplements it: entries that are already
there are written back unchanged, and entries are added only for photos whose
hash is missing.

1. Check that ``schema == "WeatherWear ClothingTags"`` and that the number of
   ``images`` entries equals ``image_count``.
2. Read the current ``vision.json``. Every value must pass ``VisionAnalysis``.
3. Link each photo in ``data/demo/photos`` to its ``clothing_tags.json`` entry
   by file name. The free-text garment names (``"t-shirt"``, ``"utility vest"``)
   are mapped onto the canonical taxonomy with ``normalize_*_type``. A name
   that maps to ``unknown`` without being ``unknown`` or one of the taxonomy's
   own aliases for ``unknown`` (``"top"``) is reported as unmapped.
4. Build the ``VisionAnalysis`` with the fixture convention of
   ``scripts/build_fixtures.py``: tags equal to ``UNKNOWN_TAGS`` mean
   ``non_outfit``, anything else ``outfit``, confidence 1.0 and a fixed reason.
5. Key it by the SHA-256 of the current photo bytes (the value the ingestion
   service computes). The ``sha256`` in ``clothing_tags.json`` was taken
   before the metadata was embedded, so it is only kept as ``source_sha256`` in
   ``vision.sources.json``.
6. A hash that is already in ``vision.json`` must get exactly the stored
   value; if the source would change it, that is a problem. Existing entries
   are never replaced or removed. An existing key that matches no photo is
   kept and printed as a warning.

``vision.json`` is written in photo file name order (existing values as they
were, new ones at their file's position); ``vision.sources.json`` is rewritten
for every photo. If any entry is missing, fails validation, cannot be mapped,
would change an existing entry, or two photos share a hash, the problems are
printed, nothing is written, and the exit code is 1.

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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.domain import (  # noqa: E402
    _LOWER_ALIASES,
    _OUTER_ALIASES,
    _UPPER_ALIASES,
    UNKNOWN_TAGS,
    ClothingTags,
    VisionAnalysis,
    normalize_lower_type,
    normalize_outer_type,
    normalize_upper_type,
)

DEFAULT_SOURCE = REPO_ROOT / "ex_img" / "clothing_tags.json"
DEFAULT_PHOTOS = REPO_ROOT / "data" / "demo" / "photos"
DEFAULT_FIXTURES = REPO_ROOT / "data" / "demo" / "fixtures"
EXPECTED_SCHEMA = "WeatherWear ClothingTags"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Same convention as scripts/build_fixtures.py ``vision_analysis_for``.
FIXTURE_PHOTO_TYPE_CONFIDENCE = 1.0
OUTFIT_REASON = "A person with visible clothing is the main subject."
NON_OUTFIT_REASON = "No person with a visible outfit."

_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "upper_type": normalize_upper_type,
    "outer_type": normalize_outer_type,
    "lower_type": normalize_lower_type,
}
# Names the taxonomy maps to "unknown" on purpose (e.g. a generic "top").
# Any other name that ends up "unknown" is an unmapped spelling.
_UNKNOWN_ON_PURPOSE: dict[str, set[str]] = {
    key: {"unknown"} | {name for name, value in aliases.items() if value == "unknown"}
    for key, aliases in (
        ("upper_type", _UPPER_ALIASES),
        ("outer_type", _OUTER_ALIASES),
        ("lower_type", _LOWER_ALIASES),
    )
}


@dataclass
class Supplement:
    """Result of ``build_supplement``. Nothing is written until ``run``."""

    vision: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    added: dict[str, str] = field(default_factory=dict)  # file name -> sha256
    kept: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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


def canonical_tags(raw: Any) -> tuple[ClothingTags | None, list[str]]:
    """Map free-text garment names onto the canonical taxonomy and validate.

    Returns ``(tags, problems)``; ``tags`` is None when there is a problem.
    """
    if not isinstance(raw, dict):
        return None, ["tags is not an object"]
    mapped = dict(raw)
    problems = []
    for key, normalize in _NORMALIZERS.items():
        value = raw.get(key)
        if not isinstance(value, str):
            problems.append(f"{key} is not a string")
            continue
        mapped[key] = normalize(value)
        spelled = value.strip().lower().replace("-", "_").replace(" ", "_")
        if mapped[key] == "unknown" and spelled not in _UNKNOWN_ON_PURPOSE[key]:
            problems.append(f"{key} {value!r} has no canonical value")
    if problems:
        return None, problems
    try:
        return ClothingTags.model_validate(mapped), []
    except ValidationError as exc:
        return None, [f"invalid tags ({exc.error_count()} errors)"]


def analysis_for(tags: ClothingTags) -> VisionAnalysis:
    """Fixture ``VisionAnalysis`` for hand-checked tags."""
    non_outfit = tags == UNKNOWN_TAGS
    return VisionAnalysis(
        photo_type="non_outfit" if non_outfit else "outfit",
        photo_type_confidence=FIXTURE_PHOTO_TYPE_CONFIDENCE,
        reason=NON_OUTFIT_REASON if non_outfit else OUTFIT_REASON,
        tags=tags,
    )


def _read_existing(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot read {path.name}: {exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{path.name}: top level is not an object"]
    problems = []
    for key, value in payload.items():
        try:
            VisionAnalysis.model_validate(value)
        except ValidationError as exc:
            problems.append(f"{path.name}: entry {key[:12]}… is not a VisionAnalysis "
                            f"({exc.error_count()} errors)")
    return payload, problems


def build_supplement(source: Path, photo_dir: Path, existing_path: Path) -> Supplement:
    """Compute the supplemented fixture. Writes nothing."""
    result = Supplement()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.problems.append(f"cannot read {source.name}: {exc}")
        return result
    if not isinstance(payload, dict) or payload.get("schema") != EXPECTED_SCHEMA:
        result.problems.append(f"{source.name}: schema is not {EXPECTED_SCHEMA!r}")
        return result
    images = payload.get("images")
    if not isinstance(images, dict):
        result.problems.append(f"{source.name}: 'images' is not an object")
        return result
    if len(images) != payload.get("image_count"):
        result.problems.append(
            f"{source.name}: {len(images)} images but image_count={payload.get('image_count')}"
        )

    existing, problems = _read_existing(existing_path)
    result.problems.extend(problems)

    seen: dict[str, str] = {}
    for photo in list_photos(photo_dir):
        name = photo.name
        entry = images.get(name)
        if not isinstance(entry, dict) or "tags" not in entry:
            result.problems.append(f"{name}: no tags in {source.name}")
            continue
        digest = sha256_of(photo)
        if digest in seen:
            result.problems.append(f"{name}: same SHA-256 as {seen[digest]}")
            continue
        seen[digest] = name
        tags, tag_problems = canonical_tags(entry["tags"])
        if tags is None:
            result.problems.extend(f"{name}: {p}" for p in tag_problems)
            continue
        candidate = analysis_for(tags).model_dump(mode="json")
        if digest in existing:
            if existing[digest] != candidate:
                result.problems.append(
                    f"{name}: {source.name} would change the existing {existing_path.name} entry"
                )
                continue
            result.vision[digest] = existing[digest]
            result.kept += 1
        else:
            result.vision[digest] = candidate
            result.added[name] = digest
        result.sources[digest] = {"file": name, "source_sha256": entry.get("sha256")}

    unknown_files = sorted(set(images) - {p.name for p in list_photos(photo_dir)})
    result.problems.extend(f"{name}: in {source.name} but not in {photo_dir.name}/" for name in unknown_files)
    for key, value in existing.items():
        if key not in result.vision and key not in seen:
            result.vision[key] = value  # never removed
            result.warnings.append(f"{existing_path.name} entry {key[:12]}… matches no photo (kept)")
    return result


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
    vision_path = fixtures_dir / "vision.json"
    result = build_supplement(source, photo_dir, vision_path)
    for warning in result.warnings:
        print(f"warning: {warning}")
    if result.problems:
        print(f"{len(result.problems)} problem(s); nothing written:")
        for p in result.problems:
            print(f"  - {p}")
        return 1
    _write_json_atomic(vision_path, result.vision)
    _write_json_atomic(fixtures_dir / "vision.sources.json", result.sources)
    print(f"{vision_path.name}: kept {result.kept}, added {len(result.added)}, "
          f"total {len(result.vision)}; wrote {fixtures_dir.name}/vision.sources.json")
    for name in result.added:
        print(f"  + {name}")
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
