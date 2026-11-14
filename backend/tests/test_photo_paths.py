"""Photo file location for watched photos (tasks 3.14, Req 3.5, 3.6)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.outfits import resolve_outfit_photo
from app.config import AutomationConfig, resolve_config
from app.domain import WeatherReading
from app.gallery.store import GalleryStore
from app.main import create_app
from app.repository import OutfitCreate
from tests.gallery_support import KNIT_TAGS, T0, png_bytes

KST = timezone(timedelta(hours=9))


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    photo_dir = tmp_path / "demo" / "photos"
    user_dir = tmp_path / "user" / "photos"
    photo_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    return photo_dir, user_dir


@pytest.fixture
def client(tmp_path: Path, dirs: tuple[Path, Path]):
    photo_dir, user_dir = dirs
    config, _ = resolve_config({"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}", "PHOTO_DIR": str(photo_dir)})
    config = replace(config, automation=AutomationConfig(user_photo_dir=user_dir, gallery_dir=tmp_path / "gallery"))
    with TestClient(create_app(config), raise_server_exceptions=False) as test_client:
        yield test_client


def add_outfit(client: TestClient, data: bytes, file_name: str, *, at: datetime) -> tuple[int, str]:
    sha = hashlib.sha256(data).hexdigest()
    reading = WeatherReading(
        observed_at=at, actual_temperature_c=12.0, apparent_temperature_c=None, relative_humidity_pct=50,
        wind_speed_kmh=None, precipitation_mm=0.0, weather_code=None, provider="fixture", is_fixture=True,
    )
    outfit = OutfitCreate(sha256=sha, file_name=file_name, captured_at=at, latitude=37.5, longitude=127.0,
                          tags=KNIT_TAGS, vision_provider="fixture")
    return client.app.state.container.repository.create_outfit_bundle(outfit, reading, None), sha


def store_of(client: TestClient) -> GalleryStore:
    return GalleryStore(client.app.state.container.engine)


def test_user_photo_served_from_user_dir(client: TestClient, dirs: tuple[Path, Path]) -> None:
    photo_dir, user_dir = dirs
    data = png_bytes((1, 2, 3))
    outfit_id, sha = add_outfit(client, data, "capture_x.png", at=datetime(2026, 11, 14, 9, tzinfo=KST))
    (user_dir / "capture_x.png").write_bytes(data)
    (photo_dir / "capture_x.png").write_bytes(b"decoy in the demo folder")
    store_of(client).put_user_photo(sha, "capture_x.png", "capture_x.png", True, T0)

    response = client.get(f"/api/outfits/{outfit_id}/photo")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"


def test_demo_photo_still_served_from_photo_dir(client: TestClient, dirs: tuple[Path, Path]) -> None:
    photo_dir, user_dir = dirs
    data = png_bytes((4, 5, 6))
    outfit_id, _ = add_outfit(client, data, "007_front.png", at=datetime(2025, 11, 20, 8, tzinfo=KST))
    (photo_dir / "007_front.png").write_bytes(data)
    (user_dir / "007_front.png").write_bytes(b"not this one")
    response = client.get(f"/api/outfits/{outfit_id}/photo")
    assert response.status_code == 200 and response.content == data


def test_user_photo_missing_does_not_fall_back(client: TestClient, dirs: tuple[Path, Path]) -> None:
    photo_dir, _ = dirs
    data = png_bytes((7, 8, 9))
    outfit_id, sha = add_outfit(client, data, "gone.png", at=datetime(2026, 11, 14, 9, tzinfo=KST))
    (photo_dir / "gone.png").write_bytes(data)
    store_of(client).put_user_photo(sha, "gone.png", "gone.png", False, T0)
    response = client.get(f"/api/outfits/{outfit_id}/photo")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PHOTO_NOT_FOUND"


@pytest.mark.parametrize("stored_name", ["../escape.png", "/etc/hosts.png", "sub/inner.png", "link.png", "notes.txt"])
def test_photo_path_traversal_404(
    client: TestClient, dirs: tuple[Path, Path], tmp_path: Path, stored_name: str
) -> None:
    _, user_dir = dirs
    data = png_bytes((10, 11, 12))
    (tmp_path / "user" / "escape.png").write_bytes(data)
    (user_dir / "sub").mkdir()
    (user_dir / "sub" / "inner.png").write_bytes(data)
    (user_dir / "notes.txt").write_bytes(b"x")
    (user_dir / "link.png").symlink_to(tmp_path / "user" / "escape.png")
    outfit_id, sha = add_outfit(client, data, "x.png", at=datetime(2026, 11, 14, 9, tzinfo=KST))
    store_of(client).put_user_photo(sha, stored_name, "x.png", False, T0)
    response = client.get(f"/api/outfits/{outfit_id}/photo")
    assert response.status_code == 404
    assert str(tmp_path) not in response.text


def test_resolve_outfit_photo_unit(tmp_path: Path) -> None:
    photo_dir, user_dir = tmp_path / "p", tmp_path / "u"
    photo_dir.mkdir()
    user_dir.mkdir()
    (photo_dir / "a.png").write_bytes(b"a")
    (user_dir / "b.png").write_bytes(b"b")

    class Record:
        sha256 = "s" * 64
        file_name = "a.png"

    assert resolve_outfit_photo(Record(), photo_dir=photo_dir, user_photo_dir=user_dir, user_photos={}) == (
        photo_dir / "a.png").resolve()
    assert resolve_outfit_photo(Record(), photo_dir=photo_dir, user_photo_dir=user_dir,
                                user_photos={"s" * 64: "b.png"}) == (user_dir / "b.png").resolve()
    assert resolve_outfit_photo(Record(), photo_dir=photo_dir, user_photo_dir=user_dir,
                                user_photos={"s" * 64: "a.png"}) is None


def test_imported_outfit_listed_in_outfits_and_candidates(client: TestClient, dirs: tuple[Path, Path]) -> None:
    _, user_dir = dirs
    data = png_bytes((13, 14, 15))
    demo_id, _ = add_outfit(client, png_bytes((16, 17, 18)), "007_front.png",
                            at=datetime(2025, 11, 20, 8, tzinfo=KST))
    user_id, sha = add_outfit(client, data, "capture_y.png", at=datetime(2026, 11, 14, 9, tzinfo=KST))
    (user_dir / "capture_y.png").write_bytes(data)
    store_of(client).put_user_photo(sha, "capture_y.png", "capture_y.png", False, T0)

    listed = client.get("/api/outfits").json()
    assert [item["id"] for item in listed] == [user_id, demo_id]  # newest capture first, same rules
    assert "path" not in listed[0] and str(user_dir) not in str(listed)
    detail = client.get(f"/api/outfits/{user_id}").json()
    assert detail["photo_url"] == f"/api/outfits/{user_id}/photo"
    assert client.get(detail["photo_url"]).content == data
    candidates = client.app.state.container.repository.list_candidates()
    assert {c.outfit_id for c in candidates} == {demo_id, user_id}
