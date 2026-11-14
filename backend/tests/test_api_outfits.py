"""Outfit router: list, detail, photo and feedback endpoints (task 10.2).

Config is injected with ``resolve_config({...})``; the real ``.env`` is never read.
Every test gets its own SQLite file and PHOTO_DIR under ``tmp_path``.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, delete

from app.config import resolve_config
from app.db import WeatherSnapshot
from app.deps import get_feedback_service
from app.domain import ClothingTags, WeatherReading
from app.feedback import FeedbackService
from app.main import create_app
from app.repository import OutfitCreate, OutfitRepository

KST = timezone(timedelta(hours=9))
TAGS = ClothingTags(
    upper_type="knit", outer_type="coat", lower_type="slacks",
    colors=["navy", "cream"], layers_visible=2, confidence=0.9,
)


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    path = tmp_path / "photos"
    path.mkdir()
    return path


@pytest.fixture
def client(tmp_path: Path, photo_dir: Path):
    config, issues = resolve_config(
        {"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}", "PHOTO_DIR": str(photo_dir)}
    )
    assert issues == []
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def repo_of(client: TestClient) -> OutfitRepository:
    return client.app.state.container.repository


def add_outfit(
    client: TestClient,
    *,
    sha: str,
    file_name: str = "a.png",
    captured_at: datetime = datetime(2025, 11, 20, 8, 30, tzinfo=KST),
    seed_vote: str | None = None,
    apparent: float | None = None,
) -> int:
    weather = WeatherReading(
        observed_at=captured_at.replace(minute=0),
        actual_temperature_c=9.0,
        apparent_temperature_c=apparent,
        relative_humidity_pct=None,
        wind_speed_kmh=None,
        precipitation_mm=1.5,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )
    outfit = OutfitCreate(
        sha256=sha, file_name=file_name, captured_at=captured_at,
        latitude=37.5665, longitude=126.978, tags=TAGS, vision_provider="fixture",
    )
    return repo_of(client).create_outfit_bundle(outfit, weather, seed_vote)  # type: ignore[arg-type]


def assert_offset(value: str) -> None:
    assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    assert value.endswith("Z") or value[-6] in "+-"


# --- GET /api/outfits ---------------------------------------------------------


def test_get_outfits_200_empty_list(client: TestClient) -> None:
    response = client.get("/api/outfits")
    assert response.status_code == 200
    assert response.json() == []


def test_get_outfits_orders_by_instant_desc_then_id_desc(client: TestClient) -> None:
    # Same instant written with two offsets, plus an earlier and a later one.
    same_kst = datetime(2025, 11, 20, 9, 0, tzinfo=KST)
    same_utc = same_kst.astimezone(timezone.utc)
    a = add_outfit(client, sha="a" * 64, captured_at=same_kst)
    b = add_outfit(client, sha="b" * 64, captured_at=same_utc)
    early = add_outfit(client, sha="c" * 64, captured_at=same_kst - timedelta(days=1))
    late = add_outfit(client, sha="d" * 64, captured_at=same_kst + timedelta(minutes=1))

    body = client.get("/api/outfits").json()
    assert [o["id"] for o in body] == [late, b, a, early]
    # captured_at keeps the original offset (Req 2.2, 10.10).
    by_id = {o["id"]: o for o in body}
    assert by_id[a]["captured_at"].endswith("+09:00")
    assert by_id[b]["captured_at"].endswith("Z") or by_id[b]["captured_at"].endswith("+00:00")


def test_outfit_shape_and_null_fields_present(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="comfortable", apparent=None)
    item = client.get("/api/outfits").json()[0]

    assert item["photo_url"] == f"/api/outfits/{outfit_id}/photo"
    assert item["source"] == "demo_sidecar"
    assert item["vision_provider"] == "fixture"
    assert item["tags"]["colors"] == ["navy", "cream"]
    assert item["effective_vote"] == "comfortable"
    weather = item["weather"]
    # Null weather fields are present as null, never omitted (Req 10.4).
    for key in ("apparent_temperature_c", "relative_humidity_pct", "wind_speed_kmh", "weather_code"):
        assert key in weather and weather[key] is None
    assert weather["precipitation_mm"] == 1.5 and weather["is_fixture"] is True
    assert_offset(item["captured_at"])
    assert_offset(weather["observed_at"])


def test_outfit_without_weather_or_vote_has_null_fields(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64)
    with Session(client.app.state.container.engine) as session:
        session.exec(delete(WeatherSnapshot))  # type: ignore[call-overload]
        session.commit()

    detail = client.get(f"/api/outfits/{outfit_id}").json()
    assert "weather" in detail and detail["weather"] is None
    assert "effective_vote" in detail and detail["effective_vote"] is None
    assert client.get("/api/outfits").json() == [detail]


# --- GET /api/outfits/{id} ------------------------------------------------------


def test_get_outfit_200_matches_list_item(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64)
    response = client.get(f"/api/outfits/{outfit_id}")
    assert response.status_code == 200
    assert response.json() == client.get("/api/outfits").json()[0]


def test_get_outfit_404(client: TestClient) -> None:
    response = client.get("/api/outfits/999")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "OUTFIT_NOT_FOUND", "message": "착장을 찾을 수 없습니다."}}


# --- GET /api/outfits/{id}/photo -------------------------------------------------


def test_photo_200_returns_file_with_media_type(client: TestClient, photo_dir: Path) -> None:
    data = png_bytes()
    (photo_dir / "a.png").write_bytes(data)
    outfit_id = add_outfit(client, sha="a" * 64, file_name="a.png")

    response = client.get(f"/api/outfits/{outfit_id}/photo")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == data


@pytest.mark.parametrize("file_name", ["missing.png", "../outside.png", "sub/inner.png", "a.metadata.json"])
def test_photo_404_no_path_leak(
    client: TestClient, photo_dir: Path, tmp_path: Path, file_name: str
) -> None:
    # Real files exist outside PHOTO_DIR / in a sub-folder / as a sidecar, but must not be served.
    (tmp_path / "outside.png").write_bytes(png_bytes())
    (photo_dir / "sub").mkdir()
    (photo_dir / "sub" / "inner.png").write_bytes(png_bytes())
    (photo_dir / "a.metadata.json").write_text("{}")
    outfit_id = add_outfit(client, sha="a" * 64, file_name=file_name)

    response = client.get(f"/api/outfits/{outfit_id}/photo")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "PHOTO_NOT_FOUND", "message": "사진을 찾을 수 없습니다."}}
    assert str(tmp_path) not in response.text and file_name not in response.text


def test_photo_404_for_unknown_outfit(client: TestClient) -> None:
    response = client.get("/api/outfits/999/photo")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PHOTO_NOT_FOUND"


def test_photo_symlink_escaping_photo_dir_is_404(
    client: TestClient, photo_dir: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret.png").write_bytes(png_bytes())
    (photo_dir / "link.png").symlink_to(tmp_path / "secret.png")
    outfit_id = add_outfit(client, sha="a" * 64, file_name="link.png")
    assert client.get(f"/api/outfits/{outfit_id}/photo").status_code == 404


def test_no_static_files_mount(client: TestClient, photo_dir: Path) -> None:
    (photo_dir / "a.png").write_bytes(png_bytes())
    for path in ("/photos/a.png", "/static/a.png", "/data/demo/photos/a.png"):
        assert client.get(path).status_code == 404


# --- POST /api/outfits/{id}/feedback -----------------------------------------------


def test_feedback_201_and_effective_vote_updates(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="cold")

    for vote in ("hot", "comfortable", "comfortable"):
        response = client.post(f"/api/outfits/{outfit_id}/feedback", json={"thermal_vote": vote})
        assert response.status_code == 201
        body = response.json()
        assert body["outfit_id"] == outfit_id and body["thermal_vote"] == vote
        assert body["created_at"].endswith("Z")
        assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == vote

    # Seed + 3 user rows: history is kept, nothing overwritten (Req 9.5).
    assert repo_of(client).counts()["feedback"] == 4


def test_feedback_extra_fields_ignored(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64)
    response = client.post(
        f"/api/outfits/{outfit_id}/feedback",
        json={"thermal_vote": "hot", "source": "demo_seed", "outfit_id": 999, "created_at": "2000-01-01T00:00:00Z"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["outfit_id"] == outfit_id and body["thermal_vote"] == "hot"
    assert not body["created_at"].startswith("2000")
    assert set(body) == {"outfit_id", "thermal_vote", "created_at"}


def test_feedback_404_for_unknown_outfit(client: TestClient) -> None:
    response = client.post("/api/outfits/999/feedback", json={"thermal_vote": "hot"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OUTFIT_NOT_FOUND"
    assert repo_of(client).counts()["feedback"] == 0


@pytest.mark.parametrize(
    "body",
    [{"thermal_vote": "COLD"}, {"thermal_vote": " cold"}, {"thermal_vote": ""},
     {"thermal_vote": 1}, {"thermal_vote": None}, {"thermal_vote": ["hot"]}, {}],
)
def test_feedback_422_variants_change_nothing(client: TestClient, body: object) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="cold")
    response = client.post(f"/api/outfits/{outfit_id}/feedback", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_THERMAL_VOTE"
    assert repo_of(client).counts()["feedback"] == 1
    assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == "cold"


def test_feedback_body_validated_before_existence(client: TestClient) -> None:
    response = client.post("/api/outfits/999/feedback", json={"thermal_vote": "warm"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_THERMAL_VOTE"


class FailingInsertRepository(OutfitRepository):
    """Real repository whose feedback INSERT fails, to exercise the 500 path."""

    def add_feedback(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("disk I/O error")


def test_feedback_storage_error_returns_500_and_keeps_vote(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="cold")
    failing = FailingInsertRepository(client.app.state.container.engine)
    client.app.dependency_overrides[get_feedback_service] = lambda: FeedbackService(failing)

    response = client.post(f"/api/outfits/{outfit_id}/feedback", json={"thermal_vote": "hot"})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "FEEDBACK_SAVE_FAILED", "message": "피드백을 저장하지 못했습니다."}}
    assert repo_of(client).counts()["feedback"] == 1
    assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == "cold"


def test_openapi_lists_outfit_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"get"} <= set(paths["/api/outfits"])
    assert {"get"} <= set(paths["/api/outfits/{outfit_id}"])
    assert {"get"} <= set(paths["/api/outfits/{outfit_id}/photo"])
    assert {"post"} <= set(paths["/api/outfits/{outfit_id}/feedback"])
