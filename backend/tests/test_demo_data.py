"""Demo data checks (task 19.23, design.md "데모 데이터 파일", Req 15.2, 15.3,
15.9~15.13).

The personal demo images in ``data/demo/photos`` are git-ignored (copied by
``scripts/prepare_demo_photos.py``); the 15 AI-generated outfits of
``data/demo/generated_outfits.json`` (141~155), the sidecars and the fixtures
are tracked. Checks that need the image bytes skip unless the whole album
listed in the manifest is present (``tests.demo_inventory``). The
scenario rule check reads only tracked files, so it runs everywhere: the file
to SHA-256 mapping comes from ``vision.sources.json``, and
``test_demo_data_layout`` checks that mapping against the current bytes.

``ex_img/`` is git-ignored as well, so the comparison with
``ex_img/clothing_tags.json`` is its own test and skips on its own.

``vision.json`` holds one ``VisionAnalysis`` per photo. A scan stores only the
photos whose analysis is ``outfit`` after the ingestion confidence gate;
``non_outfit`` and ``uncertain`` photos are reported and never stored. The scan
counts the other tests expect (``first_scan_counts``, ``rescan_counts``) are
derived from that, not written as literals.

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
from app.domain import (
    UNKNOWN_TAGS,
    CaptureMetadata,
    ClothingTags,
    VisionAnalysis,
    normalize_lower_type,
    normalize_outer_type,
    normalize_upper_type,
)
from app.ingestion.image_check import list_image_files
from app.ingestion.metadata import ScanFailure, read_embedded_metadata, resolve_capture_metadata
from app.ingestion.service import apply_confidence_gate
from app.ingestion.sidecar import SIDECAR_SUFFIX, SidecarError, parse_sidecar_text, read_sidecar, sidecar_path_for
from app.providers.weather.fixture import FixtureWeatherProvider
from tests.demo_inventory import full_demo_images_present

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
GENERATED_PATH = DEMO_DIR / "generated_outfits.json"
CLOTHING_TAGS_PATH = REPO_ROOT / "ex_img" / "clothing_tags.json"

# Req 15.2: 189 photos = PNG 149 + JPG 40: the 174 of ex_img (159 + 126~140) and
# the 15 generated outfits (141~155). Req 15.13: 20 of the JPGs are
# Non_Outfit_Photo (vision.json photo_type "non_outfit"); every other photo is "outfit".
DEMO_PHOTO_COUNT = 189
DEMO_PHOTO_COUNT_BY_SUFFIX = {".png": 149, ".jpg": 40}
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
    """Every photo of the manifest is present (the generated 15 alone are not enough)."""
    return full_demo_images_present()


requires_demo_photos = pytest.mark.skipif(
    not demo_images_present(),
    reason=(
        "The complete private demo album is not installed; "
        "run scripts/prepare_demo_photos.py to copy the demo photos"
    ),
)


def generated_catalog() -> dict[str, dict[str, Any]]:
    """``generated_outfits.json`` entries (the bundled AI-generated outfits) by file name."""
    return {entry["file"]: entry for entry in read_json(GENERATED_PATH)["photos"]}


def capture_of(image: Path, data: bytes) -> CaptureMetadata:
    """Capture time/location as the ingestion service resolves it (EXIF, then sidecar)."""
    embedded = read_embedded_metadata(data)
    sidecar = read_sidecar(image, require_location=not isinstance(embedded, CaptureMetadata))
    resolved = resolve_capture_metadata(embedded, sidecar)
    assert not isinstance(resolved, ScanFailure), f"{image.name}: {resolved}"
    return resolved[0]


@dataclass(frozen=True)
class DemoRecord:
    """One demo photo as the tracked data files describe it (no image bytes needed)."""

    file: str
    sha256: str  # vision.json key, from vision.sources.json
    analysis: VisionAnalysis
    entry: dict[str, Any]  # its weather.json entry

    @property
    def temperature_c(self) -> float:
        return self.entry["actual_temperature_c"]

    @property
    def scan_photo_type(self) -> str:
        """What a scan does with the photo: ``outfit`` is stored, the others are reported."""
        return apply_confidence_gate(self.analysis).photo_type

    def outfit_photo(self) -> OutfitPhoto:
        return OutfitPhoto(self.file, self.sha256, self.temperature_c, self.analysis)


def load_demo_records() -> list[DemoRecord]:
    """Every ``weather.json`` entry joined with its hash and vision analysis, in file name order."""
    vision = read_json(VISION_PATH)
    sha_by_file = {source["file"]: sha for sha, source in read_json(VISION_SOURCES_PATH).items()}
    records = []
    for entry in sorted(read_json(WEATHER_PATH)["entries"], key=lambda e: e["file"]):
        sha = sha_by_file.get(entry["file"])
        assert sha is not None, f"{entry['file']} is not in {VISION_SOURCES_PATH.name}"
        assert sha in vision, f"{entry['file']}: no entry in {VISION_PATH.name}"
        records.append(DemoRecord(entry["file"], sha, VisionAnalysis.model_validate(vision[sha]), entry))
    return records


def demo_outfit_photos() -> list[OutfitPhoto]:
    """All demo photos as the seed vote rules see them (Non_Outfit_Photo included)."""
    return [record.outfit_photo() for record in load_demo_records()]


def non_outfit_files(records: list[DemoRecord]) -> set[str]:
    """Photos whose fixture ``photo_type`` is ``non_outfit`` (Req 15.13)."""
    return {r.file for r in records if r.analysis.photo_type == "non_outfit"}


def not_stored_files(records: list[DemoRecord]) -> set[str]:
    """Photos a scan reports as non_outfit or uncertain instead of storing them."""
    return {r.file for r in records if r.scan_photo_type != "outfit"}


def first_scan_counts() -> dict[str, int]:
    """``ScanReport`` counts of the first fixture-mode scan of ``data/demo/photos``."""
    types = Counter(record.scan_photo_type for record in load_demo_records())
    return {
        "scanned": sum(types.values()),
        "succeeded": types["outfit"],
        "duplicate": 0,
        "skipped_non_outfit": types["non_outfit"],
        "uncertain": types["uncertain"],
        "failed": 0,
    }


def rescan_counts() -> dict[str, int]:
    """Counts of a second scan: stored outfits are duplicates, the others are reported again."""
    first = first_scan_counts()
    return {**first, "succeeded": 0, "duplicate": first["succeeded"]}


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
    """Seed vote of each photo whose sidecar has one, keyed by file name."""
    documents = {file: json.loads(text) for file, text in sidecar_texts(records).items()}
    return {file: doc["thermal_vote"] for file, doc in documents.items() if doc.get("thermal_vote") is not None}


def scenario_temperatures() -> dict[str, float]:
    return {s["id"]: s["actual_temperature_c"] for s in read_json(SCENARIOS_PATH)["scenarios"]}


def scenario_expectations() -> dict[str, dict[str, Any]]:
    """``scenario_expectations.json`` scenarios keyed by id, in file order."""
    return {s["id"]: s for s in read_json(EXPECTATIONS_PATH)["scenarios"]}


# --- Tests -------------------------------------------------------------------


@requires_demo_photos
def test_demo_data_layout() -> None:
    """Photos, capture metadata, fixture keys, weather entries and sidecars agree."""
    # Req 15.2, 15.3: exactly the confirmed photos, 189 = PNG 149 + JPG 40.
    images = list_image_files(DEMO_PHOTO_DIR)
    names = [image.name for image in images]
    assert names == sorted(confirmed_demo_photos())
    assert len(names) == DEMO_PHOTO_COUNT
    assert Counter(image.suffix.lower() for image in images) == DEMO_PHOTO_COUNT_BY_SUFFIX

    weather = read_json(WEATHER_PATH)
    entries = {entry["file"]: entry for entry in weather["entries"]}
    provider = FixtureWeatherProvider(WEATHER_PATH)

    generated = generated_catalog()
    sha_by_file: dict[str, str] = {}
    problems: list[str] = []
    for image in images:
        data = image.read_bytes()
        sha_by_file[image.name] = hashlib.sha256(data).hexdigest()
        # The ex_img photos carry embedded metadata; the generated outfits have
        # no EXIF and use the sidecar fallback (their pinned hashes forbid
        # rewriting the bytes).
        embedded = read_embedded_metadata(data)
        if (image.name in generated) == isinstance(embedded, CaptureMetadata):
            problems.append(f"{image.name}: embedded metadata {embedded!r} (generated: {image.name in generated})")
            continue
        meta = capture_of(image, data)
        entry = entries.get(image.name)
        if entry is None:
            problems.append(f"{image.name}: no entry in weather.json")
            continue
        if datetime.fromisoformat(entry["captured_at"]) != meta.captured_at:
            problems.append(f"{image.name}: weather entry captured_at differs from the capture metadata")
        # The provider finds the entry from the capture time and place
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
    for analysis in vision.values():
        VisionAnalysis.model_validate(analysis)
    # The generated outfits keep the analysis the teammate authored in the catalog.
    assert {file: vision[sha_by_file[file]] for file in generated} == {
        file: entry["vision"] for file, entry in generated.items()
    }
    assert {file: sha_by_file[file] for file in generated} == {
        file: entry["sha256"] for file, entry in generated.items()
    }

    # weather.json: recorded provenance block and one entry per photo.
    assert isinstance(weather.get("recorded"), dict) and weather["recorded"].get("source")
    assert provider.recorded == weather["recorded"]
    assert len(weather["entries"]) == DEMO_PHOTO_COUNT
    assert sorted(entries) == names  # no duplicate or unknown file
    assert provider.entry_count == DEMO_PHOTO_COUNT
    # Entries of the generated outfits are authored, not recorded: marked
    # synthetic and holding the catalog values unchanged. Every other entry is
    # recorded (it has the recorded observation hour).
    synthetic_source = weather["recorded"]["synthetic_entries"]["source"]
    assert synthetic_source == GENERATED_PATH.relative_to(REPO_ROOT).as_posix()
    assert {file for file, entry in entries.items() if entry.get("synthetic")} == set(generated)
    assert provider.synthetic_entry_count == len(generated)
    for file, item in generated.items():
        assert entries[file] == {
            "file": file,
            **{key: item["metadata"][key] for key in ("captured_at", "latitude", "longitude")},
            **item["weather"],
            "synthetic": True,
            "source": synthetic_source,
        }
        meta = capture_of(DEMO_PHOTO_DIR / file, (DEMO_PHOTO_DIR / file).read_bytes())
        assert provider.is_synthetic(meta.captured_at, meta.latitude, meta.longitude)
    assert all(entry.get("observed_at") for file, entry in entries.items() if file not in generated)

    # Req 15.13: the Non_Outfit_Photo are 20 JPGs labeled non_outfit; every other
    # photo is an outfit the scan stores (none falls to uncertain at the gate).
    records = load_demo_records()
    non_outfit = non_outfit_files(records)
    assert len(non_outfit) == NON_OUTFIT_PHOTO_COUNT
    assert all(name.endswith(".jpg") for name in non_outfit)
    assert all(r.analysis.tags == UNKNOWN_TAGS for r in records if r.file in non_outfit)
    assert Counter(r.scan_photo_type for r in records) == {
        "outfit": DEMO_PHOTO_COUNT - NON_OUTFIT_PHOTO_COUNT,
        "non_outfit": NON_OUTFIT_PHOTO_COUNT,
    }
    assert not_stored_files(records) == non_outfit

    # Req 15.2, 15.11: an ex_img photo's sidecar holds only the seed vote; a
    # generated outfit's sidecar is its capture source and keeps the catalog
    # metadata (plus a seed vote when it has one); photos that are not stored
    # have no sidecar.
    texts = sidecar_texts(records)
    assert texts, "no seed vote sidecars"
    assert sorted(set(texts) & not_stored_files(records)) == []
    assert set(generated) <= set(texts)
    for file, text in texts.items():
        document = json.loads(text)
        if file in generated:
            assert {k: v for k, v in document.items() if k not in SIDECAR_KEYS} == generated[file]["metadata"]
        else:
            assert set(document) <= SIDECAR_KEYS, f"{file}: {sorted(document)}"
            assert document.get("thermal_vote") in THERMAL_VOTES, file
        parsed = parse_sidecar_text(text, require_location=file in generated)
        assert not isinstance(parsed, SidecarError), f"{file}: {parsed}"
        assert parsed.thermal_vote == document.get("thermal_vote")


@pytest.mark.skipif(
    not CLOTHING_TAGS_PATH.is_file(),
    reason="ex_img/clothing_tags.json is absent (ex_img/ is git-ignored)",
)
def test_demo_data_layout_clothing_tags() -> None:
    """vision.json holds the tags of ``ex_img/clothing_tags.json`` (Req 15.12).

    The source spells garments freely (``"t-shirt"``); vision.json holds the
    canonical taxonomy values, so the source is compared after
    ``normalize_*_type``. The file to hash mapping is ``vision.sources.json``,
    which ``test_demo_data_layout`` checks against the current photo bytes.
    """

    def canonical(raw: dict[str, Any]) -> ClothingTags:
        return ClothingTags.model_validate({
            **raw,
            "upper_type": normalize_upper_type(raw["upper_type"]),
            "outer_type": normalize_outer_type(raw["outer_type"]),
            "lower_type": normalize_lower_type(raw["lower_type"]),
        })

    source = read_json(CLOTHING_TAGS_PATH)
    assert source["schema"] == "WeatherWear ClothingTags"
    images: dict[str, dict[str, Any]] = source["images"]
    generated = generated_catalog()
    # clothing_tags.json covers the ex_img photos; the generated outfits are analysed in their catalog.
    assert source["image_count"] == len(images) == DEMO_PHOTO_COUNT - len(generated)

    vision = read_json(VISION_PATH)
    sources = read_json(VISION_SOURCES_PATH)
    sha_by_file = {s["file"]: sha for sha, s in sources.items()}
    assert sorted(sha_by_file) == sorted(confirmed_demo_photos())
    assert sorted(images) == sorted(set(sha_by_file) - set(generated))
    assert {sources[sha_by_file[file]]["source_sha256"] for file in generated} == {None}

    mismatched = [
        file for file, item in images.items()
        if VisionAnalysis.model_validate(vision[sha_by_file[file]]).tags != canonical(item["tags"])
    ]
    assert mismatched == []
    # The hash in clothing_tags.json is of the bytes before metadata was embedded.
    assert {file: sources[sha]["source_sha256"] for file, sha in sha_by_file.items() if file in images} == {
        file: item["sha256"] for file, item in images.items()
    }
    # design: exactly the photos labeled non_outfit carry UNKNOWN_TAGS in clothing_tags.json.
    unknown = {file for file, item in images.items() if canonical(item["tags"]) == UNKNOWN_TAGS}
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
