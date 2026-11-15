"""Gallery import record API (tasks 3.16, Req 17.3, 6.8, 5.5, 17.2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AutomationConfig, resolve_config
from app.gallery.rules import Signature, StableEvent
from app.gallery.store import GalleryStore, ImportOutcome
from app.main import create_app
from app.schemas import ScanDecision
from tests.gallery_support import T0

KEY = "g" * 16


@pytest.fixture
def client(tmp_path: Path):
    config, _ = resolve_config({"DATABASE_URL": f"sqlite:///{tmp_path / 'api.db'}", "PHOTO_DIR": str(tmp_path / "photos")})
    config = replace(config, automation=AutomationConfig(
        gallery_dir=tmp_path / "secret-gallery", user_photo_dir=tmp_path / "user" / "photos"))
    app = create_app(config)
    app.state.container.gallery_store = GalleryStore(app.state.container.engine)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def store_of(client: TestClient) -> GalleryStore:
    return client.app.state.container.gallery_store


def add(store: GalleryStore, name: str, outcome: ImportOutcome | None, at_s: int) -> int:
    now = T0 + timedelta(seconds=at_s)
    row = store.upsert_version(KEY, StableEvent(name, Signature(10, at_s), now), now)
    if outcome is not None:
        assert store.claim(row.id, now)
        store.finish(row.id, outcome, now)
    return row.id


def decision(name: str, photo_type: str = "uncertain") -> ScanDecision:
    return ScanDecision(file_name=name, photo_type=photo_type, confidence=0.4, reason="Hard to tell.")


@pytest.fixture
def seeded(client: TestClient) -> dict[str, int]:
    store = store_of(client)
    store.record_baseline(KEY, {"base.jpg": Signature(1, 1)}, T0)
    return {
        "imported": add(store, "imp.jpg", ImportOutcome(status="imported", sha256="a" * 64, outfit_id=None,
                                                        capture_origin="exif", accepted_confidence=0.95,
                                                        accepted_reason="ok"), 1),
        "uncertain": add(store, "unc.jpg", ImportOutcome(status="uncertain", sha256="b" * 64,
                                                         decision=decision("unc.jpg"), capture_origin="estimated"), 2),
        "failed": add(store, "fail.jpg", ImportOutcome(status="failed", stage="analysis", code="VISION_TIMEOUT"), 3),
        "skipped": add(store, "food.jpg", ImportOutcome(status="skipped_non_outfit", sha256="c" * 64,
                                                        decision=ScanDecision(file_name="food.jpg",
                                                                              photo_type="non_outfit",
                                                                              confidence=0.9, reason="Food."),
                                                        capture_origin="estimated"), 4),
        "pending": add(store, "new.jpg", None, 5),
    }


def test_gallery_imports_filters_and_order(client: TestClient, seeded: dict[str, int]) -> None:
    body = client.get("/api/gallery/imports").json()
    assert [item["file_name"] for item in body] == ["new.jpg", "food.jpg", "fail.jpg", "unc.jpg", "imp.jpg"]
    assert all(item["status"] != "baseline" for item in body)

    uncertain = client.get("/api/gallery/imports", params={"status": "uncertain"}).json()
    assert [(i["id"], i["photo_type"], i["photo_type_confidence"], i["reason"]) for i in uncertain] == [
        (seeded["uncertain"], "uncertain", 0.4, "Hard to tell.")]
    limited = client.get("/api/gallery/imports", params={"limit": 2}).json()
    assert [item["file_name"] for item in limited] == ["new.jpg", "food.jpg"]
    failed = client.get("/api/gallery/imports", params={"status": "failed"}).json()[0]
    assert (failed["last_error_stage"], failed["last_error_code"], failed["attempts"]) == (
        "analysis", "VISION_TIMEOUT", 1)
    for bad in ({"limit": 0}, {"limit": 101}, {"status": "baseline"}, {"status": "nope"}):
        response = client.get("/api/gallery/imports", params=bad)
        assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_imports_list_marks_estimated(client: TestClient, seeded: dict[str, int]) -> None:
    by_name = {item["file_name"]: item for item in client.get("/api/gallery/imports").json()}
    assert by_name["unc.jpg"]["capture_origin"] == "estimated"
    assert by_name["imp.jpg"]["capture_origin"] == "exif"
    assert by_name["imp.jpg"]["photo_type"] == "outfit"
    assert by_name["new.jpg"]["capture_origin"] is None


def test_reanalyze_uncertain_and_failed_only(client: TestClient, seeded: dict[str, int]) -> None:
    for key in ("uncertain", "failed"):
        response = client.post(f"/api/gallery/imports/{seeded[key]}/reanalyze")
        assert response.status_code == 202, key
        body = response.json()
        assert (body["status"], body["attempts"], body["last_error_code"]) == ("pending", 0, None)
    for key in ("imported", "skipped", "pending"):
        response = client.post(f"/api/gallery/imports/{seeded[key]}/reanalyze")
        assert response.status_code == 409, key
        assert response.json()["error"] == {
            "code": "GALLERY_IMPORT_NOT_REANALYZABLE", "message": "이 사진은 다시 분석할 수 없는 상태입니다."}
    missing = client.post("/api/gallery/imports/999/reanalyze")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "GALLERY_IMPORT_NOT_FOUND"
    # The baseline row is not reanalyzable either.
    baseline_id = 1
    assert store_of(client).get(baseline_id).status == "baseline"
    assert client.post(f"/api/gallery/imports/{baseline_id}/reanalyze").status_code == 409


def test_imports_response_has_no_paths(client: TestClient, seeded: dict[str, int], tmp_path: Path) -> None:
    text = client.get("/api/gallery/imports").text
    assert str(tmp_path) not in text and "secret-gallery" not in text
    keys = set(client.get("/api/gallery/imports").json()[0])
    assert keys == {
        "id", "file_name", "status", "attempts", "last_error_stage", "last_error_code", "next_attempt_at",
        "outfit_id", "photo_type", "photo_type_confidence", "reason", "capture_origin", "first_seen_at",
        "updated_at",
    }
    item = client.get("/api/gallery/imports").json()[0]
    assert datetime.fromisoformat(item["first_seen_at"]).utcoffset() == timedelta(0)  # offset always present


def test_routes_answer_501_until_store_is_wired(tmp_path: Path) -> None:
    config, _ = resolve_config({"DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}", "PHOTO_DIR": str(tmp_path / "photos")})
    app = create_app(config)
    app.state.container.gallery_store = None  # a container without the gallery runtime (7.1 wires it)
    with TestClient(app, raise_server_exceptions=False) as plain:
        assert plain.get("/api/gallery/imports").status_code == 501
        assert plain.get("/api/gallery/imports?limit=0").status_code == 422  # validation first
        assert plain.post("/api/gallery/imports/1/reanalyze").status_code == 501
