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


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _entry(file_name: str, confirmed: bool) -> dict[str, object]:
    return {"file": file_name, "origin": f"ex_img/{file_name}", "source": "s", "license": "l",
            "rights_confirmed": confirmed}


@pytest.fixture
def layout(tmp_path: Path):
    source = tmp_path / "ex_img"
    source.mkdir()
    (source / "007_linen.png").write_bytes(_png_bytes((255, 0, 0)))
    (source / "2025-01-08_081500_KST.jpg").write_bytes(b"jpeg-bytes")
    (source / "126_unconfirmed.png").write_bytes(_png_bytes((0, 0, 255)))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"photos": [
        _entry("007_linen.png", True),
        _entry("2025-01-08_081500_KST.jpg", True),
        _entry("126_unconfirmed.png", False),
    ]}), encoding="utf-8")
    return tmp_path, manifest, source, tmp_path / "photos"


def test_captured_at_from_kst_file_name():
    assert prepare_mod.captured_at_from_kst_name("2025-01-08_081500_KST.jpg") == "2025-01-08T08:15:00+09:00"
    assert prepare_mod.captured_at_from_kst_name("2025-07-30_200300_KST.JPG") == "2025-07-30T20:03:00+09:00"
    assert prepare_mod.captured_at_from_kst_name("2025-02-30_081500_KST.jpg") is None
    assert prepare_mod.captured_at_from_kst_name("007_front_linen.png") is None


def test_prepare_copies_only_confirmed_and_creates_templates(layout):
    root, manifest, source, dest = layout
    report = prepare_mod.prepare(manifest, source, dest, repo_root=root)

    assert sorted(p.name for p in dest.iterdir()) == [
        "007_linen.metadata.json", "007_linen.png",
        "2025-01-08_081500_KST.jpg", "2025-01-08_081500_KST.metadata.json",
    ]
    assert report.confirmed == 2 and len(report.copied) == 2
    assert (dest / "007_linen.png").read_bytes() == (source / "007_linen.png").read_bytes()
    jpg_sidecar = json.loads((dest / "2025-01-08_081500_KST.metadata.json").read_text(encoding="utf-8"))
    assert jpg_sidecar == {"captured_at": "2025-01-08T08:15:00+09:00", "latitude": 37.5665, "longitude": 126.978}
    png_sidecar = json.loads((dest / "007_linen.metadata.json").read_text(encoding="utf-8"))
    assert png_sidecar["captured_at"] is None


def test_prepare_rerun_is_idempotent_and_keeps_sidecars(layout):
    root, manifest, source, dest = layout
    prepare_mod.prepare(manifest, source, dest, repo_root=root)
    edited = {"captured_at": "2025-07-01T09:00:00+09:00", "latitude": 37.5, "longitude": 127.0,
              "thermal_vote": "hot"}
    sidecar = dest / "007_linen.metadata.json"
    sidecar.write_text(json.dumps(edited), encoding="utf-8")

    report = prepare_mod.prepare(manifest, source, dest, repo_root=root)

    assert report.copied == [] and report.replaced == [] and len(report.unchanged) == 2
    assert report.sidecars_created == [] and len(report.sidecars_kept) == 2
    assert json.loads(sidecar.read_text(encoding="utf-8")) == edited
    assert len(list(dest.iterdir())) == 4


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
                                  "--vision-fixture", str(tmp_path / "vision.json")])
    assert code == 1
    assert not dest.exists()


def test_placeholders_generate_eight_valid_photos(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"photos": [_entry("126_unconfirmed.png", False)]}), encoding="utf-8")
    dest, vision = tmp_path / "photos", tmp_path / "fixtures" / "vision.json"
    args = ["--manifest", str(manifest), "--dest", str(dest), "--vision-fixture", str(vision)]

    assert placeholders_mod.main(args) == 0
    pngs = sorted(dest.glob("*.png"))
    first_bytes = [p.read_bytes() for p in pngs]
    assert placeholders_mod.main(args) == 0  # deterministic rerun

    assert len(pngs) == 8
    assert [p.read_bytes() for p in pngs] == first_bytes
    fixture = json.loads(vision.read_text(encoding="utf-8"))
    assert set(fixture) == {hashlib.sha256(b).hexdigest() for b in first_bytes}
    captured = set()
    for png in pngs:
        with Image.open(png) as image:
            assert image.format == "PNG" and image.size == (512, 640)
        sidecar = json.loads(png.with_name(png.stem + ".metadata.json").read_text(encoding="utf-8"))
        assert sidecar["captured_at"].endswith("+09:00")
        assert sidecar.get("thermal_vote") in {None, "cold", "comfortable", "hot"}
        captured.add(sidecar["captured_at"])
    assert len(captured) == 8
