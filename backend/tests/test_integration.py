"""Fixture-mode integration tests (task 17.1, Req 15.1, 15.9, 17.2, 17.5).

Every app is built with ``create_app(config=resolve_config({...}))`` and a
fresh SQLite file under ``tmp_path``, so the repository ``.env`` and
``data/weatherwear.db`` are never touched. Providers are the fixture ones
(the defaults), so no API key or network is needed.

Tests that use the real demo images (``data/demo/photos/*.png|*.jpg``) skip
when those images are absent: the images are git-ignored and are copied by
``scripts/prepare_demo_photos.py``. The sidecars and fixtures are tracked.
"""

from __future__ import annotations

import hashlib
import io
import json
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import resolve_config
from app.deps import get_vision_provider
from app.domain import ClothingTags, VisionAnalysis
from app.ingestion.image_check import SUPPORTED_EXTENSIONS
from app.main import create_app
from app.providers.vision.fixture import FixtureVisionProvider
from tests.conftest import _is_local_address

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "data" / "demo"
DEMO_PHOTO_DIR = DEMO_DIR / "photos"
MANIFEST_PATH = DEMO_DIR / "photos.manifest.json"


def confirmed_demo_photos() -> list[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return [p["file"] for p in manifest["photos"] if p.get("rights_confirmed") is True]


def demo_images_present() -> bool:
    return DEMO_PHOTO_DIR.is_dir() and any(
        p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        for p in DEMO_PHOTO_DIR.iterdir()
    )


requires_demo_photos = pytest.mark.skipif(
    not demo_images_present(),
    reason=(
        "data/demo/photos has no images (they are git-ignored); "
        "run scripts/prepare_demo_photos.py to copy the 159 demo photos"
    ),
)


# --- Helpers ----------------------------------------------------------------


@pytest.fixture
def connect_log(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record every socket connect address.

    Wraps the conftest guard (which raises on non-loopback addresses), so an
    attempt is recorded even if the caller swallows the guard's error.
    """
    log: list[object] = []
    guarded_connect = socket.socket.connect
    guarded_connect_ex = socket.socket.connect_ex

    def recording_connect(self: socket.socket, address: object) -> None:
        log.append(address)
        return guarded_connect(self, address)

    def recording_connect_ex(self: socket.socket, address: object) -> int:
        log.append(address)
        return guarded_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", recording_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", recording_connect_ex)
    return log


def make_app(db_path: Path, photo_dir: Path):
    config, issues = resolve_config(
        {"DATABASE_URL": f"sqlite:///{db_path}", "PHOTO_DIR": str(photo_dir)}
    )
    assert issues == []
    assert config.vision_provider == "fixture"
    assert config.weather_provider == "fixture"
    return create_app(config)


def file_name_of(client: TestClient, outfit_id: int) -> str:
    record = client.app.state.container.repository.get_outfit(outfit_id)
    assert record is not None
    return record.file_name


def stem_of(client: TestClient, outfit_id: int) -> str:
    return Path(file_name_of(client, outfit_id)).stem


def recommend(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/recommendations/today")
    assert response.status_code == 200, response.text
    return response.json()


def select_scenario(client: TestClient, scenario_id: str) -> None:
    response = client.put("/api/demo/scenarios/selected", json={"scenario_id": scenario_id})
    assert response.status_code == 200, response.text
    assert response.json()["selected_id"] == scenario_id


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


# Synthetic PHOTO_DIR: November/March mornings near 9 °C under the fixture
# seasonal formula, so the chilly-morning scenario (9.0 °C) has candidates.
SYNTHETIC_PHOTOS: list[tuple[str, str, str | None, ClothingTags | None]] = [
    ("a_coat", "2024-11-03T08:10:00+09:00", "comfortable",
     ClothingTags(upper_type="knit", outer_type="coat", lower_type="slacks",
                  colors=["navy", "cream"], layers_visible=2, confidence=0.9)),
    ("b_cardigan", "2024-11-12T13:20:00+09:00", None,
     ClothingTags(upper_type="shirt", outer_type="cardigan", lower_type="jeans",
                  colors=["blue"], layers_visible=2, confidence=0.8)),
    ("c_hoodie", "2024-03-21T15:40:00+09:00", "cold",
     ClothingTags(upper_type="hoodie", outer_type="none", lower_type="jeans",
                  colors=["gray"], layers_visible=1, confidence=0.7)),
    ("d_trench", "2024-11-25T11:05:00+09:00", "hot",
     ClothingTags(upper_type="blouse", outer_type="trench_coat", lower_type="skirt",
                  colors=["beige"], layers_visible=2, confidence=0.85)),
    ("e_untagged", "2024-03-09T14:30:00+00:00", None, None),  # not in fixture map
    ("f_parka", "2024-11-28T07:45:00+09:00", None,
     ClothingTags(upper_type="knit", outer_type="parka", lower_type="trousers",
                  colors=["black"], layers_visible=3, confidence=0.95)),
]


def build_synthetic_photo_dir(photo_dir: Path) -> dict[str, VisionAnalysis]:
    """Write synthetic photos + sidecars and return the vision fixture map."""
    photo_dir.mkdir()
    analyses_by_hash: dict[str, VisionAnalysis] = {}
    for index, (stem, captured_at, vote, tags) in enumerate(SYNTHETIC_PHOTOS):
        data = png_bytes((index * 40 % 256, 120, 200 - index * 20))
        (photo_dir / f"{stem}.png").write_bytes(data)
        sidecar: dict[str, Any] = {
            "captured_at": captured_at, "latitude": 37.5665, "longitude": 126.978,
        }
        if vote is not None:
            sidecar["thermal_vote"] = vote
        (photo_dir / f"{stem}.metadata.json").write_text(json.dumps(sidecar), encoding="utf-8")
        if tags is not None:
            analyses_by_hash[hashlib.sha256(data).hexdigest()] = VisionAnalysis(
                photo_type="outfit",
                photo_type_confidence=0.99,
                reason="A full outfit is visible.",
                tags=tags,
            )
    # One file that fails validation, so the failure path is part of the run too.
    (photo_dir / "z_broken.png").write_bytes(b"not an image")
    (photo_dir / "z_broken.metadata.json").write_text(
        json.dumps({"captured_at": "2024-11-03T09:00:00+09:00",
                    "latitude": 37.5665, "longitude": 126.978}),
        encoding="utf-8",
    )
    return analyses_by_hash


def run_demo_flow(client: TestClient, feedback_vote: str) -> dict[str, Any]:
    """Startup -> scan -> select scenario -> recommend -> feedback -> recommend.

    Returns everything that must be identical between two fresh databases.
    Outfits are keyed by file name; feedback ``created_at`` is excluded.
    """
    scan = client.post("/api/demo/scan")
    assert scan.status_code == 200, scan.text
    report = scan.json()

    outfits = client.get("/api/outfits")
    assert outfits.status_code == 200
    by_name = {
        file_name_of(client, o["id"]): {
            "captured_at": o["captured_at"],
            "tags": o["tags"],
            "weather": o["weather"],
            "effective_vote": o["effective_vote"],
            "vision_provider": o["vision_provider"],
        }
        for o in outfits.json()
    }
    order = [file_name_of(client, o["id"]) for o in outfits.json()]

    select_scenario(client, "chilly-morning")

    def ranking(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": body["status"],
            "fallback": body["fallback"],
            "applied_tolerance_c": body["applied_tolerance_c"],
            "today": body["today"],
            "items": [
                {
                    "file": file_name_of(client, item["outfit"]["id"]),
                    "rank": item["rank"],
                    "score": item["score"],
                    "temperature_diff_c": item["temperature_diff_c"],
                    "confidence_level": item["confidence_level"],
                    "selected_by_fallback": item["selected_by_fallback"],
                    "reasons": item["reasons"],
                    "explanation": item["explanation"],
                }
                for item in body["items"]
            ],
        }

    before = recommend(client)
    assert before["items"], "chilly-morning must yield at least one recommendation"
    top_id = before["items"][0]["outfit"]["id"]
    feedback = client.post(f"/api/outfits/{top_id}/feedback", json={"thermal_vote": feedback_vote})
    assert feedback.status_code == 201, feedback.text
    after = recommend(client)

    return {
        "report": report,
        "outfits": by_name,
        "order": order,
        "before": ranking(before),
        "feedback": {"file": file_name_of(client, top_id), "vote": feedback.json()["thermal_vote"]},
        "after": ranking(after),
    }


# --- Tests ------------------------------------------------------------------


@pytest.mark.parametrize(
    "photo_source",
    ["synthetic", pytest.param("demo", marks=requires_demo_photos)],
)
def test_fixture_mode_end_to_end_deterministic(
    tmp_path: Path, connect_log: list[object], photo_source: str
) -> None:
    """Two fresh databases give the same tags, weather and rankings (Req 15.1)."""
    if photo_source == "synthetic":
        photo_dir = tmp_path / "photos"
        analyses_by_hash = build_synthetic_photo_dir(photo_dir)
    else:
        photo_dir = DEMO_PHOTO_DIR
        analyses_by_hash = None

    results = []
    for run in ("first", "second"):
        app = make_app(tmp_path / f"{run}.db", photo_dir)
        if analyses_by_hash is not None:
            vision = FixtureVisionProvider(analyses_by_hash)
            app.dependency_overrides[get_vision_provider] = lambda vision=vision: vision
        with TestClient(app) as client:
            results.append(run_demo_flow(client, feedback_vote="cold"))

    first, second = results
    assert first == second

    # The run did real work, not an empty comparison.
    report = first["report"]
    assert report["succeeded"] > 0
    assert report["scanned"] == (
        report["succeeded"]
        + report["duplicate"]
        + report["skipped_non_outfit"]
        + report["uncertain"]
        + report["failed"]
    )
    assert all(o["weather"] is not None and o["weather"]["is_fixture"] for o in first["outfits"].values())
    assert first["before"]["today"]["provider"] == "scenario"
    assert first["after"] != first["before"]  # the feedback changed the ranking/explanation

    if photo_source == "synthetic":
        assert report == {
            "scanned": 7,
            "succeeded": 5,
            "duplicate": 0,
            "failed": 1,
            "skipped_non_outfit": 0,
            "uncertain": 1,
            "errors": [report["errors"][0]],
            "decisions": [report["decisions"][0]],
        }
        assert report["errors"][0]["file_name"] == "z_broken.png"
        assert report["errors"][0]["code"] == "INVALID_IMAGE_FORMAT"
        assert report["decisions"][0]["file_name"] == "e_untagged.png"
        assert report["decisions"][0]["photo_type"] == "uncertain"
        assert "e_untagged.png" not in first["outfits"]
        assert first["outfits"]["a_coat.png"]["tags"]["outer_type"] == "coat"
        assert first["outfits"]["c_hoodie.png"]["effective_vote"] == "cold"

    # Socket guard: no connection attempt left the machine.
    assert [a for a in connect_log if not _is_local_address(a)] == []


@requires_demo_photos
def test_demo_scenarios_cover_cases(tmp_path: Path, connect_log: list[object]) -> None:
    """The four demo scenarios give design.md's result table (Req 15.9)."""
    expected_total = len(confirmed_demo_photos())
    assert expected_total == 159

    with TestClient(make_app(tmp_path / "demo.db", DEMO_PHOTO_DIR)) as client:
        report = client.post("/api/demo/scan").json()
        assert report["scanned"] == expected_total, "data/demo/photos is incomplete"
        assert report["succeeded"] == 139
        assert report["skipped_non_outfit"] == 20
        assert report["uncertain"] == 0
        assert report["failed"] == 0 and report["errors"] == []
        assert len(report["decisions"]) == 20

        # 쌀쌀한 아침 9.0: 5 items within 3.0, no fallback, 081 first with 115.0.
        select_scenario(client, "chilly-morning")
        body = recommend(client)
        assert body["status"] == "ok"
        assert body["fallback"] is False
        assert body["applied_tolerance_c"] == 3.0
        assert body["today"]["actual_temperature_c"] == 9.0
        assert len(body["items"]) == 5
        assert all(i["temperature_diff_c"] <= 3.0 for i in body["items"])
        assert all(i["selected_by_fallback"] is False for i in body["items"])
        top = body["items"][0]
        assert stem_of(client, top["outfit"]["id"]) == "081_hiphop_varsity_baggy_denim"
        assert top["score"] == 115.0
        assert top["temperature_diff_c"] == 0.0
        assert all(i["score"] < 115.0 for i in body["items"][1:])  # unique top score

        # 기록적 한파 −12.0: only 076 (diff exactly 3.0); 071 (3.5) excluded.
        select_scenario(client, "cold-snap")
        body = recommend(client)
        assert body["status"] == "ok"
        assert body["fallback"] is False
        assert len(body["items"]) == 1
        only = body["items"][0]
        assert stem_of(client, only["outfit"]["id"]) == "076_winter_navy_long_parka"
        assert only["temperature_diff_c"] == 3.0
        assert only["selected_by_fallback"] is False
        assert only["confidence_level"] == "high"

        # 때 이른 무더위 30.0: nothing within 3.0, widened to 5.0, 5 items.
        select_scenario(client, "early-heat")
        body = recommend(client)
        assert body["status"] == "ok"
        assert body["fallback"] is True
        assert body["applied_tolerance_c"] == 5.0
        assert len(body["items"]) == 5
        for item in body["items"]:
            assert 3.5 <= item["temperature_diff_c"] <= 5.0
            assert item["selected_by_fallback"] is True
            assert item["confidence_level"] in ("medium", "low")
            assert "기온 허용 범위를 5.0°C로 확장" in item["explanation"]

        # 기록적 폭염 38.0: nothing within 5.0 either.
        select_scenario(client, "heatwave")
        body = recommend(client)
        assert body["status"] == "insufficient_data"
        assert body["fallback"] is True
        assert body["items"] == []
        assert body["today"]["actual_temperature_c"] == 38.0

    assert [a for a in connect_log if not _is_local_address(a)] == []


@requires_demo_photos
def test_five_minute_demo_walkthrough(tmp_path: Path, connect_log: list[object]) -> None:
    """README 5분 데모 절차 (Fixture 모드, API 키 없음). README(17.4)는 이 순서를 그대로 쓴다.

    1. 상태 확인: ``GET /api/demo/status`` → vision/weather 모두 ``fixture``,
       ``gemini_api_key_configured=false``, 착장 0건.
    2. 폴더 스캔: ``POST /api/demo/scan`` → scanned 159, succeeded 139,
       skipped_non_outfit 20, duplicate 0, failed 0.
    3. 기록 화면: ``GET /api/outfits`` → 착장 사진 139건, 촬영 시각 최신순.
       첫 항목의 ``GET /api/outfits/{id}/photo`` → 200 이미지.
    4. 시나리오 선택: ``GET /api/demo/scenarios`` → 4개.
       ``PUT /api/demo/scenarios/selected`` ``{"scenario_id": "chilly-morning"}``.
    5. 오늘 추천: ``GET /api/recommendations/today`` → 오늘 9.0°C(시나리오),
       1위 ``081_hiphop_varsity_baggy_denim`` 115.0점("과거에 적당했음").
    6. 체감 피드백: 1위 착장에 ``POST /api/outfits/{id}/feedback``
       ``{"thermal_vote": "hot"}`` (알림 카드의 🥵 더웠어) → 201.
    7. 추천 재조회: ``GET /api/recommendations/today`` → 081은 85.0점으로 떨어져
       상위 5건에서 빠지고, 1위가 ``052_burgundy_sweater_skirt_full``(110.0점)로 바뀐다.
       ``GET /api/outfits/{id}``의 ``effective_vote``는 ``hot``.
    8. 재스캔: ``POST /api/demo/scan`` → duplicate 139, skipped_non_outfit 20,
       succeeded 0, 착장 수 그대로.
    """
    with TestClient(make_app(tmp_path / "walkthrough.db", DEMO_PHOTO_DIR)) as client:
        # 1. 상태 확인
        status = client.get("/api/demo/status").json()
        assert status["vision_provider"] == "fixture"
        assert status["weather_provider"] == "fixture"
        assert status["gemini_api_key_configured"] is False
        assert status["outfit_count"] == 0

        # 2. 폴더 스캔
        report = client.post("/api/demo/scan").json()
        assert {
            k: report[k]
            for k in (
                "scanned", "succeeded", "duplicate", "failed",
                "skipped_non_outfit", "uncertain",
            )
        } == {
            "scanned": 159,
            "succeeded": 139,
            "duplicate": 0,
            "failed": 0,
            "skipped_non_outfit": 20,
            "uncertain": 0,
        }

        # 3. 기록 화면
        outfits = client.get("/api/outfits").json()
        assert len(outfits) == 139
        instants = [datetime.fromisoformat(o["captured_at"]) for o in outfits]
        assert instants == sorted(instants, reverse=True)
        photo = client.get(outfits[0]["photo_url"])
        assert photo.status_code == 200
        assert photo.headers["content-type"].startswith("image/")

        # 4. 시나리오 선택
        scenarios = client.get("/api/demo/scenarios").json()
        assert [s["id"] for s in scenarios["scenarios"]] == [
            "chilly-morning", "cold-snap", "early-heat", "heatwave",
        ]
        assert scenarios["selected_id"] is None
        select_scenario(client, "chilly-morning")

        # 5. 오늘 추천
        body = recommend(client)
        assert body["today"]["provider"] == "scenario"
        assert body["today"]["scenario_id"] == "chilly-morning"
        assert body["today"]["actual_temperature_c"] == 9.0
        top = body["items"][0]
        top_id = top["outfit"]["id"]
        assert stem_of(client, top_id) == "081_hiphop_varsity_baggy_denim"
        assert top["score"] == 115.0
        assert top["outfit"]["effective_vote"] == "comfortable"
        assert "과거에 적당했음" in top["explanation"]

        # 6. 체감 피드백 (🥵 더웠어)
        feedback = client.post(f"/api/outfits/{top_id}/feedback", json={"thermal_vote": "hot"})
        assert feedback.status_code == 201
        assert feedback.json()["outfit_id"] == top_id
        assert feedback.json()["thermal_vote"] == "hot"

        # 7. 추천 재조회
        body = recommend(client)
        assert top_id not in [i["outfit"]["id"] for i in body["items"]]
        new_top = body["items"][0]
        assert stem_of(client, new_top["outfit"]["id"]) == "052_burgundy_sweater_skirt_full"
        assert new_top["score"] == 110.0
        assert len(body["items"]) == 5
        assert all(i["score"] > 85.0 for i in body["items"])  # 081 now scores 85.0
        detail = client.get(f"/api/outfits/{top_id}").json()
        assert detail["effective_vote"] == "hot"

        # 8. 재스캔
        report = client.post("/api/demo/scan").json()
        assert {
            k: report[k]
            for k in (
                "scanned", "succeeded", "duplicate", "failed",
                "skipped_non_outfit", "uncertain",
            )
        } == {
            "scanned": 159,
            "succeeded": 0,
            "duplicate": 139,
            "failed": 0,
            "skipped_non_outfit": 20,
            "uncertain": 0,
        }
        assert client.get("/api/demo/status").json()["outfit_count"] == 139

    assert [a for a in connect_log if not _is_local_address(a)] == []
