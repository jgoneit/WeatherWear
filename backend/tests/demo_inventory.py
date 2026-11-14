"""The full private album is optional; generated demo assets ship with the repo."""

import json
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / "data" / "demo"


def full_demo_images_present() -> bool:
    manifest = json.loads((DEMO / "photos.manifest.json").read_text(encoding="utf-8"))
    return all(
        (DEMO / "photos" / entry["file"]).is_file()
        for entry in manifest["photos"] if entry.get("rights_confirmed") is True
    )
