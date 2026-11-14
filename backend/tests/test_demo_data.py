"""Demo data checks (task 19.23, design.md "데모 데이터 파일", Req 15.2, 15.3,
15.9~15.13).

The demo images in ``data/demo/photos`` are git-ignored (copied by
``scripts/prepare_demo_photos.py``); the sidecars and fixtures are tracked.
Checks that need the image bytes skip when the images are absent. The
scenario rule check reads only tracked files, so it runs everywhere: the file
to SHA-256 mapping comes from ``vision.sources.json``, and
``test_demo_data_layout`` checks that mapping against the current bytes.

``ex_img/`` is git-ignored as well, so the comparison with
``ex_img/clothing_tags.json`` is its own test and skips on its own.

The other modules that use the demo data import their helpers from here.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.demo_scenarios import (
    S1_ID,
    OutfitPhoto,
    ScenarioTemps,
    assign_seed_votes,
    build_scenarios_document,
    check_scenarios,
    choose_scenarios,
    outfit_temperatures,
    v2_photos,
)
from app.domain import UNKNOWN_TAGS, CaptureMetadata, ClothingTags
from app.ingestion.image_check import SUPPORTED_EXTENSIONS, list_image_files
from app.ingestion.metadata import read_embedded_metadata
from app.ingestion.sidecar import SIDECAR_SUFFIX, SidecarError, parse_sidecar_text, sidecar_path_for
from app.providers.weather.fixture import FixtureWeatherProvider
from app.recommendation.rule_based_v1 import is_non_outfit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "data" / "demo"
DEMO_PHOTO_DIR = DEMO_DIR / "photos"
FIXTURES_DIR = DEMO_DIR / "fixtures"
MANIFEST_PATH = DEMO_DIR / "photos.manifest.json"
SCENARIOS_PATH = DEMO_DIR / "scenarios.json"
EXPECTATIONS_PATH = DEMO_DIR / "scenario_expectations.json"
VISION_PATH = FIXTURES_DIR / "vision.json"
VISION_SOURCES_PATH = FIXTURES_DIR / "vision.sources.json"
WEATHER_PATH = FIXTURES_DIR / "weather.json"
CLOTHING_TAGS_PATH = REPO_ROOT / "ex_img" / "clothing_tags.json"

# Req 15.2: 174 photos = PNG 134 + JPG 40. Req 15.13: 20 of the JPGs are Non_Outfit_Photo.
DEMO_PHOTO_COUNT = 174
DEMO_PHOTO_COUNT_BY_SUFFIX = {".png": 134, ".jpg": 40}
NON_OUTFIT_PHOTO_COUNT = 20
# Seed vote sidecars (design "사이드카"): the vote plus a free-text note the parser ignores.
SIDECAR_KEYS = {"thermal_vote", "note"}
THERMAL_VOTES = ("cold", "comfortable", "hot")
TODAY_DEFAULT_KEYS = (
    "actual_temperature_c", "apparent_temperature_c", "relative_humidity_pct",
    "wind_speed_kmh", "precipitation_mm",
)


# --- Helpers (also used by test_integration.py) ------------------------------


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def confirmed_demo_photos() -> list[str]:
    """File names the manifest lists with ``rights_confirmed: true`` (Req 15.3)."""
    return [p["file"] for p in read_json(MANIFEST_PATH)["photos"] if p.get("rights_confirmed") is True]


def demo_images_present() -> bool:
    return DEMO_PHOTO_DIR.is_dir() and any(
        p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS for p in DEMO_PHOTO_DIR.iterdir()
    )


requires_demo_photos = pytest.mark.skipif(
    not demo_images_present(),
    reason=(
        "data/demo/photos has no images (they are git-ignored); "
        "run scripts/prepare_demo_photos.py to copy the photos listed in photos.manifest.json"
    ),
)


@dataclass(frozen=True)
class DemoRecord:
    """One demo photo as the tracked data files describe it (no image bytes needed)."""

    file: str
    sha256: str  # vision.json key, from vision.sources.json
    tags: ClothingTags
    entry: dict[str, Any]  # its weather.json entry

    @property
    def temperature_c(self) -> float:
        return self.entry["actual_temperature_c"]

    def outfit_photo(self) -> OutfitPhoto:
        return OutfitPhoto(self.file, self.sha256, self.temperature_c, self.tags)


def load_demo_records() -> list[DemoRecord]:
    """Every ``weather.json`` entry joined with its hash and tags, in file name order."""
    vision = read_json(VISION_PATH)
    sha_by_file = {source["file"]: sha for sha, source in read_json(VISION_SOURCES_PATH).items()}
    records = []
    for entry in sorted(read_json(WEATHER_PATH)["entries"], key=lambda e: e["file"]):
        sha = sha_by_file.get(entry["file"])
        assert sha is not None, f"{entry['file']} is not in {VISION_SOURCES_PATH.name}"
        assert sha in vision, f"{entry['file']}: no tags in {VISION_PATH.name}"
        records.append(DemoRecord(entry["file"], sha, ClothingTags.model_validate(vision[sha]), entry))
    return records


def demo_outfit_photos() -> list[OutfitPhoto]:
    """All demo photos as the seed vote rules see them (Non_Outfit_Photo included)."""
    return [record.outfit_photo() for record in load_demo_records()]


def non_outfit_files(records: list[DemoRecord]) -> set[str]:
    return {r.file for r in records if is_non_outfit(r.tags)}


def sidecar_texts(records: list[DemoRecord]) -> dict[str, str]:
    """Raw text of every ``*.metadata.json`` keyed by its photo's file name."""
    photo_by_sidecar = {sidecar_path_for(Path(r.file)).name: r.file for r in records}
    texts: dict[str, str] = {}
    orphans = []
    for sidecar in sorted(DEMO_PHOTO_DIR.glob(f"*{SIDECAR_SUFFIX}")):
        photo = photo_by_sidecar.get(sidecar.name)
        if photo is None:
            orphans.append(sidecar.name)
            continue
        texts[photo] = sidecar.read_text(encoding="utf-8")
    assert orphans == [], "sidecars without a demo photo"
    return texts


def sidecar_votes(records: list[DemoRecord]) -> dict[str, str]:
    """Seed vote of each photo that has a sidecar, keyed by file name."""
    return {file: json.loads(text)["thermal_vote"] for file, text in sidecar_texts(records).items()}


def scenario_temperatures() -> dict[str, float]:
    return {s["id"]: s["actual_temperature_c"] for s in read_json(SCENARIOS_PATH)["scenarios"]}


def scenario_expectations() -> dict[str, dict[str, Any]]:
    """``scenario_expectations.json`` scenarios keyed by id, in file order."""
    return {s["id"]: s for s in read_json(EXPECTATIONS_PATH)["scenarios"]}


# --- Tests -------------------------------------------------------------------


@requires_demo_photos
def test_demo_data_layout() -> None:
    """Photos, embedded metadata, fixture keys, recorded weather and sidecars agree."""
    # Req 15.2, 15.3: exactly the confirmed photos, 174 = PNG 134 + JPG 40.
    images = list_image_files(DEMO_PHOTO_DIR)
    names = [image.name for image in images]
    assert names == sorted(confirmed_demo_photos())
    assert len(names) == DEMO_PHOTO_COUNT
    assert Counter(image.suffix.lower() for image in images) == DEMO_PHOTO_COUNT_BY_SUFFIX

    weather = read_json(WEATHER_PATH)
    entries = {entry["file"]: entry for entry in weather["entries"]}
    provider = FixtureWeatherProvider(WEATHER_PATH)

    sha_by_file: dict[str, str] = {}
    problems: list[str] = []
    for image in images:
        data = image.read_bytes()
        sha_by_file[image.name] = hashlib.sha256(data).hexdigest()
        meta = read_embedded_metadata(data)
        if not isinstance(meta, CaptureMetadata):
            problems.append(f"{image.name}: no valid embedded metadata ({meta!r})")
            continue
        entry = entries.get(image.name)
        if entry is None:
            problems.append(f"{image.name}: no entry in weather.json")
            continue
        if datetime.fromisoformat(entry["captured_at"]) != meta.captured_at:
            problems.append(f"{image.name}: weather entry captured_at differs from embedded metadata")
        # The provider finds the recorded entry from the embedded time and place
        # (not today_default or the seasonal formula).
        reading = provider.get_weather(meta.captured_at, meta.latitude, meta.longitude)
        if reading.actual_temperature_c != entry["actual_temperature_c"]:
            problems.append(f"{image.name}: provider gives {reading.actual_temperature_c}, "
                            f"entry has {entry['actual_temperature_c']}")
    assert problems == []

    # vision.json keys are the SHA-256 of the current bytes (after embedding metadata).
    vision = read_json(VISION_PATH)
    assert len(set(sha_by_file.values())) == len(sha_by_file), "two demo photos have the same bytes"
    assert set(vision) == set(sha_by_file.values())
    assert {s["file"]: sha for sha, s in read_json(VISION_SOURCES_PATH).items()} == sha_by_file
    for tags in vision.values():
        ClothingTags.model_validate(tags)

    # weather.json: recorded provenance block and one entry per photo.
    assert isinstance(weather.get("recorded"), dict) and weather["recorded"].get("source")
    assert provider.recorded == weather["recorded"]
    assert len(weather["entries"]) == DEMO_PHOTO_COUNT
    assert sorted(entries) == names  # no duplicate or unknown file
    assert provider.entry_count == DEMO_PHOTO_COUNT

    # Req 15.13: the Non_Outfit_Photo are 20 JPGs.
    records = load_demo_records()
    non_outfit = non_outfit_files(records)
    assert len(non_outfit) == NON_OUTFIT_PHOTO_COUNT
    assert all(name.endswith(".jpg") for name in non_outfit)

    # Req 15.2, 15.11: sidecars hold only the seed vote; Non_Outfit_Photo have none.
    texts = sidecar_texts(records)
    assert texts, "no seed vote sidecars"
    assert sorted(set(texts) & non_outfit) == []
    for file, text in texts.items():
        document = json.loads(text)
        assert set(document) <= SIDECAR_KEYS, f"{file}: {sorted(document)}"
        assert document.get("thermal_vote") in THERMAL_VOTES, file
        parsed = parse_sidecar_text(text, require_location=False)
        assert not isinstance(parsed, SidecarError), f"{file}: {parsed}"
        assert parsed.thermal_vote == document["thermal_vote"]


@pytest.mark.skipif(
    not CLOTHING_TAGS_PATH.is_file(),
    reason="ex_img/clothing_tags.json is absent (ex_img/ is git-ignored)",
)
def test_demo_data_layout_clothing_tags() -> None:
    """vision.json holds the tags of ``ex_img/clothing_tags.json`` (Req 15.12).

    The file to hash mapping is ``vision.sources.json``, which
    ``test_demo_data_layout`` checks against the current photo bytes.
    """
    source = read_json(CLOTHING_TAGS_PATH)
    assert source["schema"] == "WeatherWear ClothingTags"
    images: dict[str, dict[str, Any]] = source["images"]
    assert source["image_count"] == len(images) == DEMO_PHOTO_COUNT

    vision = read_json(VISION_PATH)
    sources = read_json(VISION_SOURCES_PATH)
    sha_by_file = {s["file"]: sha for sha, s in sources.items()}
    assert sorted(images) == sorted(sha_by_file) == sorted(confirmed_demo_photos())

    mismatched = [
        file for file, item in images.items()
        if ClothingTags.model_validate(vision[sha_by_file[file]]) != ClothingTags.model_validate(item["tags"])
    ]
    assert mismatched == []
    # The hash in clothing_tags.json is of the bytes before metadata was embedded.
    assert {file: sources[sha]["source_sha256"] for file, sha in sha_by_file.items()} == {
        file: item["sha256"] for file, item in images.items()
    }
    # design: exactly the Non_Outfit_Photo carry UNKNOWN_TAGS in clothing_tags.json.
    unknown = {file for file, item in images.items() if ClothingTags.model_validate(item["tags"]) == UNKNOWN_TAGS}
    assert unknown == non_outfit_files(load_demo_records())


def test_demo_scenarios_satisfy_rules() -> None:
    """``check_scenarios`` finds no S1~S4 or V1~V3 violation in the real data (Req 15.10)."""
    records = load_demo_records()
    photos = [record.outfit_photo() for record in records]
    temps = outfit_temperatures(photos)
    scenarios = scenario_temperatures()
    votes = sidecar_votes(records)

    assert check_scenarios(scenarios, temps, photos=photos, votes=votes) == []

    # The files hold what the rules choose from this data (Req 15.9, 15.11).
    chosen = choose_scenarios(temps)
    assert isinstance(chosen, ScenarioTemps), chosen
    assert read_json(SCENARIOS_PATH) == build_scenarios_document(chosen)
    assert votes == {file: seed.vote for file, seed in assign_seed_votes(photos, chosen).items()}

    # Req 15.11: among the S1 candidates within 3.0°C, at least one comfortable
    # and at least one cold or hot vote.
    s1_votes = {votes.get(photo.file) for photo in v2_photos(photos, scenarios[S1_ID])}
    assert "comfortable" in s1_votes
    assert s1_votes & {"cold", "hot"}

    # today_default (fixture mode, no scenario selected) is the S1 scenario.
    s1 = next(s for s in read_json(SCENARIOS_PATH)["scenarios"] if s["id"] == S1_ID)
    assert read_json(WEATHER_PATH)["today_default"] == {key: s1[key] for key in TODAY_DEFAULT_KEYS}
