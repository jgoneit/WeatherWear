#!/usr/bin/env python3
""""방금 찍은 사진" 흉내: put one new photo into Gallery_Dir (Phase 2 tasks 3.17, Req 8).

    backend/.venv/bin/python scripts/simulate_capture.py
    backend/.venv/bin/python scripts/simulate_capture.py --source ~/me.jpg --at 2026-11-14T08:10:00+09:00
    backend/.venv/bin/python scripts/simulate_capture.py --lat 35.18 --lon 129.08 --no-fixture

Steps (design.md §10):

1. Source: ``--source``, else the Capture_Pool_Dir image after ``.last_used``
   (``data/user/capture_pool/``, name order, wraps), else a drawn placeholder
   (cream knit + jeans).
2. A demo photo (name in the manifest, or a hash from ``vision.json`` /
   ``vision.sources.json``) is refused with exit code 2.
3. Time: ``--at`` (ISO 8601 with an offset), else now in APP_TIMEZONE.
   Place: ``--lat/--lon``, else Last_Location read from the DB *read-only*,
   else DEFAULT_LATITUDE/DEFAULT_LONGITUDE.
4. Re-encode with Embedded_Metadata (PNG/WebP → PNG, JPEG → JPEG q92).
5. Fixture mode without ``--no-fixture``: the overlay entry for the new hash is
   written first. Tags: ``--tags`` JSON, the pool's ``clothing_tags.json``,
   the placeholder's fixed tags; without tags the photo will stay
   ``uncertain`` (a notice goes to stderr).
6. Hidden ``.<name>.tmp`` in Gallery_Dir → ``capture_<YYYYMMDD_HHMMSS>.<ext>``.
7. Print the new file name only.

Offline: no Gemini, no Open-Meteo, no API server needed (Req 8.8).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from PIL import Image  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.domain import ClothingTags  # noqa: E402
from app.gallery.simulate import (  # noqa: E402
    PLACEHOLDER_TAGS,
    add_overlay_entry_atomic,
    draw_placeholder,
    encode_with_exif,
    is_demo_source,
    load_demo_identity,
    next_pool_source,
    output_format,
    overlay_entry,
    pool_tags,
    publish_atomic,
    remember_pool_source,
    tags_from_clothing_json,
)
from app.main import DEFAULT_ENV_FILE, load_settings, resolve_automation_paths, resolve_paths  # noqa: E402
from app.providers.vision.fixture import DEFAULT_VISION_FIXTURE_PATH  # noqa: E402

DEMO_DIR = REPO_ROOT / "data" / "demo"
MANIFEST_PATH = DEMO_DIR / "photos.manifest.json"
VISION_SOURCES_PATH = DEMO_DIR / "fixtures" / "vision.sources.json"

EXIT_OK = 0
EXIT_USAGE = 2
DEMO_REFUSED_MESSAGE = "데모 사진은 쓸 수 없습니다. --source에 다른 사진을 주거나 data/user/capture_pool/에 사진을 넣어 주세요."
UNCERTAIN_NOTICE = "태그가 없어 overlay를 쓰지 않았습니다. fixture 모드에서는 이 사진이 uncertain으로 남습니다."


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gallery_Dir에 '방금 찍은 사진' 1장을 만듭니다.")
    parser.add_argument("--source", type=Path, help="원본 이미지 경로")
    parser.add_argument("--at", help="촬영 시각 (ISO 8601, 오프셋 필수)")
    parser.add_argument("--lat", type=float, help="촬영 위도")
    parser.add_argument("--lon", type=float, help="촬영 경도")
    parser.add_argument("--tags", help='fixture 판정 태그 JSON (예: {"upper_type": "knit", ...})')
    parser.add_argument("--no-fixture", action="store_true", help="fixture 모드에서도 overlay를 쓰지 않음")
    parser.add_argument("--gallery-dir", type=Path, help="GALLERY_DIR 대신 쓸 폴더")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="읽을 .env 파일")
    return parser.parse_args(argv)


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return EXIT_USAGE


def load_config(args: argparse.Namespace) -> AppConfig:
    env_file = args.env_file if args.env_file and args.env_file.exists() else None
    config, _ = load_settings(env_file)
    if args.gallery_dir is not None:
        config = replace(config, automation=replace(config.automation, gallery_dir=args.gallery_dir))
    return resolve_automation_paths(resolve_paths(config))


def capture_time(raw: str | None, config: AppConfig) -> datetime | None:
    if raw is None:
        return datetime.now(config.app_timezone).replace(microsecond=0)
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if at.tzinfo is None or at.utcoffset() is None:
        return None
    return at.replace(microsecond=0)


def last_location_readonly(config: AppConfig) -> tuple[float, float] | None:
    """Last_Location from the SQLite file opened read-only; never creates or migrates it."""
    from sqlalchemy.engine import make_url

    url = make_url(config.database_url)
    database = url.database
    if not url.drivername.startswith("sqlite") or not database or database == ":memory:":
        return None
    path = Path(database)
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT latitude, longitude FROM user_location WHERE id = 1").fetchone()
    except sqlite3.Error:
        return None
    return (float(row[0]), float(row[1])) if row else None


def capture_place(args: argparse.Namespace, config: AppConfig) -> tuple[float, float] | str:
    if (args.lat is None) != (args.lon is None):
        return "--lat와 --lon은 함께 주어야 합니다."
    if args.lat is not None:
        if not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
            return "--lat는 -90~90, --lon은 -180~180이어야 합니다."
        return float(args.lat), float(args.lon)
    return last_location_readonly(config) or (config.default_latitude, config.default_longitude)


def parse_tags(raw: str) -> ClothingTags | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ClothingTags.model_validate(payload)
    except ValidationError:
        return tags_from_clothing_json(payload)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    pool_dir = config.automation.user_photo_dir.parent / "capture_pool"

    # 1. source image
    pool_source: Path | None = None
    if args.source is not None:
        source_path: Path | None = args.source
    else:
        pool_source = next_pool_source(pool_dir)
        source_path = pool_source
    if source_path is not None:
        try:
            raw = source_path.read_bytes()
            with Image.open(BytesIO(raw)) as opened:
                opened.load()
                image = opened.copy()
        except (OSError, ValueError, Image.DecompressionBombError):
            return fail("원본 이미지를 읽을 수 없습니다.")
        source_name = source_path.name
        source_sha = hashlib.sha256(raw).hexdigest()
        # 2. demo photos are never reused (Req 8.3)
        manifest_names, demo_hashes = load_demo_identity(
            MANIFEST_PATH, DEFAULT_VISION_FIXTURE_PATH, VISION_SOURCES_PATH
        )
        if is_demo_source(source_name, source_sha, manifest_names, demo_hashes):
            return fail(DEMO_REFUSED_MESSAGE)
        default_tags = pool_tags(pool_dir, source_name)
    else:
        image = draw_placeholder()
        source_name = "placeholder.jpg"
        default_tags = PLACEHOLDER_TAGS

    # 3. time and place
    at = capture_time(args.at, config)
    if at is None:
        return fail("--at은 오프셋이 있는 ISO 8601 시각이어야 합니다 (예: 2026-11-14T08:10:00+09:00).")
    place = capture_place(args, config)
    if isinstance(place, str):
        return fail(place)
    lat, lon = place

    # 4. re-encode with Embedded_Metadata
    fmt = output_format(source_name)
    try:
        data = encode_with_exif(image, fmt, at, lat, lon, config.app_timezone.key)
    except ValueError:
        return fail("촬영 시각의 UTC 오프셋은 분 단위여야 합니다.")
    sha256 = hashlib.sha256(data).hexdigest()

    # 5. fixture overlay before the file appears (Req 8.6)
    if config.vision_provider == "fixture" and not args.no_fixture:
        tags = default_tags
        if args.tags is not None:
            tags = parse_tags(args.tags)
            if tags is None:
                return fail("--tags는 ClothingTags JSON이어야 합니다.")
        if tags is not None:
            add_overlay_entry_atomic(config.automation.vision_overlay_path, sha256, overlay_entry(tags))
        else:
            print(UNCERTAIN_NOTICE, file=sys.stderr)

    # 6. hidden temp file → final name, never overwriting (Req 8.7)
    name = publish_atomic(config.automation.gallery_dir, data, at, ".jpg" if fmt == "JPEG" else ".png")
    if pool_source is not None:
        remember_pool_source(pool_dir, pool_source.name)

    # 7. the file name only (Req 8.1)
    print(name)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
