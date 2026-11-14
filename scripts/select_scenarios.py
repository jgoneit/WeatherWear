#!/usr/bin/env python3
"""Choose the demo weather scenarios and seed votes from the recorded weather.

Design "시나리오 규칙과 선택 알고리즘" and "시드 투표 규칙" (Req 2.6, 5.2, 15.9,
15.10, 15.11). The rules live in ``backend/app/demo_scenarios.py``; this script
reads the data, applies them and writes:

- ``data/demo/scenarios.json``: the four scenarios. Only
  ``actual_temperature_c`` is chosen (one decimal place); ids, names and the
  other values are fixed. S2 is renamed when it sits above the warmest photo.
- ``data/demo/fixtures/weather.json``: only ``today_default`` changes. It gets
  the S1 values, so fixture mode without a selected scenario shows the S1 case.
- Seed vote sidecars in ``data/demo/photos``. Capture time and location are
  resolved exactly as the ingestion service does: embedded metadata first,
  the sidecar as the fallback source (``resolve_capture_metadata``). A photo
  whose capture comes from its sidecar (the bundled generated outfits, which
  have no EXIF) keeps every non-vote key of that sidecar; the vote and its
  note are added or removed. Every other ``*.metadata.json`` is deleted and
  ``{"thermal_vote": ..., "note": ...}`` is written next to each photo that has
  a vote. The script refuses to run while any photo has no capture
  time/location from either source.
- ``data/demo/scenario_expectations.json``: the result of
  ``RuleBasedTemperatureStrategy`` for each scenario on the recorded data.
  Only the photos a scan stores as Outfits are candidates (fixture
  ``photo_type`` ``outfit`` after the ingestion confidence gate; non-outfit and
  uncertain photos are reported by the scan and never stored). Outfit ids
  follow the scan order (file name order among the stored photos), so ties on
  equal score and weather resolve the same way as after a real scan; items are
  compared by file name.

Before writing, ``check_scenarios`` and the expected outcome of each scenario
are checked. Any violation is printed, nothing is written and the exit code
is 1. ``--check`` writes nothing and checks the current files instead (and
that they match what the script would write).

Inputs are local files only (no network): photos, ``weather.json``
(``scripts/record_fixture_weather.py``) and ``vision.json``
(``scripts/import_clothing_tags.py``).

Usage: backend/.venv/bin/python scripts/select_scenarios.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.demo_scenarios import (  # noqa: E402
    DEFAULT_TOLERANCE_C,
    FALLBACK_TOLERANCE_C,
    S1_ID,
    S2_ID,
    S3_ID,
    S4_ID,
    SCENARIO_IDS,
    Infeasible,
    OutfitPhoto,
    SeedVote,
    Violation,
    assign_seed_votes,
    build_scenarios_document,
    check_scenarios,
    choose_scenarios,
    outfit_temperatures,
    v2_photos,
)
from app.domain import (  # noqa: E402
    ALGORITHM_VERSION,
    CaptureMetadata,
    OutfitWithWeatherAndFeedback,
    ThermalVote,
    VisionAnalysis,
    WeatherContext,
    WeatherReading,
)
from app.ingestion.image_check import list_image_files  # noqa: E402
from app.ingestion.metadata import ScanFailure, read_embedded_metadata, resolve_capture_metadata  # noqa: E402
from app.ingestion.service import apply_confidence_gate  # noqa: E402
from app.ingestion.sidecar import (  # noqa: E402
    SIDECAR_SUFFIX,
    SidecarError,
    parse_sidecar_text,
    read_sidecar,
    sidecar_path_for,
)
from app.providers.weather.fixture import FixtureWeatherProvider  # noqa: E402
from app.recommendation.rule_based_v1 import RuleBasedTemperatureStrategy  # noqa: E402
from app.today_weather import load_scenarios  # noqa: E402

DEMO_DIR = REPO_ROOT / "data" / "demo"
DEFAULT_PHOTOS = DEMO_DIR / "photos"
DEFAULT_WEATHER = DEMO_DIR / "fixtures" / "weather.json"
DEFAULT_VISION = DEMO_DIR / "fixtures" / "vision.json"
DEFAULT_SCENARIOS = DEMO_DIR / "scenarios.json"
DEFAULT_EXPECTATIONS = DEMO_DIR / "scenario_expectations.json"

APP_TIMEZONE = ZoneInfo("Asia/Seoul")
SIDECAR_NOTE = "select_scenarios.py 시드 투표 ({rule}). 촬영 시각·위치는 사진 내장 메타데이터에서 읽는다"
SIDECAR_SOURCE_NOTE = "select_scenarios.py 시드 투표 ({rule}). 촬영 시각·위치는 이 사이드카에서 읽는다(내장 메타데이터 없음)"
VOTE_KEYS = {"thermal_vote", "note"}


@dataclass(frozen=True)
class PhotoData:
    file: str
    sha256: str
    captured_at: datetime
    analysis: VisionAnalysis
    weather: WeatherReading
    # Non-vote keys of the sidecar when it is the capture source, else None.
    sidecar_metadata: dict[str, Any] | None = None

    def outfit_photo(self) -> OutfitPhoto:
        return OutfitPhoto(self.file, self.sha256, self.weather.actual_temperature_c, self.analysis)


@dataclass(frozen=True)
class Paths:
    photos: Path
    weather: Path
    vision: Path
    scenarios: Path
    expectations: Path


# -- reading ---------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_photo_data(paths: Paths) -> tuple[list[PhotoData], list[str]]:
    """Every photo with its hash, capture time, vision analysis and fixture weather.

    The capture comes from the embedded metadata or, when that is missing or
    invalid, from the sidecar, like the ingestion service. The weather is what
    ``FixtureWeatherProvider`` returns for that capture, so recorded and
    authored (``synthetic``) entries are read the same way as in a scan.
    """
    problems: list[str] = []
    try:
        weather = _read_json(paths.weather)
        vision = _read_json(paths.vision)
    except (OSError, ValueError) as exc:
        return [], [f"cannot read fixtures: {type(exc).__name__}: {exc}"]
    entries = {e.get("file"): e for e in weather.get("entries", []) if isinstance(e, dict)}
    provider = FixtureWeatherProvider(paths.weather, tz=APP_TIMEZONE)

    photos: list[PhotoData] = []
    seen: dict[str, str] = {}
    for image in list_image_files(paths.photos):
        data = image.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha in seen:
            problems.append(f"{image.name}: same SHA-256 as {seen[sha]}")
            continue
        seen[sha] = image.name
        embedded = read_embedded_metadata(data)
        sidecar = read_sidecar(image, require_location=not isinstance(embedded, CaptureMetadata))
        resolved = resolve_capture_metadata(embedded, sidecar)
        if isinstance(resolved, ScanFailure):
            problems.append(f"{image.name}: no capture time/location ({resolved.code})")
            continue
        capture, _ = resolved
        sidecar_metadata = None
        if capture.origin == "sidecar":
            raw = _read_json(sidecar_path_for(image))
            sidecar_metadata = {k: v for k, v in raw.items() if k not in VOTE_KEYS}
        entry = entries.get(image.name)
        if entry is None:
            problems.append(f"{image.name}: no entry in {paths.weather.name}")
            continue
        try:
            entry_captured = datetime.fromisoformat(entry["captured_at"])
            entry_place = (float(entry["latitude"]), float(entry["longitude"]))
        except (KeyError, TypeError, ValueError):
            problems.append(f"{image.name}: invalid entry in {paths.weather.name}")
            continue
        if entry_captured != capture.captured_at or entry_place != (capture.latitude, capture.longitude):
            problems.append(f"{image.name}: weather entry time/place differs from the capture metadata")
            continue
        reading = provider.get_weather(capture.captured_at, capture.latitude, capture.longitude)
        if reading.actual_temperature_c != entry.get("actual_temperature_c"):
            problems.append(f"{image.name}: {paths.weather.name} entry is not readable by FixtureWeatherProvider")
            continue
        raw_analysis = vision.get(sha)
        if raw_analysis is None:
            problems.append(f"{image.name}: no entry in {paths.vision.name} for its SHA-256")
            continue
        try:
            analysis = VisionAnalysis.model_validate(raw_analysis)
        except ValidationError:
            problems.append(f"{image.name}: invalid VisionAnalysis in {paths.vision.name}")
            continue
        photos.append(PhotoData(image.name, sha, capture.captured_at, analysis, reading, sidecar_metadata))
    if not photos and not problems:
        problems.append(f"no images in {paths.photos}")
    return photos, problems


def read_sidecars(photo_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, ThermalVote], list[str]]:
    """Current sidecar documents and votes by image file name, plus format problems."""
    problems: list[str] = []
    documents: dict[str, dict[str, Any]] = {}
    votes: dict[str, ThermalVote] = {}
    images = {sidecar_path_for(p).name: p.name for p in list_image_files(photo_dir)}
    for sidecar in sorted(photo_dir.glob(f"*{SIDECAR_SUFFIX}")):
        image = images.get(sidecar.name)
        if image is None:
            problems.append(f"{sidecar.name}: no matching photo")
            continue
        text = sidecar.read_text(encoding="utf-8")
        parsed = parse_sidecar_text(text, require_location=False)
        if isinstance(parsed, SidecarError):
            problems.append(f"{sidecar.name}: {parsed.code}")
            continue
        documents[image] = json.loads(text)
        if parsed.thermal_vote is not None:
            votes[image] = parsed.thermal_vote
    return documents, votes, problems


# -- expected results ------------------------------------------------------------


def _today(scenario: dict[str, Any]) -> WeatherContext:
    return WeatherContext(
        actual_temperature_c=scenario["actual_temperature_c"],
        apparent_temperature_c=scenario.get("apparent_temperature_c"),
        relative_humidity_pct=scenario.get("relative_humidity_pct"),
        wind_speed_kmh=scenario.get("wind_speed_kmh"),
        precipitation_mm=scenario.get("precipitation_mm"),
        observed_at=None,
        provider="scenario",
        is_fixture=True,
        scenario_id=scenario["id"],
        scenario_name=scenario["name"],
    )


def simulate(
    photos: list[PhotoData], votes: dict[str, ThermalVote], scenarios_doc: dict[str, Any]
) -> dict[str, Any]:
    """Run rule_based_v1 for each scenario on the photos a scan stores.

    Outfit id = 1-based position in file name order among the stored photos
    (the order a scan inserts them).
    """
    ordered = sorted((p for p in photos if p.outfit_photo().stored_as_outfit), key=lambda p: p.file)
    by_id = {index: photo for index, photo in enumerate(ordered, start=1)}
    candidates = [
        OutfitWithWeatherAndFeedback(
            outfit_id=index,
            captured_at=photo.captured_at,
            tags=photo.analysis.tags,
            weather=photo.weather,
            effective_vote=votes.get(photo.file),
        )
        for index, photo in by_id.items()
    ]
    strategy = RuleBasedTemperatureStrategy(DEFAULT_TOLERANCE_C)
    results = []
    for scenario in scenarios_doc["scenarios"]:
        outcome = strategy.recommend_with_outcome(_today(scenario), candidates)
        items = [
            {
                "rank": r.rank,
                "file": by_id[r.outfit_id].file,
                "past_temperature_c": by_id[r.outfit_id].weather.actual_temperature_c,
                "temperature_diff_c": r.temperature_diff_c,
                "score": r.score,
                "effective_vote": votes.get(by_id[r.outfit_id].file),
                "confidence_level": r.confidence_level,
                "selected_by_fallback": r.selected_by_fallback,
            }
            for r in outcome.results
        ]
        results.append(
            {
                "id": scenario["id"],
                "name": scenario["name"],
                "actual_temperature_c": scenario["actual_temperature_c"],
                "status": "ok" if items else "insufficient_data",
                "fallback": outcome.fallback or not items,  # same rule as RecommendationService
                "applied_tolerance_c": float(outcome.applied_tolerance_c),
                "count": len(items),
                "top_file": items[0]["file"] if items else None,
                "items": items,
            }
        )
    return {
        "generated_by": "scripts/select_scenarios.py",
        "algorithm_version": ALGORITHM_VERSION,
        "tolerance_c": float(DEFAULT_TOLERANCE_C),
        "note": (
            "fixture 모드로 data/demo/photos 전체를 스캔한 결과의 기대값. 착장으로 저장되는 사진만 후보이고 "
            "착장 아닌 사진(non_outfit)과 uncertain 사진은 저장되지 않는다. 항목은 파일 이름으로 비교한다"
        ),
        "scenarios": results,
    }


def expectation_violations(expectations: dict[str, Any], v2_files: tuple[str, str] | None) -> list[Violation]:
    """Check the "시나리오별 기대 결과" table against simulated results."""
    found = {s["id"]: s for s in expectations["scenarios"]}
    out: list[Violation] = []

    def need(cond: bool, rule: str, sid: str, detail: str) -> None:
        if not cond:
            out.append(Violation(rule, sid, detail))

    s1 = found.get(S1_ID)
    if s1:
        need(s1["status"] == "ok" and s1["count"] == 5 and not s1["fallback"], "S1", S1_ID,
             "expected 5 normal results")
        need(all(i["temperature_diff_c"] <= 3.0 for i in s1["items"]), "S1", S1_ID, "diff above 3.0")
        if v2_files is not None:
            ranks = {i["file"]: i["rank"] for i in s1["items"]}
            cold, comfy = v2_files
            need(comfy in ranks and (cold not in ranks or ranks[comfy] < ranks[cold]), "V2", S1_ID,
                 "V2 comfortable photo is not ranked above the V2 cold photo")
    s2 = found.get(S2_ID)
    if s2:
        need(s2["status"] == "ok" and s2["count"] == 1 and not s2["fallback"], "S2", S2_ID,
             "expected exactly 1 normal result")
        need(all(i["temperature_diff_c"] == 3.0 and i["confidence_level"] == "high" for i in s2["items"]),
             "S2", S2_ID, "expected diff 3.0 and confidence high")
    s3 = found.get(S3_ID)
    if s3:
        need(s3["status"] == "ok" and 1 <= s3["count"] <= 5 and s3["fallback"]
             and s3["applied_tolerance_c"] == float(FALLBACK_TOLERANCE_C), "S3", S3_ID,
             "expected 1~5 fallback results with tolerance 5.0")
        need(all(i["selected_by_fallback"] and 4.0 <= i["temperature_diff_c"] <= 5.0
                 and i["confidence_level"] in ("medium", "low") for i in s3["items"]),
             "S3", S3_ID, "expected fallback items with diff 4.0~5.0")
    s4 = found.get(S4_ID)
    if s4:
        need(s4["status"] == "insufficient_data" and s4["count"] == 0 and s4["fallback"], "S4", S4_ID,
             "expected insufficient_data")
    return out


# -- writing ---------------------------------------------------------------------


def write_json_atomic(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def today_default_for(scenarios_doc: dict[str, Any]) -> dict[str, Any]:
    s1 = next(s for s in scenarios_doc["scenarios"] if s["id"] == S1_ID)
    return {
        key: s1[key]
        for key in ("actual_temperature_c", "apparent_temperature_c", "relative_humidity_pct",
                    "wind_speed_kmh", "precipitation_mm")
    }


def sidecar_documents(photos: list[PhotoData], votes: dict[str, SeedVote]) -> dict[str, dict[str, Any]]:
    """The sidecar each photo should have, by file name (no entry: no sidecar).

    A photo whose capture comes from its sidecar keeps the non-vote keys of
    that sidecar; the seed vote and its note are added when it has one.
    """
    documents: dict[str, dict[str, Any]] = {}
    for photo in sorted(photos, key=lambda p: p.file):
        document = dict(photo.sidecar_metadata or {})
        seed = votes.get(photo.file)
        if seed is not None:
            note = SIDECAR_SOURCE_NOTE if photo.sidecar_metadata is not None else SIDECAR_NOTE
            document.update(thermal_vote=seed.vote, note=note.format(rule=seed.rule))
        if document:
            documents[photo.file] = document
    return documents


def write_sidecars(photo_dir: Path, documents: dict[str, dict[str, Any]]) -> tuple[int, int]:
    """Delete sidecars that should not exist, then write the expected ones. Returns (removed, written)."""
    keep = {sidecar_path_for(photo_dir / name).name for name in documents}
    removed = 0
    for old in photo_dir.glob(f"*{SIDECAR_SUFFIX}"):
        if old.name not in keep:
            old.unlink()
            removed += 1
    for file_name, document in documents.items():
        write_json_atomic(sidecar_path_for(photo_dir / file_name), document)
    return removed, len(documents)


# -- main ------------------------------------------------------------------------


@dataclass
class Plan:
    photos: list[PhotoData]
    scenarios_doc: dict[str, Any]
    seed_votes: dict[str, SeedVote]
    sidecars: dict[str, dict[str, Any]]
    expectations: dict[str, Any]
    violations: list[Violation]


def plan(photos: list[PhotoData]) -> Plan | Infeasible:
    outfit_photos = [p.outfit_photo() for p in photos]
    temps = outfit_temperatures(outfit_photos)
    chosen = choose_scenarios(temps)
    if isinstance(chosen, Infeasible):
        return chosen
    scenarios_doc = build_scenarios_document(chosen)
    seed_votes = assign_seed_votes(outfit_photos, chosen)
    votes = {f: s.vote for f, s in seed_votes.items()}
    expectations = simulate(photos, votes, scenarios_doc)
    ranked = v2_photos(outfit_photos, chosen.chilly_morning)
    v2 = (ranked[0].file, ranked[1].file) if len(ranked) >= 2 else None
    violations = check_scenarios(chosen, temps, photos=outfit_photos, votes=seed_votes)
    violations += expectation_violations(expectations, v2)
    return Plan(photos, scenarios_doc, seed_votes, sidecar_documents(photos, seed_votes), expectations, violations)


def check_current(paths: Paths, photos: list[PhotoData], expected: Plan) -> list[str]:
    """Problems with the files on disk (``--check``)."""
    problems: list[str] = []
    outfit_photos = [p.outfit_photo() for p in photos]
    temps = outfit_temperatures(outfit_photos)

    scenarios, errors = load_scenarios(paths.scenarios)
    problems += [f"{paths.scenarios.name}: {e}" for e in errors]
    current = {s.id: s.actual_temperature_c for s in scenarios if s.id in SCENARIO_IDS}
    documents, votes, sidecar_problems = read_sidecars(paths.photos)
    problems += sidecar_problems
    problems += [_format(v) for v in check_scenarios(current, temps, photos=outfit_photos, votes=votes)]

    try:
        on_disk = _read_json(paths.scenarios)
    except (OSError, ValueError):
        on_disk = None
    if on_disk != expected.scenarios_doc:
        problems.append(f"{paths.scenarios.name} differs from the chosen scenarios")
    if votes != {f: s.vote for f, s in expected.seed_votes.items()}:
        problems.append("sidecar votes differ from the V1~V4 seed votes")
    if documents != expected.sidecars:
        differing = sorted(set(documents) ^ set(expected.sidecars)
                           | {f for f in documents if f in expected.sidecars and documents[f] != expected.sidecars[f]})
        problems.append(f"sidecars differ from the expected documents: {', '.join(differing[:5])}")
    try:
        today_default = _read_json(paths.weather).get("today_default")
    except (OSError, ValueError, AttributeError):
        today_default = None
    if today_default != today_default_for(expected.scenarios_doc):
        problems.append(f"{paths.weather.name}: today_default is not the S1 scenario values")
    try:
        expectations = _read_json(paths.expectations)
    except (OSError, ValueError):
        expectations = None
    if expectations != expected.expectations:
        problems.append(f"{paths.expectations.name} differs from the simulated results")
    return problems


def _format(v: Violation) -> str:
    where = f" [{v.scenario_id}]" if v.scenario_id else ""
    return f"{v.rule}{where}: {v.detail}"


def _summary(result: Plan) -> None:
    for scenario in result.expectations["scenarios"]:
        top = scenario["items"][0] if scenario["items"] else None
        top_text = (
            f"1위 {top['file']} ({top['past_temperature_c']}°C, 차이 {top['temperature_diff_c']}, "
            f"{top['score']}점, {top['confidence_level']})" if top else "결과 없음"
        )
        print(
            f"  {scenario['id']:<15} {scenario['actual_temperature_c']:>6.1f}°C  "
            f"{scenario['status']}, fallback={scenario['fallback']}, "
            f"tolerance={scenario['applied_tolerance_c']}, {scenario['count']}건, {top_text}"
        )
    rules: dict[str, int] = {}
    for seed in result.seed_votes.values():
        rules[seed.rule] = rules.get(seed.rule, 0) + 1
    print(f"  seed votes: {len(result.seed_votes)} ({', '.join(f'{k} {n}' for k, n in sorted(rules.items()))})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="check the current files; write nothing")
    parser.add_argument("--photos", type=Path, default=DEFAULT_PHOTOS)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--vision", type=Path, default=DEFAULT_VISION)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    args = parser.parse_args(argv)
    paths = Paths(args.photos, args.weather, args.vision, args.scenarios, args.expectations)

    if not paths.photos.is_dir():
        print(f"error: photo folder not found: {paths.photos}", file=sys.stderr)
        return 1
    photos, problems = load_photo_data(paths)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print(f"{len(problems)} problem(s); nothing written.", file=sys.stderr)
        return 1

    result = plan(photos)
    if isinstance(result, Infeasible):
        print(f"error: rule {result.rule} cannot be satisfied: {result.reason}", file=sys.stderr)
        return 1

    if args.check:
        current_problems = check_current(paths, photos, result)
        current_problems += [_format(v) for v in result.violations]
        if current_problems:
            for problem in current_problems:
                print(f"violation: {problem}", file=sys.stderr)
            return 1
        print(f"ok: {len(photos)} photos, current scenario files satisfy S1~S4 and V1~V4")
        _summary(result)
        return 0

    if result.violations:
        for violation in result.violations:
            print(f"violation: {_format(violation)}", file=sys.stderr)
        print("nothing written.", file=sys.stderr)
        return 1

    weather_doc = _read_json(paths.weather)
    weather_doc["today_default"] = today_default_for(result.scenarios_doc)
    write_json_atomic(paths.scenarios, result.scenarios_doc)
    write_json_atomic(paths.weather, weather_doc)
    removed, written = write_sidecars(paths.photos, result.sidecars)
    write_json_atomic(paths.expectations, result.expectations)

    gated = [apply_confidence_gate(p.analysis).photo_type for p in photos]
    print(f"photos: {len(photos)} (stored as outfit {gated.count('outfit')}, "
          f"non_outfit {gated.count('non_outfit')}, uncertain {gated.count('uncertain')}; "
          f"capture from sidecar {sum(p.sidecar_metadata is not None for p in photos)})")
    print(f"wrote {paths.scenarios.name}, today_default in {paths.weather.name}, {paths.expectations.name}")
    print(f"sidecars: removed {removed}, wrote {written}")
    _summary(result)

    after = check_current(paths, photos, result)
    if after:
        for problem in after:
            print(f"violation: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
