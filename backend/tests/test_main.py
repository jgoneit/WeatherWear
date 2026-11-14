"""App factory, dependency injection and common error handlers (task 10.1).

Configs are built with ``resolve_config({...})`` or monkeypatched process env
with ``env_file=None``; the real ``.env`` is never read.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect

import app.main as main_module
from app.config import AppConfig, resolve_config
from app.deps import (
    ApiError,
    get_recommendation_service,
    get_scan_lock,
    get_strategy,
    scan_lock,
)
from app.main import REPO_ROOT, create_app, resolve_paths
from app.recommendation.base import MissingTodayTemperature, RecommendationOutcome
from app.recommendation.service import RecommendationService
from app.schemas import FeedbackIn

ALLOWED_ORIGIN = "http://localhost:5173"


def make_config(tmp_path: Path, **overrides: str) -> AppConfig:
    raw = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        "CORS_ORIGINS": ALLOWED_ORIGIN,
        **overrides,
    }
    config, issues = resolve_config(raw)
    assert issues == []
    return config


def add_test_routes(app: FastAPI) -> None:
    @app.post("/api/outfits/{outfit_id}/feedback", status_code=201)
    def feedback(outfit_id: int, body: FeedbackIn) -> dict:
        return {"outfit_id": outfit_id, "thermal_vote": body.thermal_vote}

    @app.get("/test/number")
    def number(n: int) -> dict:
        return {"n": n}

    @app.get("/test/boom")
    def boom() -> dict:
        raise RuntimeError("/secret/server/path exploded")

    @app.get("/test/missing-today")
    def missing_today() -> dict:
        raise MissingTodayTemperature()

    @app.get("/test/api-error")
    def api_error() -> dict:
        raise ApiError(409, "SCAN_IN_PROGRESS", "이미 스캔이 진행 중입니다.")

    @app.get("/test/recommend")
    def recommend(service: RecommendationService = Depends(get_recommendation_service)) -> dict:
        return {"status": service.today_recommendations().status}


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(make_config(tmp_path))
    add_test_routes(app)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_create_app_builds_tables_and_container(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    engine = app.state.container.engine
    assert (tmp_path / "test.db").exists()
    assert {"outfits", "weather_snapshots", "feedback"} <= set(inspect(engine).get_table_names())
    assert app.state.container.strategy.algorithm_version == "rule_based_v1"


def test_importing_main_does_not_build_module_app() -> None:
    assert main_module._app is None


def test_resolve_paths_uses_repo_root_for_relative_paths(tmp_path: Path) -> None:
    config, _ = resolve_config({})
    resolved = resolve_paths(config)
    assert resolved.photo_dir == (REPO_ROOT / "data" / "demo" / "photos").resolve()
    assert resolved.database_url == f"sqlite:///{(REPO_ROOT / 'data' / 'weatherwear.db').resolve()}"

    absolute = make_config(tmp_path)
    assert resolve_paths(absolute) == absolute
    memory, _ = resolve_config({"DATABASE_URL": "sqlite://"})
    assert resolve_paths(memory).database_url == "sqlite://"


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    allowed = client.get("/test/number?n=1", headers={"Origin": ALLOWED_ORIGIN})
    assert allowed.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN

    for origin in ("http://evil.example", "http://localhost:5174", "HTTP://LOCALHOST:5173"):
        response = client.get("/test/number?n=1", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "body",
    [
        {"thermal_vote": "COLD"},
        {"thermal_vote": " cold"},
        {"thermal_vote": ""},
        {"thermal_vote": 1},
        {"thermal_vote": None},
        {"thermal_vote": True},
        {},
        None,
    ],
)
def test_feedback_body_errors_are_invalid_thermal_vote(client: TestClient, body: object) -> None:
    response = client.post("/api/outfits/1/feedback", json=body)
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_THERMAL_VOTE",
            "message": "체감 값은 cold, comfortable, hot 중 하나여야 합니다.",
        }
    }


def test_valid_feedback_body_passes_validation(client: TestClient) -> None:
    response = client.post("/api/outfits/1/feedback", json={"thermal_vote": "hot", "x": 1})
    # The real outfits router (task 10.2) shadows the test route: outfit 1 does
    # not exist, so the body passes validation and the existence check answers 404.
    assert response.status_code in (201, 404)
    assert response.json().get("error", {}).get("code") != "INVALID_THERMAL_VOTE"


def test_other_validation_errors_are_validation_error(client: TestClient) -> None:
    response = client.get("/test/number?n=abc")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unhandled_error_hides_traceback(client: TestClient) -> None:
    response = client.get("/test/boom")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다."}}
    assert "Traceback" not in response.text and "/secret/server/path" not in response.text


def test_domain_and_api_errors_use_common_format(client: TestClient) -> None:
    missing = client.get("/test/missing-today")
    assert missing.status_code == 503
    assert missing.json()["error"]["code"] == "TODAY_TEMPERATURE_MISSING"

    conflict = client.get("/test/api-error")
    assert conflict.status_code == 409
    assert conflict.json() == {"error": {"code": "SCAN_IN_PROGRESS", "message": "이미 스캔이 진행 중입니다."}}

    unknown = client.get("/no/such/route")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "NOT_FOUND"


def test_get_strategy_override_is_used_by_recommendations(client: TestClient) -> None:
    calls: list[int] = []

    class RecordingStrategy:
        algorithm_version = "rule_based_v1"

        def recommend(self, today, candidates):  # noqa: ANN001
            return self.recommend_with_outcome(today, candidates).results

        def recommend_with_outcome(self, today, candidates):  # noqa: ANN001
            calls.append(len(candidates))
            return RecommendationOutcome(results=[], applied_tolerance_c=Decimal("3.0"), fallback=True)

    client.app.dependency_overrides[get_strategy] = lambda: RecordingStrategy()
    response = client.get("/test/recommend")
    assert response.status_code == 200
    assert response.json() == {"status": "insufficient_data"}
    assert calls == [0]


def test_scan_lock_is_process_wide() -> None:
    assert get_scan_lock() is scan_lock
    assert scan_lock.acquire(blocking=False)
    try:
        assert not get_scan_lock().acquire(blocking=False)
    finally:
        scan_lock.release()


def test_startup_logs_config_issues_and_redacts_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "test-key-0123456789abcdef"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    monkeypatch.setenv("VISION_PROVIDER", "bogus")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))

    with caplog.at_level(logging.INFO):
        app = create_app(env_file=None)
        add_test_routes(app)
        with TestClient(app, raise_server_exceptions=False) as test_client:
            logging.getLogger("test").error("key is %s", secret)
            assert test_client.get("/test/boom").status_code == 500

    issue_records = [r for r in caplog.records if "VISION_PROVIDER" in r.getMessage()]
    assert issue_records and all(r.levelno == logging.ERROR for r in issue_records)
    assert app.state.config.vision_provider == "fixture"
    assert secret not in caplog.text
    assert "key is ***" in caplog.text
