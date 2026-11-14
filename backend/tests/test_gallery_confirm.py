"""Uncertain photo "착장 맞음" confirmation (tasks 3.20, Req 6.12–6.15, area (m))."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import AutomationConfig, resolve_config
from app.db import Outfit, PhotoAnalysisRow, WeatherSnapshot
from app.db_automation import PhotoConfirmation
from app.domain import UNKNOWN_ANALYSIS, ClothingTags, VisionAnalysis, WeatherContext
from app.gallery.confirm import CONFIRMED_REASON, ConfirmOutcome, confirmed_analysis
from app.gallery.store import GalleryStore
from app.main import create_app
from app.recommendation.factory import build_strategy
from app.recommendation.rule_based_v1 import is_non_outfit
from app.recommendation.service import RecommendationService
from tests.gallery_support import KNIT_TAGS, LOW_OUTFIT, OUTFIT, FakeVision, GalleryEnv, png_bytes


class App:
    def __init__(self, tmp_path: Path, vision: FakeVision) -> None:
        config, _ = resolve_config({"DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
                                    "PHOTO_DIR": str(tmp_path / "demo")})
        config = replace(config, automation=AutomationConfig(
            gallery_dir=tmp_path / "gallery", user_photo_dir=tmp_path / "user" / "photos"))
        self.app = create_app(config)
        container = self.app.state.container
        self.env = GalleryEnv(tmp_path, vision, engine=container.engine)
        container.gallery_store = self.env.store
        container.outfit_confirm = self.env.confirm
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def uncertain(self, name: str, data: bytes) -> int:
        """Import ``name`` through the watcher; it must end ``uncertain``."""
        if not self.env.store.baseline_done(self.env.watcher.key):
            self.env.poll()
        self.env.add(name, data)
        self.env.poll(2)
        row = self.env.row(name)
        assert row.status == "uncertain", row
        return row.id

    def confirm(self, row_id: int):  # noqa: ANN201
        return self.client.post(f"/api/gallery/imports/{row_id}/confirm-outfit")

    def count(self, model) -> int:  # noqa: ANN001
        with Session(self.env.engine) as session:
            return session.exec(select(func.count()).select_from(model)).one()


@pytest.fixture
def app(tmp_path: Path):
    instance = App(tmp_path, FakeVision(LOW_OUTFIT))  # 0.69 → uncertain, tags kept
    with instance.client:
        yield instance


def test_confirm_uncertain_creates_outfit_with_audit(app: App) -> None:
    data = png_bytes((1, 2, 3))
    row_id = app.uncertain("maybe.png", data)
    sha = hashlib.sha256(data).hexdigest()
    assert app.env.user_files() == []  # the uncertain copy was removed (Req 3.4)

    response = app.confirm(row_id)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "imported" and body["outfit_id"] is not None
    assert (body["photo_type"], body["reason"], body["capture_origin"]) == ("outfit", CONFIRMED_REASON, "estimated")

    with Session(app.env.engine) as session:
        analysis = session.exec(select(PhotoAnalysisRow).where(PhotoAnalysisRow.sha256 == sha)).one()
        assert analysis.photo_type == "outfit"
        assert VisionAnalysis.model_validate_json(analysis.analysis_json).reason == CONFIRMED_REASON
        audit = session.exec(select(PhotoConfirmation)).one()
        assert (audit.sha256, audit.gallery_file_id, audit.outfit_id, audit.source) == (
            sha, row_id, body["outfit_id"], "user_action")
        assert audit.previous_photo_type == "uncertain" and audit.previous_confidence == 0.69
        assert audit.previous_reason.startswith("Photo-type confidence 0.69")
    record = app.env.repo.get_outfit(body["outfit_id"])
    assert record.tags == KNIT_TAGS and record.source == "demo_sidecar"
    assert app.count(WeatherSnapshot) == 1
    photo = app.client.get(f"/api/outfits/{body['outfit_id']}/photo")
    assert photo.status_code == 200 and photo.content == data
    assert app.env.store.user_photo_file(sha) == "maybe.png"


def test_confirm_does_not_call_vision(app: App) -> None:
    row_id = app.uncertain("x.png", png_bytes((4, 5, 6)))
    calls = app.env.vision.calls
    app.env.vision.default = RuntimeError("Vision must not be called")
    assert app.confirm(row_id).status_code == 200
    assert app.env.vision.calls == calls


def test_confirm_twice_conflict(app: App) -> None:
    row_id = app.uncertain("x.png", png_bytes((7, 8, 9)))
    assert app.confirm(row_id).status_code == 200
    again = app.confirm(row_id)
    assert again.status_code == 409
    assert again.json()["error"] == {
        "code": "GALLERY_IMPORT_NOT_CONFIRMABLE", "message": "이 사진은 착장으로 확정할 수 없는 상태입니다."}
    assert app.count(Outfit) == 1 and app.count(PhotoConfirmation) == 1
    missing = app.confirm(999)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "GALLERY_IMPORT_NOT_FOUND"


def test_confirm_other_states_conflict(tmp_path: Path) -> None:
    instance = App(tmp_path, FakeVision(OUTFIT))
    with instance.client:
        instance.env.poll()
        instance.env.add("ok.png", png_bytes((10, 11, 12)))
        instance.env.poll(2)
        row = instance.env.row("ok.png")
        assert row.status == "imported"
        assert instance.confirm(row.id).status_code == 409


def test_confirm_source_missing_or_changed_409(app: App) -> None:
    gone = app.uncertain("gone.png", png_bytes((13, 14, 15)))
    (app.env.gallery / "gone.png").unlink()
    response = app.confirm(gone)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GALLERY_IMPORT_NOT_CONFIRMABLE"
    assert "원본 사진을 찾을 수 없습니다" in response.json()["error"]["message"]
    assert app.env.store.get(gone).status == "uncertain"

    changed = app.uncertain("changed.png", png_bytes((16, 17, 18)))
    (app.env.gallery / "changed.png").write_bytes(png_bytes((19, 20, 21)))
    response = app.confirm(changed)
    assert response.status_code == 409 and "원본 사진" in response.json()["error"]["message"]
    assert app.env.store.get(changed).status == "uncertain"
    assert app.count(Outfit) == 0 and app.count(PhotoConfirmation) == 0
    assert app.env.user_files() == []


def test_confirm_storage_failure_rolls_back_all(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    data = png_bytes((22, 23, 24))
    row_id = app.uncertain("x.png", data)

    def fail_inside_transaction(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("disk full")

    monkeypatch.setattr(GalleryStore, "mark_confirmed_in", staticmethod(fail_inside_transaction))
    response = app.confirm(row_id)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "STORAGE_ERROR"
    assert app.count(Outfit) == 0 and app.count(WeatherSnapshot) == 0 and app.count(PhotoConfirmation) == 0
    with Session(app.env.engine) as session:
        analysis = session.exec(select(PhotoAnalysisRow)).one()
        assert analysis.photo_type == "uncertain"  # the decision change rolled back too
    row = app.env.store.get(row_id)
    assert row.status == "uncertain" and row.outfit_id is None
    assert app.env.user_files() == [] and app.env.store.user_photo_file(hashlib.sha256(data).hexdigest()) is None

    monkeypatch.undo()
    assert app.confirm(row_id).status_code == 200  # the record can still be confirmed


def test_confirmed_unknown_tags_listed_not_recommended(tmp_path: Path) -> None:
    instance = App(tmp_path, FakeVision(UNKNOWN_ANALYSIS))
    with instance.client:
        unknown_id = instance.uncertain("blank.png", png_bytes((25, 26, 27)))
        body = instance.confirm(unknown_id).json()
        tagged = LOW_OUTFIT.model_copy(update={"photo_type": "uncertain"})
        tagged_bytes = png_bytes((28, 29, 30))
        instance.env.vision.by_hash[hashlib.sha256(tagged_bytes).hexdigest()] = tagged
        tagged_id = instance.uncertain("tagged.png", tagged_bytes)
        tagged_body = instance.confirm(tagged_id).json()

        listed = instance.client.get("/api/outfits").json()
        by_id = {item["id"]: item for item in listed}
        assert set(by_id) == {body["outfit_id"], tagged_body["outfit_id"]}  # both listed (Req 6.15)
        tags = ClothingTags.model_validate(by_id[body["outfit_id"]]["tags"])
        assert is_non_outfit(tags) and tags.confidence == 0.0

        record = instance.env.repo.get_outfit(tagged_body["outfit_id"])
        today = WeatherContext(
            actual_temperature_c=record.weather.actual_temperature_c, apparent_temperature_c=None,
            relative_humidity_pct=None, wind_speed_kmh=None, precipitation_mm=None, observed_at=None,
            provider="scenario", is_fixture=True, scenario_id="t", scenario_name="t",
        )
        service = RecommendationService(None, instance.env.repo, build_strategy(tolerance_c=Decimal("3.0")))  # type: ignore[arg-type]
        items = service.recommendations_for(today).items
        assert [item.outfit.id for item in items] == [tagged_body["outfit_id"]]


def test_confirmed_analysis_rules() -> None:
    stored = LOW_OUTFIT.model_copy(update={"photo_type": "uncertain", "reason": "dim"})
    result = confirmed_analysis(stored)
    assert (result.photo_type, result.photo_type_confidence, result.reason, result.tags) == (
        "outfit", 0.69, CONFIRMED_REASON, KNIT_TAGS)
    assert confirmed_analysis(None).tags == UNKNOWN_ANALYSIS.tags
    assert confirmed_analysis(None).photo_type_confidence == 0.0
    # All-unknown garment types are stored with tag confidence 0 (Req 6.15, client "no tags" notice).
    vague = ClothingTags(upper_type="unknown", outer_type="unknown", lower_type="unknown",
                         colors=["grey"], layers_visible=1, confidence=0.4)
    result = confirmed_analysis(UNKNOWN_ANALYSIS.model_copy(update={"tags": vague, "photo_type_confidence": 0.3}))
    assert result.tags.confidence == 0.0 and is_non_outfit(result.tags)
    assert result.photo_type_confidence == 0.3


def test_service_outcomes_without_api(app: App) -> None:
    row_id = app.uncertain("svc.png", png_bytes((31, 32, 33)))
    outcome, row = app.env.confirm.confirm(row_id)
    assert outcome is ConfirmOutcome.CONFIRMED and row.status == "imported"
    assert app.env.confirm.confirm(row_id)[0] is ConfirmOutcome.NOT_CONFIRMABLE
    assert app.env.confirm.confirm(12345) == (ConfirmOutcome.NOT_FOUND, None)


def test_route_answers_501_until_wired(tmp_path: Path) -> None:
    config, _ = resolve_config({"DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}", "PHOTO_DIR": str(tmp_path / "p")})
    with TestClient(create_app(config), raise_server_exceptions=False) as client:
        assert client.post("/api/gallery/imports/1/confirm-outfit").status_code == 501
