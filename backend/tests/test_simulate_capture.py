"""Capture simulator (tasks 3.17, Req 8.1–8.8). tmp folders only, offline."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from app.db import create_db_engine
from app.domain import CaptureMetadata, ClothingTags
from app.gallery.simulate import (
    PLACEHOLDER_TAGS,
    capture_file_name,
    draw_placeholder,
    encode_with_exif,
    is_demo_source,
    overlay_entry,
    user_comment_payload,
)
from app.ingestion.metadata import EXIF_IFD, TAG_USER_COMMENT, read_embedded_metadata
from app.location import LatLon, LocationService
from app.providers.vision.fixture import load_vision_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))
AT = "2026-11-14T08:10:00+09:00"


def load_script():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("simulate_capture", REPO_ROOT / "scripts" / "simulate_capture.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Sim:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.gallery = tmp_path / "gallery"
        self.user = tmp_path / "user" / "photos"
        self.pool = tmp_path / "user" / "capture_pool"
        self.overlay = tmp_path / "user" / "fixtures" / "vision.overlay.json"
        self.db = tmp_path / "ww.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{self.db}")
        monkeypatch.setenv("USER_PHOTO_DIR", str(self.user))
        monkeypatch.setenv("GALLERY_DIR", str(self.gallery))
        monkeypatch.setenv("VISION_PROVIDER", "fixture")
        monkeypatch.setenv("WEATHER_PROVIDER", "fixture")
        self.env_file = tmp_path / "absent.env"
        self.script = load_script()

    def run(self, *args: str) -> int:
        return self.script.main([*args, "--env-file", str(self.env_file)])

    def files(self) -> list[str]:
        return sorted(p.name for p in self.gallery.iterdir()) if self.gallery.exists() else []

    def overlay_map(self) -> dict:
        return load_vision_fixture(self.overlay) if self.overlay.exists() else {}


@pytest.fixture
def sim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sim:
    return Sim(tmp_path, monkeypatch)


def png_file(path: Path, color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color).save(path, "PNG")
    return path


def strip_user_comment(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        exif = image.getexif()
        exif.get_ifd(EXIF_IFD).pop(TAG_USER_COMMENT, None)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95, exif=exif.tobytes())
    return buffer.getvalue()


def test_simulate_writes_exif_and_prints_name(sim: Sim, capsys: pytest.CaptureFixture[str]) -> None:
    assert sim.run("--at", AT, "--lat", "35.1796", "--lon", "129.0756") == 0
    out = capsys.readouterr().out
    assert out == "capture_20261114_081000.jpg\n"  # the file name only
    data = (sim.gallery / "capture_20261114_081000.jpg").read_bytes()

    meta = read_embedded_metadata(data)
    assert isinstance(meta, CaptureMetadata) and meta.origin == "user_comment"
    assert meta.captured_at == datetime(2026, 11, 14, 8, 10, tzinfo=KST)
    assert meta.captured_at.utcoffset() == timedelta(hours=9)
    assert (meta.latitude, meta.longitude, meta.synthetic) == (35.1796, 129.0756, True)

    standard = read_embedded_metadata(strip_user_comment(data))
    assert isinstance(standard, CaptureMetadata) and standard.origin == "exif_standard"
    assert standard.captured_at == meta.captured_at and standard.synthetic is True
    assert abs(standard.latitude - 35.1796) < 1e-5 and abs(standard.longitude - 129.0756) < 1e-5
    assert sim.files() == ["capture_20261114_081000.jpg"]  # no temp file left behind


def test_user_comment_payload_fields() -> None:
    payload = user_comment_payload(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-5))),
                                   1.5, -2.5, "Asia/Seoul")
    assert payload == {
        "date": "2026-01-02", "time": "03:04:05", "timezone": "Asia/Seoul", "utc_offset": "-05:00",
        "latitude": 1.5, "longitude": -2.5, "synthetic": True, "source": "simulate_capture",
    }


def test_png_keeps_user_comment_metadata() -> None:
    at = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone(timedelta(hours=14)))
    data = encode_with_exif(Image.new("RGB", (8, 8)), "PNG", at, -33.86, 151.21, "Asia/Seoul")
    meta = read_embedded_metadata(data)
    assert isinstance(meta, CaptureMetadata)
    assert meta.captured_at == at and (meta.latitude, meta.longitude) == (-33.86, 151.21)


def test_simulate_refuses_demo_photo(sim: Sim, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = json.loads((REPO_ROOT / "data" / "demo" / "photos.manifest.json").read_text(encoding="utf-8"))
    demo_name = manifest["photos"][0]["file"]
    source = png_file(tmp_path / "elsewhere" / demo_name, (1, 2, 3))
    assert sim.run("--source", str(source), "--at", AT) == 2
    assert "데모 사진" in capsys.readouterr().err
    assert sim.files() == [] and not sim.overlay.exists()

    demo_photo = REPO_ROOT / "data" / "demo" / "photos" / demo_name
    if demo_photo.exists():  # same bytes under another name: refused by hash
        renamed = tmp_path / "renamed.png"
        renamed.write_bytes(demo_photo.read_bytes())
        assert sim.run("--source", str(renamed), "--at", AT) == 2
        assert sim.files() == []


def test_is_demo_source_rules() -> None:
    assert is_demo_source("007.png", "a" * 64, {"007.png"}, set())
    assert is_demo_source("mine.png", "A" * 64, set(), {"a" * 64})
    assert not is_demo_source("mine.png", "b" * 64, {"007.png"}, {"a" * 64})


def test_simulate_placeholder_when_pool_empty(sim: Sim, capsys: pytest.CaptureFixture[str]) -> None:
    sim.pool.mkdir(parents=True)  # exists but has no images
    assert sim.run("--at", AT) == 0
    name = capsys.readouterr().out.strip()
    data = (sim.gallery / name).read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (768, 1024) and image.format == "JPEG"
    entry = sim.overlay_map()[hashlib.sha256(data).hexdigest()]
    assert entry == overlay_entry(PLACEHOLDER_TAGS)
    assert (entry.photo_type, entry.photo_type_confidence, entry.reason) == (
        "outfit", 0.95, "simulate_capture fixture")
    meta = read_embedded_metadata(data)  # default location without --lat/--lon or a DB
    assert (meta.latitude, meta.longitude) == (37.5665, 126.978)
    assert not sim.db.exists()  # the DB is read-only and never created


def test_simulate_pool_rotation_and_tags(sim: Sim, capsys: pytest.CaptureFixture[str]) -> None:
    png_file(sim.pool / "b.png", (9, 9, 9))
    png_file(sim.pool / "a.png", (8, 8, 8))
    (sim.pool / "clothing_tags.json").write_text(json.dumps({"images": {"a.png": {"tags": {
        "upper_type": "t-shirt", "outer_type": "cardigan", "lower_type": "jeans",
        "colors": ["beige"], "layers_visible": 2, "confidence": 0.7}}}}), encoding="utf-8")

    names = []
    for minute in range(3):
        assert sim.run("--at", f"2026-11-14T08:1{minute}:00+09:00") == 0
        names.append(capsys.readouterr())
    assert [n.out.strip() for n in names] == [
        "capture_20261114_081000.png", "capture_20261114_081100.png", "capture_20261114_081200.png"]
    assert (sim.pool / ".last_used").read_text(encoding="utf-8") == "a.png"  # a → b → a

    overlay = sim.overlay_map()
    first = (sim.gallery / "capture_20261114_081000.png").read_bytes()
    tags = overlay[hashlib.sha256(first).hexdigest()].tags
    assert (tags.upper_type, tags.outer_type, tags.lower_type) == ("t_shirt", "cardigan", "jeans")
    # b.png has no tags: no overlay entry and the notice.
    second = (sim.gallery / "capture_20261114_081100.png").read_bytes()
    assert hashlib.sha256(second).hexdigest() not in overlay
    assert "uncertain" in names[1].err
    assert len(overlay) == 2


def test_simulate_atomic_name_collision_suffix(sim: Sim, capsys: pytest.CaptureFixture[str]) -> None:
    sim.gallery.mkdir()
    (sim.gallery / "capture_20261114_081000.jpg").write_bytes(b"older photo")
    assert sim.run("--at", AT) == 0
    assert sim.run("--at", AT) == 0
    assert capsys.readouterr().out.split() == ["capture_20261114_081000_2.jpg", "capture_20261114_081000_3.jpg"]
    assert (sim.gallery / "capture_20261114_081000.jpg").read_bytes() == b"older photo"
    assert not any(name.startswith(".") for name in sim.files())


def test_capture_file_name_rules() -> None:
    at = datetime(2026, 11, 14, 8, 10, 0, tzinfo=KST)
    assert capture_file_name(at, ".png", set()) == "capture_20261114_081000.png"
    assert capture_file_name(at, "png", {"CAPTURE_20261114_081000.PNG"}) == "capture_20261114_081000_2.png"


def test_simulate_overlay_written_before_publish(sim: Sim, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    original = sim.script.publish_atomic

    def checking_publish(gallery_dir, data, at, ext):  # noqa: ANN001, ANN202
        seen.append(hashlib.sha256(data).hexdigest() in sim.overlay_map())
        assert not list(Path(gallery_dir).glob("capture_*")) if Path(gallery_dir).exists() else True
        return original(gallery_dir, data, at, ext)

    monkeypatch.setattr(sim.script, "publish_atomic", checking_publish)
    assert sim.run("--at", AT) == 0
    assert seen == [True]


def test_simulate_no_tags_prints_uncertain_notice(sim: Sim, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    source = png_file(tmp_path / "mine" / "selfie.png", (40, 50, 60))
    assert sim.run("--source", str(source), "--at", AT) == 0
    captured = capsys.readouterr()
    assert captured.out == "capture_20261114_081000.png\n"
    assert "uncertain" in captured.err
    assert not sim.overlay.exists()


def test_simulate_explicit_tags_and_no_fixture(sim: Sim, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = png_file(tmp_path / "mine" / "coat.png", (70, 80, 90))
    tags = ClothingTags(upper_type="shirt", outer_type="coat", lower_type="slacks", colors=["navy"],
                        layers_visible=2, confidence=0.8)
    assert sim.run("--source", str(source), "--at", AT, "--tags", tags.model_dump_json()) == 0
    name = capsys.readouterr().out.strip()
    sha = hashlib.sha256((sim.gallery / name).read_bytes()).hexdigest()
    assert sim.overlay_map()[sha].tags == tags

    assert sim.run("--source", str(source), "--at", "2026-11-14T09:00:00+09:00", "--no-fixture") == 0
    name = capsys.readouterr().out.strip()
    sha = hashlib.sha256((sim.gallery / name).read_bytes()).hexdigest()
    assert sha not in sim.overlay_map()


def test_simulate_uses_last_location_read_only(sim: Sim, capsys: pytest.CaptureFixture[str]) -> None:
    engine = create_db_engine(f"sqlite:///{sim.db}")
    LocationService(engine, LatLon(37.5665, 126.978)).report(35.1796, 129.0756, 30.0)
    engine.dispose()
    assert sim.run("--at", AT) == 0
    meta = read_embedded_metadata((sim.gallery / capsys.readouterr().out.strip()).read_bytes())
    assert (meta.latitude, meta.longitude) == (35.18, 129.08)


@pytest.mark.parametrize("args", [["--at", "2026-11-14T08:10:00"], ["--at", "nope"], ["--lat", "37.5"],
                                  ["--lat", "91", "--lon", "0"], ["--tags", "[1]"]])
def test_simulate_rejects_bad_arguments(sim: Sim, args: list[str]) -> None:
    assert sim.run(*args) == 2
    assert sim.files() == []


def test_simulate_no_network(sim: Sim, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[object] = []
    guarded = socket.socket.connect

    def recording_connect(self, address):  # noqa: ANN001, ANN202
        attempts.append(address)
        return guarded(self, address)

    monkeypatch.setattr(socket.socket, "connect", recording_connect)
    assert sim.run("--at", AT) == 0
    assert attempts == []


def test_draw_placeholder_size() -> None:
    assert draw_placeholder().size == (768, 1024)
