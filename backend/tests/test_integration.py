"""Fixture-mode integration tests (task 17.1, Req 15.1, 15.9, 17.2, 17.5).

Every app is built with ``create_app(config=resolve_config({...}))`` and a
fresh SQLite file under ``tmp_path``, so the repository ``.env`` and
``data/weatherwear.db`` are never touched. Providers are the fixture ones
(the defaults), so no API key or network is needed.

Tests that use the real demo images (``data/demo/photos/*.png|*.jpg``) skip
when those images are absent: the images are git-ignored and are copied by
``scripts/prepare_demo_photos.py``. The sidecars and fixtures are tracked.

Expected values for the demo photos come from the data files, not from
literals: photo counts from ``photos.manifest.json``, scan counts from the
``vision.json`` photo types (``first_scan_counts``), scenario temperatures
from ``scenarios.json`` and rankings from ``scenario_expectations.json``
(written by ``scripts/select_scenarios.py``). The S1~S4 conditions of design.md
"시나리오별 기대 결과" are checked directly as well, because the expectations
file is produced by the same strategy code.
"""

from __future__ import annotations

import hashlib
import io
import json
import socket
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import resolve_config
from app.demo_scenarios import S1_ID, S2_ID, S3_ID, S4_ID, SCENARIO_IDS, v2_photos
from app.deps import get_vision_provider
from app.domain import ClothingTags, VisionAnalysis
from app.main import create_app
from app.providers.vision.fixture import FixtureVisionProvider
from app.recommendation.rule_based_v1 import score_for
from tests.conftest import _is_local_address
from tests.test_demo_data import (
    DEMO_PHOTO_DIR,
    confirmed_demo_photos,
    demo_outfit_photos,
    first_scan_counts,
    load_demo_records,
    not_stored_files,
    requires_demo_photos,
    rescan_counts,
    scenario_expectations,
    scenario_temperatures,
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


def recommend(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/recommendations/today")
    assert response.status_code == 200, response.text
    return response.json()


def select_scenario(client: TestClient, scenario_id: str) -> None:
    response = client.put("/api/demo/scenarios/selected", json={"scenario_id": scenario_id})
    assert response.status_code == 200, response.text
    assert response.json()["selected_id"] == scenario_id


def scenario_outcome(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    """A recommendation response in the shape of a ``scenario_expectations.json`` entry.

    Items are identified by file name, because outfit ids depend on the database.
    """
    items = [
        {
            "rank": item["rank"],
            "file": file_name_of(client, item["outfit"]["id"]),
            "past_temperature_c": item["outfit"]["weather"]["actual_temperature_c"],
            "temperature_diff_c": item["temperature_diff_c"],
            "score": item["score"],
            "effective_vote": item["outfit"]["effective_vote"],
            "confidence_level": item["confidence_level"],
            "selected_by_fallback": item["selected_by_fallback"],
        }
        for item in body["items"]
    ]
    return {
        "id": body["today"]["scenario_id"],
        "name": body["today"]["scenario_name"],
        "actual_temperature_c": body["today"]["actual_temperature_c"],
        "status": body["status"],
        "fallback": body["fallback"],
        "applied_tolerance_c": body["applied_tolerance_c"],
        "count": len(items),
        "top_file": items[0]["file"] if items else None,
        "items": items,
    }


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


# Synthetic PHOTO_DIR: sidecar capture times with no weather.json entry, so the
# fixture provider uses its seasonal formula: the tagged photos get 5.5~11.0 °C.
# Every S1 candidate (5.0~15.0 °C, design "선택 방법") is within the 5.0 °C
# fallback range of at least one of them, so chilly-morning has candidates
# whatever temperature scripts/select_scenarios.py chose.
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
    assert first["before"]["today"]["actual_temperature_c"] == scenario_temperatures()[S1_ID]
    assert first["after"] != first["before"]  # the feedback changed the ranking/explanation

    if photo_source == "demo":
        counts = first_scan_counts()
        assert counts["scanned"] == len(confirmed_demo_photos())
        assert report == {**counts, "errors": [], "decisions": report["decisions"]}
        assert sorted(d["file_name"] for d in report["decisions"]) == sorted(not_stored_files(load_demo_records()))
        assert len(first["outfits"]) == counts["succeeded"]
        expected = scenario_expectations()[S1_ID]
        assert [(i["rank"], i["file"], i["score"]) for i in first["before"]["items"]] == [
            (i["rank"], i["file"], i["score"]) for i in expected["items"]
        ]

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
    """The four demo scenarios give design.md's "시나리오별 기대 결과" (Req 15.9, 15.13).

    Each scenario's response equals its ``scenario_expectations.json`` entry
    (status, fallback, applied tolerance, count and every ranked item by file
    name), and the S1~S4 conditions are checked on the response directly.
    """
    expected_total = len(confirmed_demo_photos())
    expectations = scenario_expectations()
    temperatures = scenario_temperatures()
    assert list(expectations) == list(temperatures) == list(SCENARIO_IDS)

    photos = demo_outfit_photos()
    not_stored = {p.file for p in photos if not p.stored_as_outfit}
    assert not_stored, "the demo data has Non_Outfit_Photo (Req 15.13)"
    counts = first_scan_counts()
    # V2 seed votes: the closest S1 photo is cold, the second closest comfortable.
    v2_cold, v2_comfortable = (p.file for p in v2_photos(photos, temperatures[S1_ID])[:2])

    bodies: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    with TestClient(make_app(tmp_path / "demo.db", DEMO_PHOTO_DIR)) as client:
        report = client.post("/api/demo/scan").json()
        assert report["scanned"] == expected_total, "data/demo/photos is incomplete"
        assert {key: report[key] for key in counts} == counts
        assert report["errors"] == []
        # The gate reports every photo it does not store, and nothing else.
        assert sorted(d["file_name"] for d in report["decisions"]) == sorted(not_stored)

        for scenario_id in SCENARIO_IDS:
            select_scenario(client, scenario_id)
            bodies[scenario_id] = recommend(client)
            outcomes[scenario_id] = scenario_outcome(client, bodies[scenario_id])

    # Regression: the real scan gives what select_scenarios.py recorded.
    for scenario_id in SCENARIO_IDS:
        assert outcomes[scenario_id] == expectations[scenario_id], scenario_id

    for scenario_id, outcome in outcomes.items():
        assert outcome["actual_temperature_c"] == temperatures[scenario_id]
        assert {i["file"] for i in outcome["items"]}.isdisjoint(not_stored), scenario_id  # Req 6.10

    # S1 쌀쌀한 아침: 5 normal results within 3.0, V2 comfortable above V2 cold.
    s1 = outcomes[S1_ID]
    assert s1["status"] == "ok"
    assert s1["fallback"] is False
    assert s1["applied_tolerance_c"] == 3.0
    assert s1["count"] == 5
    assert all(i["temperature_diff_c"] <= 3.0 for i in s1["items"])
    assert all(i["selected_by_fallback"] is False for i in s1["items"])
    ranks = {i["file"]: i["rank"] for i in s1["items"]}
    assert v2_comfortable in ranks
    assert ranks[v2_comfortable] < ranks.get(v2_cold, len(ranks) + 1)

    # S2 기록적 한파: exactly one normal result at the 3.0 boundary, voted (V3).
    s2 = outcomes[S2_ID]
    assert s2["status"] == "ok"
    assert s2["fallback"] is False
    assert s2["applied_tolerance_c"] == 3.0
    assert s2["count"] == 1
    (only,) = s2["items"]
    assert only["temperature_diff_c"] == 3.0
    assert only["selected_by_fallback"] is False
    assert only["confidence_level"] == "high"

    # S3 무더위: nothing within 3.0, widened to 5.0, 1~5 results 4.0~5.0 away.
    s3 = outcomes[S3_ID]
    assert s3["status"] == "ok"
    assert s3["fallback"] is True
    assert s3["applied_tolerance_c"] == 5.0
    assert 1 <= s3["count"] <= 5
    for item in s3["items"]:
        assert 4.0 <= item["temperature_diff_c"] <= 5.0
        assert item["selected_by_fallback"] is True
        assert item["confidence_level"] in ("medium", "low")
    assert all("기온 허용 범위를 5.0°C로 확장" in i["explanation"] for i in bodies[S3_ID]["items"])

    # S4 기록적 폭염: nothing within 5.0 either.
    s4 = outcomes[S4_ID]
    assert s4["status"] == "insufficient_data"
    assert s4["fallback"] is True
    assert s4["items"] == []

    assert [a for a in connect_log if not _is_local_address(a)] == []


@requires_demo_photos
def test_five_minute_demo_walkthrough(tmp_path: Path, connect_log: list[object]) -> None:
    """README 5분 데모 절차 (Fixture 모드, API 키 없음). README(17.4)는 이 순서를 그대로 쓴다.

    값은 데이터 파일에서 온다: 사진 수 N은 ``photos.manifest.json``의 확인된 사진 수,
    착장 사진 수 O와 착장 아닌 사진 수 X는 ``vision.json``의 ``photo_type``(수집 게이트 적용),
    시나리오 기온은 ``scenarios.json``, 순위는 ``scenario_expectations.json``
    (2026-11-14 데이터: N=174, O=154, X=20, 14.5°C, 1위 ``015_loose_hair_front_cream`` 114.0점,
    피드백 뒤 84.0점, 새 1위 ``089_quiet_luxury_camel_blazer`` 114.0점).

    1. 상태 확인: ``GET /api/demo/status`` → vision/weather 모두 ``fixture``,
       ``gemini_api_key_configured=false``, 착장 0건.
    2. 폴더 스캔: ``POST /api/demo/scan`` → scanned N, succeeded O,
       skipped_non_outfit X, uncertain 0, duplicate 0, failed 0.
    3. 기록 화면: ``GET /api/outfits`` → 착장 사진 O건, 촬영 시각 최신순.
       첫 항목의 ``GET /api/outfits/{id}/photo`` → 200 이미지.
    4. 시나리오 선택: ``GET /api/demo/scenarios`` → 4개.
       ``PUT /api/demo/scenarios/selected`` ``{"scenario_id": "chilly-morning"}``.
    5. 오늘 추천: ``GET /api/recommendations/today`` → 오늘 기온은 chilly-morning 기온,
       1위는 기대 결과의 1위("과거에 적당했음").
    6. 체감 피드백: 1위 착장에 ``POST /api/outfits/{id}/feedback``
       ``{"thermal_vote": "hot"}`` (알림 카드의 🥵 더웠어) → 201.
    7. 추천 재조회: ``GET /api/recommendations/today`` → 그 착장은 +15 대신 −15를 받아
       상위 5건에서 빠지고, 기대 결과의 2위가 1위가 된다.
       ``GET /api/outfits/{id}``의 ``effective_vote``는 ``hot``.
    8. 재스캔: ``POST /api/demo/scan`` → duplicate O, skipped_non_outfit X,
       succeeded 0, 착장 수 그대로.
    """
    total = len(confirmed_demo_photos())
    first_scan, second_scan = first_scan_counts(), rescan_counts()
    assert first_scan["scanned"] == total
    stored = first_scan["succeeded"]
    expected = scenario_expectations()[S1_ID]
    expected_top, expected_next = expected["items"][0], expected["items"][1]
    # The demo story needs a top outfit that felt right before (V2 comfortable).
    assert expected_top["effective_vote"] == "comfortable"
    demoted_score = float(score_for(Decimal(str(expected_top["temperature_diff_c"])), "hot"))

    with TestClient(make_app(tmp_path / "walkthrough.db", DEMO_PHOTO_DIR)) as client:
        # 1. 상태 확인
        status = client.get("/api/demo/status").json()
        assert status["vision_provider"] == "fixture"
        assert status["weather_provider"] == "fixture"
        assert status["gemini_api_key_configured"] is False
        assert status["outfit_count"] == 0

        # 2. 폴더 스캔
        report = client.post("/api/demo/scan").json()
        assert {k: report[k] for k in first_scan} == first_scan

        # 3. 기록 화면
        outfits = client.get("/api/outfits").json()
        assert len(outfits) == stored
        instants = [datetime.fromisoformat(o["captured_at"]) for o in outfits]
        assert instants == sorted(instants, reverse=True)
        photo = client.get(outfits[0]["photo_url"])
        assert photo.status_code == 200
        assert photo.headers["content-type"].startswith("image/")

        # 4. 시나리오 선택
        scenarios = client.get("/api/demo/scenarios").json()
        assert [s["id"] for s in scenarios["scenarios"]] == list(SCENARIO_IDS)
        assert scenarios["selected_id"] is None
        select_scenario(client, S1_ID)

        # 5. 오늘 추천
        body = recommend(client)
        assert body["today"]["provider"] == "scenario"
        assert body["today"]["scenario_id"] == S1_ID
        assert body["today"]["actual_temperature_c"] == scenario_temperatures()[S1_ID]
        top = body["items"][0]
        top_id = top["outfit"]["id"]
        assert file_name_of(client, top_id) == expected_top["file"]
        assert top["score"] == expected_top["score"]
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
        assert file_name_of(client, new_top["outfit"]["id"]) == expected_next["file"]
        assert new_top["score"] == expected_next["score"]
        assert len(body["items"]) == 5
        assert all(i["score"] > demoted_score for i in body["items"])  # the old top fell out
        detail = client.get(f"/api/outfits/{top_id}").json()
        assert detail["effective_vote"] == "hot"

        # 8. 재스캔
        report = client.post("/api/demo/scan").json()
        assert {k: report[k] for k in second_scan} == second_scan
        assert client.get("/api/demo/status").json()["outfit_count"] == stored

    assert [a for a in connect_log if not _is_local_address(a)] == []
