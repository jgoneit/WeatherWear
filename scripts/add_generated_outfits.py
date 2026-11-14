#!/usr/bin/env python3
"""Merge the generated extreme-weather demo supplement without rebuilding originals.

Generated images are tracked in data/demo/photos. Personal original photos
are distributed separately. The catalog records their hashes, generation prompts and synthetic data.
Run after restoring the images; --export-to also copies these 15 PNGs to ex_img.
No model or weather API is called. Existing unrelated fixtures are preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export-to", type=Path, help="also copy PNGs to this existing folder")
    args = parser.parse_args()
    photos = read(DEMO / "generated_outfits.json")["photos"]
    # Validate all assets before touching fixtures or sidecars.
    for entry in photos:
        path = DEMO / "photos" / entry["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise SystemExit(f"Generated image hash mismatch: {path}")
    if args.export_to:
        if not args.export_to.is_dir():
            raise SystemExit(f"Export folder does not exist: {args.export_to}")
        for entry in photos:
            target = args.export_to / entry["file"]
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise SystemExit(f"Refusing to overwrite a different image: {target}")
        for entry in photos:
            target = args.export_to / entry["file"]
            if not target.exists():
                shutil.copy2(DEMO / "photos" / entry["file"], target)

    vision_path = DEMO / "fixtures" / "vision.json"
    weather_path = DEMO / "fixtures" / "weather.json"
    manifest_path = DEMO / "photos.manifest.json"
    vision, weather, manifest = read(vision_path), read(weather_path), read(manifest_path)
    generated_names = {entry["file"] for entry in photos}
    weather["entries"] = [e for e in weather["entries"] if e["file"] not in generated_names]
    manifest["photos"] = [e for e in manifest["photos"] if e["file"] not in generated_names]
    for entry in photos:
        sidecar = DEMO / "photos" / (Path(entry["file"]).stem + ".metadata.json")
        # Preserve user feedback and any unrelated metadata on reruns.
        metadata = read(sidecar) if sidecar.exists() else {}
        metadata.update(entry["metadata"])
        write(sidecar, metadata)
        vision[entry["sha256"]] = entry["vision"]
        weather["entries"].append({
            "file": entry["file"],
            **{key: entry["metadata"][key] for key in ("captured_at", "latitude", "longitude")},
            **entry["weather"],
        })
        manifest["photos"].append({
            "file": entry["file"], "origin": "ex_img/" + entry["file"],
            "sha256": entry["sha256"],
            "source": "OpenAI image_gen · 사용자 요청으로 생성한 데모 착장",
            "license": "프로젝트 데모용 AI 생성 이미지. 실존 인물의 촬영 사진이 아님.",
            "rights_confirmed": True,
        })
    write(vision_path, vision)
    write(weather_path, weather)
    write(manifest_path, manifest)
    print(f"Merged {len(photos)} generated outfits with synthetic capture/weather fixtures.")


if __name__ == "__main__":
    main()
