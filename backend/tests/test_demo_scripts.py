"""Unit tests for scripts/prepare_demo_photos.py and scripts/make_placeholders.py."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prepare_mod = _load("prepare_demo_photos")
placeholders_mod = _load("make_placeholders")


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
