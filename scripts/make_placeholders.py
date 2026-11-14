#!/usr/bin/env python3
"""Generate 8 placeholder outfit photos for when no real photo is rights-confirmed.

Fallback only (Req 15.7). The script refuses to run while the manifest has any
``rights_confirmed: true`` entry; use ``scripts/prepare_demo_photos.py`` then.

For each placeholder it writes, into ``data/demo/photos/``:
- ``<name>.png``: 512x640 (4:5) image with a temperature-colored background and
  the outfit text (ASCII, rendered with Pillow's bundled font so the output does
  not depend on fonts installed on the machine).
- ``<name>.metadata.json``: a valid sidecar (``captured_at`` +09:00, Seoul
  coordinates, optional ``thermal_vote``). Existing sidecars are kept.

It also merges the placeholder tags into ``data/demo/fixtures/vision.json``
keyed by each PNG's SHA-256. Commit the generated files so the hashes stay
fixed; a different Pillow version may encode the PNGs differently.

Usage: python scripts/make_placeholders.py [--manifest PATH] [--dest DIR] [--vision-fixture PATH] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "data" / "demo" / "photos.manifest.json"
DEFAULT_DEST = REPO_ROOT / "data" / "demo" / "photos"
DEFAULT_VISION_FIXTURE = REPO_ROOT / "data" / "demo" / "fixtures" / "vision.json"

IMAGE_SIZE = (512, 640)
TEXT_COLOR = (15, 23, 42)  # slate-900
SEOUL_LATITUDE = 37.5665
SEOUL_LONGITUDE = 126.9780


@dataclass(frozen=True)
class Placeholder:
    name: str
    temperature_c: float
    captured_at: str
    outer_type: str
    upper_type: str
    lower_type: str
    colors: tuple[str, ...]
    thermal_vote: str | None = None

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

    def sidecar(self) -> dict[str, object]:
        data: dict[str, object] = {
            "captured_at": self.captured_at,
            "latitude": SEOUL_LATITUDE,
            "longitude": SEOUL_LONGITUDE,
        }
        if self.thermal_vote is not None:
            data["thermal_vote"] = self.thermal_vote
        data["note"] = f"placeholder, fixture temperature {self.temperature_c:.1f}C"
        return data


PLACEHOLDERS: tuple[Placeholder, ...] = (
    Placeholder("ph_01_navy_parka", -9.0, "2025-01-10T08:00:00+09:00", "parka", "knit", "jeans", ("navy", "grey"), "comfortable"),
    Placeholder("ph_02_wool_coat", 1.0, "2025-02-03T08:30:00+09:00", "coat", "turtleneck", "slacks", ("camel", "black")),
    Placeholder("ph_03_cream_sweater", 8.0, "2025-11-20T08:30:00+09:00", "none", "knit", "slacks", ("cream", "brown"), "comfortable"),
    Placeholder("ph_04_varsity_jacket", 9.0, "2025-11-05T09:00:00+09:00", "varsity_jacket", "hoodie", "jeans", ("green", "white", "blue"), "comfortable"),
    Placeholder("ph_05_sage_sweater", 9.0, "2025-03-12T09:00:00+09:00", "none", "knit", "jeans", ("sage", "blue"), "cold"),
    Placeholder("ph_06_beige_trench", 14.0, "2025-04-08T08:00:00+09:00", "trench_coat", "shirt", "slacks", ("beige", "white", "navy")),
    Placeholder("ph_07_oxford_shirt", 19.0, "2025-05-20T08:30:00+09:00", "none", "shirt", "chinos", ("blue", "tan"), "hot"),
    Placeholder("ph_08_linen_tee", 25.0, "2025-07-15T08:00:00+09:00", "none", "t_shirt", "trousers", ("white", "ivory")),
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


def render_png(p: Placeholder) -> bytes:
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
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def confirmed_photo_count(manifest_path: Path) -> int:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    entries = data.get("photos", []) if isinstance(data, dict) else []
    return sum(1 for e in entries if isinstance(e, dict) and e.get("rights_confirmed") is True)


def generate(dest_dir: Path, vision_fixture: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    tags_by_hash: dict[str, object] = {}
    for p in PLACEHOLDERS:
        png = render_png(p)
        (dest_dir / f"{p.name}.png").write_bytes(png)
        written.append(f"{p.name}.png")
        sidecar = dest_dir / f"{p.name}.metadata.json"
        if not sidecar.exists():
            sidecar.write_text(json.dumps(p.sidecar(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written.append(sidecar.name)
        tags_by_hash[hashlib.sha256(png).hexdigest()] = {
            "photo_type": "outfit",
            "photo_type_confidence": 1.0,
            "reason": "A placeholder outfit is the main subject.",
            "tags": p.tags(),
        }

    existing: dict[str, object] = {}
    if vision_fixture.is_file():
        loaded = json.loads(vision_fixture.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded
    existing.update(tags_by_hash)
    vision_fixture.parent.mkdir(parents=True, exist_ok=True)
    vision_fixture.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--vision-fixture", type=Path, default=DEFAULT_VISION_FIXTURE)
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

    written = generate(args.dest, args.vision_fixture)
    print(f"wrote {len(written)} files to {args.dest} and updated {args.vision_fixture}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
