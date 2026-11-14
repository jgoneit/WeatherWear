"""Unit tests for the demo data scripts: prepare_demo_photos.py, make_placeholders.py,
build_fixtures.py (legacy builder guard) and import_clothing_tags.py (supplement mode)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.domain import VisionAnalysis

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prepare_mod = _load("prepare_demo_photos")
placeholders_mod = _load("make_placeholders")
build_mod = _load("build_fixtures")
import_mod = _load("import_clothing_tags")


USER_COMMENT = {"date": "2025-01-08", "time": "08:15:00", "timezone": "Asia/Seoul",
                "utc_offset": "+09:00", "latitude": 37.556, "longitude": 126.924,
                "location": "Seoul", "synthetic": True}


def _image_bytes(color: tuple[int, int, int], fmt: str = "PNG", comment: dict | None = USER_COMMENT) -> bytes:
    """8×8 image; with ``comment`` it carries a UserComment JSON like the ex_img photos."""
    buffer = io.BytesIO()
    kwargs = {}
    if comment is not None:
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9286] = b"ASCII\x00\x00\x00" + json.dumps(comment).encode()
        kwargs["exif"] = exif.tobytes()
    Image.new("RGB", (8, 8), color).save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _entry(file_name: str, confirmed: bool) -> dict[str, object]:
    return {"file": file_name, "origin": f"ex_img/{file_name}", "source": "s", "license": "l",
            "rights_confirmed": confirmed}


@pytest.fixture
def layout(tmp_path: Path):
    source = tmp_path / "ex_img"
    source.mkdir()
    (source / "007_linen.png").write_bytes(_image_bytes((255, 0, 0)))
    (source / "2025-01-08_081500_KST.jpg").write_bytes(_image_bytes((0, 255, 0), fmt="JPEG"))
    (source / "126_unconfirmed.png").write_bytes(_image_bytes((0, 0, 255)))
    (source / "clothing_tags.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"photos": [
        _entry("007_linen.png", True),
        _entry("2025-01-08_081500_KST.jpg", True),
        _entry("126_unconfirmed.png", False),
    ]}), encoding="utf-8")
    return tmp_path, manifest, source, tmp_path / "photos"


def test_prepare_copies_confirmed_photos_byte_for_byte_without_sidecars(layout):
    root, manifest, source, dest = layout
    report = prepare_mod.prepare(manifest, source, dest, repo_root=root)

    assert sorted(p.name for p in dest.iterdir()) == ["007_linen.png", "2025-01-08_081500_KST.jpg"]
    assert report.confirmed == 2 and len(report.copied) == 2 and report.ok
    assert report.metadata_problems == [] and report.sidecars_present == 0
    for name in ("007_linen.png", "2025-01-08_081500_KST.jpg"):
        assert (dest / name).read_bytes() == (source / name).read_bytes()


def test_prepare_replaces_stale_copy_and_rerun_is_idempotent(layout):
    root, manifest, source, dest = layout
    dest.mkdir()
    (dest / "007_linen.png").write_bytes(_image_bytes((255, 0, 0), comment=None))  # pre-metadata copy
    vote_sidecar = dest / "007_linen.metadata.json"
    vote_sidecar.write_text('{"thermal_vote": "hot"}', encoding="utf-8")

    first = prepare_mod.prepare(manifest, source, dest, repo_root=root)
    second = prepare_mod.prepare(manifest, source, dest, repo_root=root)

    assert first.replaced == ["007_linen.png"] and first.copied == ["2025-01-08_081500_KST.jpg"]
    assert (dest / "007_linen.png").read_bytes() == (source / "007_linen.png").read_bytes()
    assert second.copied == [] and second.replaced == [] and len(second.unchanged) == 2 and second.ok
    assert vote_sidecar.read_text(encoding="utf-8") == '{"thermal_vote": "hot"}'
    assert second.sidecars_present == 1 and len(list(dest.iterdir())) == 3


def test_prepare_reports_photo_without_embedded_metadata(layout, capsys, monkeypatch):
    root, manifest, source, dest = layout
    (source / "007_linen.png").write_bytes(_image_bytes((255, 0, 0), comment=None))
    bad_comment = {**USER_COMMENT, "latitude": 123.0}
    (source / "2025-01-08_081500_KST.jpg").write_bytes(_image_bytes((0, 255, 0), "JPEG", bad_comment))

    report = prepare_mod.prepare(manifest, source, dest, repo_root=root)
    assert not report.ok
    assert {(p.file, p.code) for p in report.metadata_problems} == {
        ("007_linen.png", "METADATA_NOT_FOUND"),
        ("2025-01-08_081500_KST.jpg", "METADATA_OUT_OF_RANGE"),
    }

    monkeypatch.setattr(prepare_mod, "REPO_ROOT", root)
    code = prepare_mod.main(["--manifest", str(manifest), "--source", str(source), "--dest", str(dest)])
    assert code == 1
    assert "007_linen.png (METADATA_NOT_FOUND)" in capsys.readouterr().err


def test_prepare_rejects_origin_outside_source(layout):
    root, manifest, source, dest = layout
    manifest.write_text(json.dumps({"photos": [
        {"file": "x.png", "origin": "../x.png", "rights_confirmed": True}]}), encoding="utf-8")
    with pytest.raises(prepare_mod.ManifestError):
        prepare_mod.prepare(manifest, source, dest, repo_root=root)
    assert not dest.exists()


@pytest.mark.parametrize("matches", [True, False])
def test_prepare_accepts_bundled_photo_only_with_matching_manifest_hash(layout, matches):
    root, manifest, source, dest = layout
    data = (source / "007_linen.png").read_bytes()
    dest.mkdir()
    (dest / "007_linen.png").write_bytes(data)
    (source / "007_linen.png").unlink()
    entry = _entry("007_linen.png", True)
    entry["sha256"] = hashlib.sha256(data).hexdigest() if matches else "0" * 64
    manifest.write_text(json.dumps({"photos": [entry]}), encoding="utf-8")
    report = prepare_mod.prepare(manifest, source, dest, repo_root=root)
    assert report.unchanged == (["007_linen.png"] if matches else [])
    assert report.missing_sources == ([] if matches else ["007_linen.png"])
    assert (dest / "007_linen.png").read_bytes() == data


def test_personal_album_fixture_labels_twenty_outfits_and_twenty_non_outfits() -> None:
    outfit_stems = sorted(key for key in build_mod.TAGS if key.startswith("2025-"))
    non_outfit_stems = sorted(build_mod.NON_OUTFIT)
    assert len(outfit_stems) == 20
    assert len(non_outfit_stems) == 20
    assert set(outfit_stems).isdisjoint(non_outfit_stems)

    analyses = {
        stem: VisionAnalysis.model_validate(build_mod.vision_analysis_for(stem))
        for stem in outfit_stems + non_outfit_stems
    }
    assert {stem for stem, value in analyses.items() if value.photo_type == "outfit"} == set(
        outfit_stems
    )
    assert {
        stem for stem, value in analyses.items() if value.photo_type == "non_outfit"
    } == set(non_outfit_stems)


def test_import_uses_the_build_fixtures_convention() -> None:
    outfit, non_outfit = "081_hiphop_varsity_baggy_denim", sorted(build_mod.NON_OUTFIT)[0]
    for stem in (outfit, non_outfit):
        expected = VisionAnalysis.model_validate(build_mod.vision_analysis_for(stem))
        assert import_mod.analysis_for(expected.tags) == expected


def _raw_tags(upper: str, outer: str, lower: str) -> dict[str, object]:
    return {"upper_type": upper, "outer_type": outer, "lower_type": lower,
            "colors": ["navy"], "layers_visible": 2, "confidence": 0.85}


@pytest.fixture
def tag_layout(tmp_path: Path):
    """Two photos, a clothing_tags.json for both and a vision.json that has only the first."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "001_kept.png").write_bytes(_image_bytes((10, 20, 30)))
    (photos / "002_added.png").write_bytes(_image_bytes((40, 50, 60)))
    source = tmp_path / "clothing_tags.json"
    images = {
        "001_kept.png": {"sha256": "s1", "tags": _raw_tags("knit top", "wool coat", "jeans")},
        "002_added.png": {"sha256": "s2", "tags": _raw_tags("top", "utility vest", "cargo trousers")},
    }
    source.write_text(json.dumps({"schema": "WeatherWear ClothingTags", "image_count": 2,
                                  "images": images}), encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    kept_sha = hashlib.sha256((photos / "001_kept.png").read_bytes()).hexdigest()
    kept_value = {
        "photo_type": "outfit",
        "photo_type_confidence": 1.0,
        "reason": "A person with visible clothing is the main subject.",
        "tags": _raw_tags("knit", "coat", "jeans"),
    }
    (fixtures / "vision.json").write_text(json.dumps({kept_sha: kept_value}, indent=2), encoding="utf-8")
    return source, photos, fixtures, kept_sha, kept_value, images


def test_import_keeps_existing_entries_and_adds_missing_ones(tag_layout):
    source, photos, fixtures, kept_sha, kept_value, _ = tag_layout

    assert import_mod.run(source, photos, fixtures) == 0
    vision = json.loads((fixtures / "vision.json").read_text(encoding="utf-8"))
    added_sha = hashlib.sha256((photos / "002_added.png").read_bytes()).hexdigest()
    assert list(vision) == [kept_sha, added_sha]  # photo file name order
    assert vision[kept_sha] == kept_value
    added = VisionAnalysis.model_validate(vision[added_sha])
    assert added.photo_type == "outfit" and added.photo_type_confidence == 1.0
    # "top" is the taxonomy's own alias for unknown; the others map to canonical values.
    assert (added.tags.upper_type, added.tags.outer_type, added.tags.lower_type) == (
        "unknown", "vest", "cargo_pants")
    sources = json.loads((fixtures / "vision.sources.json").read_text(encoding="utf-8"))
    assert sources == {kept_sha: {"file": "001_kept.png", "source_sha256": "s1"},
                       added_sha: {"file": "002_added.png", "source_sha256": "s2"}}

    first = (fixtures / "vision.json").read_bytes()
    assert import_mod.run(source, photos, fixtures) == 0  # rerun adds nothing
    assert (fixtures / "vision.json").read_bytes() == first


def test_import_refuses_to_change_an_existing_entry(tag_layout, capsys):
    source, photos, fixtures, kept_sha, kept_value, images = tag_layout
    images["001_kept.png"]["tags"]["confidence"] = 0.5  # the source now disagrees
    source.write_text(json.dumps({"schema": "WeatherWear ClothingTags", "image_count": 2,
                                  "images": images}), encoding="utf-8")
    before = (fixtures / "vision.json").read_bytes()

    assert import_mod.run(source, photos, fixtures) == 1
    assert "001_kept.png: clothing_tags.json would change the existing vision.json entry" in capsys.readouterr().out
    assert (fixtures / "vision.json").read_bytes() == before
    assert not (fixtures / "vision.sources.json").exists()


def test_import_rejects_unmapped_garment_names(tag_layout, capsys):
    source, photos, fixtures, _, _, images = tag_layout
    images["002_added.png"]["tags"]["outer_type"] = "space suit"
    source.write_text(json.dumps({"schema": "WeatherWear ClothingTags", "image_count": 2,
                                  "images": images}), encoding="utf-8")
    before = (fixtures / "vision.json").read_bytes()

    assert import_mod.run(source, photos, fixtures) == 1
    assert "002_added.png: outer_type 'space suit' has no canonical value" in capsys.readouterr().out
    assert (fixtures / "vision.json").read_bytes() == before


def test_build_fixtures_refuses_recorded_demo_data(tmp_path, monkeypatch, capsys):
    # The repository's own data are the recorded set, so the legacy builder refuses.
    assert build_mod.recorded_data_refusal(build_mod.DEFAULT_FIXTURES) is not None

    repo_fixtures, target, photos = tmp_path / "repo_fixtures", tmp_path / "target", tmp_path / "photos"
    for folder in (repo_fixtures, target, photos):
        folder.mkdir()
    monkeypatch.setattr(build_mod, "DEFAULT_FIXTURES", repo_fixtures)
    monkeypatch.setattr(build_mod, "SCENARIOS_PATH", tmp_path / "scenarios.json")
    (photos / "001_x.png").write_bytes(_image_bytes((1, 2, 3)))
    legacy = json.dumps({"entries": [], "today_default": {}})
    recorded = json.dumps({"recorded": {"source": "open-meteo"}, "entries": []})

    (repo_fixtures / "weather.json").write_text(legacy, encoding="utf-8")
    assert build_mod.recorded_data_refusal(target) is None  # pre-Phase-1 data only

    for where in (target, repo_fixtures):  # recorded data in the target or in the repository
        (where / "weather.json").write_text(recorded, encoding="utf-8")
        for args in ([], ["--check"]):
            assert build_mod.main(["--photos", str(photos), "--fixtures", str(target), *args]) == 1
            assert "Nothing was written" in capsys.readouterr().err
        assert sorted(p.name for p in photos.iterdir()) == ["001_x.png"]
        assert sorted(p.name for p in target.iterdir()) == (["weather.json"] if where is target else [])
        assert not (tmp_path / "scenarios.json").exists()
        (where / "weather.json").write_text(legacy, encoding="utf-8")
        if where is target:
            (target / "weather.json").unlink()


def test_placeholders_refuse_when_photos_confirmed(layout, tmp_path):
    _, manifest, _, dest = layout
    code = placeholders_mod.main(["--manifest", str(manifest), "--dest", str(dest),
                                  "--vision-fixture", str(tmp_path / "vision.json"),
                                  "--weather-fixture", str(tmp_path / "weather.json")])
    assert code == 1
    assert not dest.exists()
    assert not (tmp_path / "weather.json").exists()


@pytest.mark.parametrize("confirmed", [False, True])
def test_placeholders_generate_eight_photos_with_embedded_metadata(tmp_path, confirmed):
    from app.ingestion.metadata import read_embedded_metadata
    from app.providers.weather.fixture import FixtureWeatherProvider

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"photos": [_entry("126_x.png", confirmed)]}), encoding="utf-8")
    dest = tmp_path / "photos"
    vision, weather = tmp_path / "fixtures" / "vision.json", tmp_path / "fixtures" / "weather.json"
    args = ["--manifest", str(manifest), "--dest", str(dest), "--vision-fixture", str(vision),
            "--weather-fixture", str(weather)] + (["--force"] if confirmed else [])

    assert placeholders_mod.main(args) == 0
    pngs = sorted(dest.glob("*.png"))
    first_bytes = [p.read_bytes() for p in pngs]
    first_weather = weather.read_text(encoding="utf-8")
    assert placeholders_mod.main(args) == 0  # deterministic rerun

    assert len(pngs) == 8
    assert [p.read_bytes() for p in pngs] == first_bytes
    assert weather.read_text(encoding="utf-8") == first_weather
    assert not list(dest.glob("*.metadata.json"))
    fixture = json.loads(vision.read_text(encoding="utf-8"))
    assert set(fixture) == {hashlib.sha256(b).hexdigest() for b in first_bytes}

    weather_data = json.loads(first_weather)
    assert weather_data["recorded"]["source"] == "placeholder"
    assert len(weather_data["entries"]) == 8
    provider = FixtureWeatherProvider(weather)
    assert provider.entry_count == 8
    captured = set()
    for png, data, entry in zip(pngs, first_bytes, weather_data["entries"], strict=True):
        with Image.open(png) as image:
            assert image.format == "PNG" and image.size == (512, 640)
        meta = read_embedded_metadata(data)
        assert meta is not None and meta.origin == "user_comment"
        assert meta.synthetic is True
        assert meta.captured_at.utcoffset().total_seconds() == 9 * 3600
        assert (meta.latitude, meta.longitude) == (37.5665, 126.978)
        assert entry["file"] == png.name
        reading = provider.get_weather(meta.captured_at, meta.latitude, meta.longitude)
        assert reading.actual_temperature_c == entry["actual_temperature_c"]
        captured.add(meta.captured_at)
    assert len(captured) == 8
