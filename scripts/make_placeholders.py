#!/usr/bin/env python3
"""Generate 8 placeholder outfit photos for when no real photo is rights-confirmed.

Fallback only (Req 15.7). The script refuses to run while the manifest has any
``rights_confirmed: true`` entry; use ``scripts/prepare_demo_photos.py`` then.

For each placeholder it writes ``<name>.png`` into ``data/demo/photos/``: a
512x640 (4:5) image with a temperature-colored background and the outfit text
(ASCII, rendered with Pillow's bundled font so the output does not depend on
fonts installed on the machine). Each PNG carries an EXIF UserComment in the
same format as ``ex_img/``: the 8-byte ``ASCII\\0\\0\\0`` header followed by a
JSON object with ``date``, ``time``, ``timezone``, ``utc_offset``,
``latitude``, ``longitude`` (Seoul) and ``synthetic: true``. No sidecar files
are written; ``scripts/select_scenarios.py`` adds seed-vote sidecars later.

It also writes:
- ``data/demo/fixtures/vision.json``: placeholder tags merged in, keyed by each
  PNG's SHA-256.
- ``data/demo/fixtures/weather.json``: one entry per placeholder with the
  deterministic seasonal formula value (``seasonal_temp``) and
  ``recorded.source = "placeholder"``. Entries for other files and an existing
  ``today_default`` are kept.

Output is deterministic. Commit the generated files so the hashes stay fixed; a
different Pillow version may encode the PNGs differently.

Usage: python scripts/make_placeholders.py [--manifest PATH] [--dest DIR]
       [--vision-fixture PATH] [--weather-fixture PATH] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.providers.weather.fixture import seasonal_temp  # noqa: E402

DEFAULT_MANIFEST = REPO_ROOT / "data" / "demo" / "photos.manifest.json"
DEFAULT_DEST = REPO_ROOT / "data" / "demo" / "photos"
DEFAULT_VISION_FIXTURE = REPO_ROOT / "data" / "demo" / "fixtures" / "vision.json"
DEFAULT_WEATHER_FIXTURE = REPO_ROOT / "data" / "demo" / "fixtures" / "weather.json"

IMAGE_SIZE = (512, 640)
TEXT_COLOR = (15, 23, 42)  # slate-900
TIMEZONE_NAME = "Asia/Seoul"
TZ = ZoneInfo(TIMEZONE_NAME)
SEOUL_LATITUDE = 37.5665
SEOUL_LONGITUDE = 126.9780

EXIF_IFD = 0x8769
TAG_USER_COMMENT = 0x9286
USER_COMMENT_HEADER = b"ASCII\x00\x00\x00"


@dataclass(frozen=True)
class Placeholder:
    name: str
    captured_at: str  # ISO 8601 with +09:00, all distinct
    outer_type: str
    upper_type: str
    lower_type: str
    colors: tuple[str, ...]

    @property
    def captured(self) -> datetime:
        return datetime.fromisoformat(self.captured_at)

    @property
    def temperature_c(self) -> float:
        local = self.captured.astimezone(TZ)
        return seasonal_temp(local.month, local.hour)

    @property
    def layers_visible(self) -> int:
        return 1 + (self.outer_type != "none")

    @property
    def outfit_text(self) -> str:
        parts = [t for t in (self.outer_type, self.upper_type, self.lower_type) if t not in {"none", "unknown"}]
        return " + ".join(p.replace("_", " ") for p in parts).upper()

    def tags(self) -> dict[str, object]:
        return {
            "upper_type": self.upper_type,
            "outer_type": self.outer_type,
            "lower_type": self.lower_type,
            "colors": list(self.colors),
            "layers_visible": self.layers_visible,
            "confidence": 1.0,
        }

    def user_comment(self, index: int) -> dict[str, object]:
        """Same keys as the ex_img/ UserComment JSON (extra keys are ignored by the reader)."""
        local = self.captured.astimezone(TZ)
        return {
            "index": index,
            "date": local.date().isoformat(),
            "time": local.time().isoformat(timespec="seconds"),
            "timezone": TIMEZONE_NAME,
            "utc_offset": local.strftime("%z")[:3] + ":" + local.strftime("%z")[3:],
            "latitude": SEOUL_LATITUDE,
            "longitude": SEOUL_LONGITUDE,
            "location": "Synthetic Seoul central area; placeholder coordinates, not an actual capture location.",
            "outfit": self.outfit_text.lower(),
            "synthetic": True,
            "source": "scripts/make_placeholders.py",
            "file": f"{self.name}.png",
        }

    def weather_entry(self) -> dict[str, object]:
        local = self.captured.astimezone(TZ)
        return {
            "file": f"{self.name}.png",
            "captured_at": self.captured_at,
            "latitude": SEOUL_LATITUDE,
            "longitude": SEOUL_LONGITUDE,
            "observed_at": local.replace(minute=0, second=0, microsecond=0).isoformat(),
            "actual_temperature_c": self.temperature_c,
            "apparent_temperature_c": None,
            "relative_humidity_pct": None,
            "wind_speed_kmh": None,
            "precipitation_mm": None,
            "weather_code": None,
        }


# Capture times are spread over the year so the seasonal formula gives a range
# of temperatures (about -6 to 29 °C).
PLACEHOLDERS: tuple[Placeholder, ...] = (
    Placeholder("ph_01_navy_parka", "2025-01-10T08:00:00+09:00", "parka", "knit", "jeans", ("navy", "grey")),
    Placeholder("ph_02_wool_coat", "2025-02-03T08:30:00+09:00", "wool_coat", "turtleneck", "slacks", ("camel", "black")),
    Placeholder("ph_03_cream_sweater", "2025-11-20T13:30:00+09:00", "none", "sweater", "slacks", ("cream", "brown")),
    Placeholder("ph_04_varsity_jacket", "2025-10-28T09:00:00+09:00", "varsity_jacket", "hoodie", "jeans", ("green", "white", "blue")),
    Placeholder("ph_05_sage_sweater", "2025-03-12T14:00:00+09:00", "none", "sweater", "jeans", ("sage", "blue")),
    Placeholder("ph_06_beige_trench", "2025-04-08T08:00:00+09:00", "trench_coat", "shirt", "slacks", ("beige", "white", "navy")),
    Placeholder("ph_07_oxford_shirt", "2025-05-20T15:30:00+09:00", "none", "shirt", "chinos", ("blue", "tan")),
    Placeholder("ph_08_linen_tee", "2025-07-15T14:00:00+09:00", "none", "t_shirt", "linen_trousers", ("white", "ivory")),
)


def background_for(temperature_c: float) -> tuple[int, int, int]:
    """Temperature colors from the design (ice blue / sage / peach / coral)."""
    if temperature_c <= 5:
        return (0xA5, 0xC8, 0xFF)
    if temperature_c < 17:
        return (0xBF, 0xE3, 0xD0)
    if temperature_c < 25:
        return (0xFF, 0xD3, 0xB0)
    return (0xFF, 0x9F, 0x8A)


def _draw_centered(draw: ImageDraw.ImageDraw, y: int, text: str, size: int) -> None:
    font = ImageFont.load_default(size=size)
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    x = (IMAGE_SIZE[0] - (right - left)) // 2 - left
    draw.text((x, y), text, fill=TEXT_COLOR, font=font)


def _wrap(text: str, max_chars: int = 18) -> list[str]:
    lines: list[str] = []
    for part in text.split(" + "):
        if lines and len(lines[-1]) + 3 + len(part) <= max_chars:
            lines[-1] += " + " + part
        else:
            lines.append(part)
    return lines


def _exif_with_user_comment(payload: dict[str, object]) -> bytes:
    exif = Image.Exif()
    body = json.dumps(payload, ensure_ascii=True, indent=2).encode("ascii")
    exif.get_ifd(EXIF_IFD)[TAG_USER_COMMENT] = USER_COMMENT_HEADER + body
    # An Exif with only a nested IFD is falsy, so Pillow's PNG writer would skip
    # it; pass the encoded bytes instead.
    return exif.tobytes()


def render_png(p: Placeholder, index: int = 0) -> bytes:
    image = Image.new("RGB", IMAGE_SIZE, background_for(p.temperature_c))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, IMAGE_SIZE[0] - 32, IMAGE_SIZE[1] - 32), radius=36, outline=TEXT_COLOR, width=4)
    _draw_centered(draw, 80, "PLACEHOLDER", 32)
    _draw_centered(draw, 180, f"{p.temperature_c:.1f}°C", 72)
    y = 330
    for line in _wrap(p.outfit_text or "UNKNOWN"):
        _draw_centered(draw, y, line, 36)
        y += 52
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, exif=_exif_with_user_comment(p.user_comment(index)))
    return buffer.getvalue()


def confirmed_photo_count(manifest_path: Path) -> int:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    entries = data.get("photos", []) if isinstance(data, dict) else []
    return sum(1 for e in entries if isinstance(e, dict) and e.get("rights_confirmed") is True)


def _read_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, data: object, *, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n", encoding="utf-8")


def _placeholder_recorded_block() -> dict[str, object]:
    return {
        "source": "placeholder",
        "endpoint": None,
        "hourly": [],
        "timezone": TIMEZONE_NAME,
        "note": "make_placeholders.py가 결정적 계절 공식(seasonal_temp)으로 만든 값. 앱은 provider=fixture로 표시한다",
    }


def write_weather_fixture(path: Path) -> None:
    """Replace placeholder entries in weather.json; keep other entries and today_default."""
    existing = _read_json_object(path)
    names = {f"{p.name}.png" for p in PLACEHOLDERS}
    raw_entries = existing.get("entries", [])
    others = [e for e in raw_entries if isinstance(e, dict) and e.get("file") not in names] \
        if isinstance(raw_entries, list) else []
    recorded = existing.get("recorded") if others and isinstance(existing.get("recorded"), dict) \
        else _placeholder_recorded_block()
    data: dict[str, object] = {"recorded": recorded, "entries": others + [p.weather_entry() for p in PLACEHOLDERS]}
    if "today_default" in existing:
        data["today_default"] = existing["today_default"]
    _write_json(path, data)


def generate(dest_dir: Path, vision_fixture: Path, weather_fixture: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    tags_by_hash: dict[str, object] = {}
    for index, p in enumerate(PLACEHOLDERS):
        png = render_png(p, index)
        (dest_dir / f"{p.name}.png").write_bytes(png)
        written.append(f"{p.name}.png")
        tags_by_hash[hashlib.sha256(png).hexdigest()] = p.tags()

    vision = _read_json_object(vision_fixture)
    vision.update(tags_by_hash)
    _write_json(vision_fixture, vision, sort_keys=True)
    write_weather_fixture(weather_fixture)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--vision-fixture", type=Path, default=DEFAULT_VISION_FIXTURE)
    parser.add_argument("--weather-fixture", type=Path, default=DEFAULT_WEATHER_FIXTURE)
    parser.add_argument("--force", action="store_true", help="generate even if confirmed photos exist")
    args = parser.parse_args(argv)

    confirmed = confirmed_photo_count(args.manifest)
    if confirmed > 0 and not args.force:
        print(
            f"{confirmed} rights-confirmed photos in the manifest; placeholders are not needed. "
            "Run scripts/prepare_demo_photos.py instead (or pass --force).",
            file=sys.stderr,
        )
        return 1

    written = generate(args.dest, args.vision_fixture, args.weather_fixture)
    print(f"wrote {len(written)} photos to {args.dest} and updated {args.vision_fixture}, {args.weather_fixture}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
