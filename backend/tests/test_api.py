"""API status codes and wiring, area (f) of Req 17.4 (task 10.10).

Every app is built with ``create_app(config=resolve_config({...}))`` or with
monkeypatched process env and an explicit ``env_file`` (``None`` or a file
under ``tmp_path``), so the real repository ``.env`` is never read. Each test
gets its own SQLite file and PHOTO_DIR under ``tmp_path``; providers are the
fixture ones, so no network is used.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlmodel import Session, delete, select

from app.config import AppConfig, resolve_config
from app.db import Feedback, WeatherSnapshot
from app.deps import get_ingestion_service, get_strategy, get_today_weather_service, scan_lock
from app.domain import ClothingTags, RecommendationResult, WeatherContext, WeatherReading
from app.main import create_app
from app.recommendation.base import RecommendationOutcome
from app.repository import OutfitCreate, OutfitRepository
from app.schemas import ScanReport
from app.today_weather import DEFAULT_SCENARIOS_PATH

KST = timezone(timedelta(hours=9))
FAKE_KEY = "fake-test-key-0123456789abcdef"  # never a real key
TAGS = ClothingTags(
    upper_type="knit", outer_type="coat", lower_type="slacks",
    colors=["navy"], layers_visible=2, confidence=0.9,
)
CONFIG_VARS = (
    "GEMINI_API_KEY", "VISION_PROVIDER", "GEMINI_MODEL", "WEATHER_PROVIDER", "DATABASE_URL",
    "PHOTO_DIR", "APP_TIMEZONE", "TEMPERATURE_TOLERANCE_C", "DEFAULT_LATITUDE",
    "DEFAULT_LONGITUDE", "CORS_ORIGINS",
)

# Every route of design.md §11 (Req 10.6).
EXPECTED_OPERATIONS = {
    ("/api/demo/scan", "post"),
    ("/api/demo/status", "get"),
    ("/api/demo/scenarios", "get"),
    ("/api/demo/scenarios/selected", "put"),
    ("/api/demo/scenarios/selected", "delete"),
    ("/api/meta", "get"),
    ("/api/weather/today", "get"),
    ("/api/recommendations/today", "get"),
    ("/api/outfits", "get"),
    ("/api/outfits/{outfit_id}", "get"),
    ("/api/outfits/{outfit_id}/photo", "get"),
    ("/api/outfits/{outfit_id}/feedback", "post"),
}


# --- Helpers ----------------------------------------------------------------


def make_config(tmp_path: Path, **overrides: str) -> AppConfig:
    raw = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        **overrides,
    }
    config, issues = resolve_config(raw)
    assert issues == []
    return config


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    path = tmp_path / "photos"
    path.mkdir()
    return path


@pytest.fixture
def client(tmp_path: Path, photo_dir: Path):
    with TestClient(create_app(make_config(tmp_path)), raise_server_exceptions=False) as test_client:
        yield test_client


def repo_of(client: TestClient) -> OutfitRepository:
    return client.app.state.container.repository


def png_bytes(seed: int = 0) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (seed % 256, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


def write_photo(directory: Path, name: str, captured_at: str, seed: int) -> None:
    (directory / f"{name}.png").write_bytes(png_bytes(seed))
    (directory / f"{name}.metadata.json").write_text(
        f'{{"captured_at": "{captured_at}", "latitude": 37.5665, "longitude": 126.978}}',
        encoding="utf-8",
    )


def add_outfit(
    client: TestClient,
    *,
    sha: str,
    file_name: str = "a.png",
    temp: float = 9.0,
    seed_vote: str | None = None,
) -> int:
    captured_at = datetime(2025, 11, 20, 8, 30, tzinfo=KST)
    weather = WeatherReading(
        observed_at=captured_at.replace(minute=0),
        actual_temperature_c=temp,
        apparent_temperature_c=None,
        relative_humidity_pct=None,
        wind_speed_kmh=None,
        precipitation_mm=None,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )
    outfit = OutfitCreate(
        sha256=sha, file_name=file_name, captured_at=captured_at,
        latitude=37.5665, longitude=126.978, tags=TAGS, vision_provider="fixture",
    )
    return repo_of(client).create_outfit_bundle(outfit, weather, seed_vote)  # type: ignore[arg-type]


def feedback_rows(client: TestClient) -> list[Feedback]:
    with Session(client.app.state.container.engine) as session:
        return list(session.exec(select(Feedback).order_by(Feedback.id)))


def counts(client: TestClient) -> dict[str, int]:
    return repo_of(client).counts()


def scenario_temp(scenario_id: str) -> float:
    """``actual_temperature_c`` of a scenario in the real data/demo/scenarios.json."""
    doc = json.loads(DEFAULT_SCENARIOS_PATH.read_text(encoding="utf-8"))
    return next(s["actual_temperature_c"] for s in doc["scenarios"] if s["id"] == scenario_id)


def clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)


# --- GET /api/outfits, /api/outfits/{id} -------------------------------------


def test_get_outfits_200_empty_list(client: TestClient) -> None:
    response = client.get("/api/outfits")
    assert response.status_code == 200
    assert response.json() == []


def test_get_outfit_404(client: TestClient) -> None:
    add_outfit(client, sha="a" * 64)
    response = client.get("/api/outfits/999")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "OUTFIT_NOT_FOUND", "message": "착장을 찾을 수 없습니다."}
    }


def test_null_fields_present_in_json(client: TestClient) -> None:
    with_weather = add_outfit(client, sha="a" * 64)
    bare = add_outfit(client, sha="b" * 64, file_name="b.png")
    with Session(client.app.state.container.engine) as session:
        session.exec(delete(WeatherSnapshot).where(WeatherSnapshot.outfit_id == bare))  # type: ignore[call-overload]
        session.commit()

    detail = client.get(f"/api/outfits/{bare}").json()
    # Absent weather and vote are present as null, never omitted (Req 10.4).
    assert "weather" in detail and detail["weather"] is None
    assert "effective_vote" in detail and detail["effective_vote"] is None

    weather = client.get(f"/api/outfits/{with_weather}").json()["weather"]
    for key in (
        "apparent_temperature_c", "relative_humidity_pct", "wind_speed_kmh",
        "precipitation_mm", "weather_code",
    ):
        assert key in weather and weather[key] is None
    listed = {o["id"]: o for o in client.get("/api/outfits").json()}
    assert listed[bare]["weather"] is None and listed[bare]["effective_vote"] is None


# --- GET /api/outfits/{id}/photo ---------------------------------------------


@pytest.mark.parametrize("file_name", ["missing.png", "../outside.png", "sub/inner.png"])
def test_photo_404_no_path_leak(
    client: TestClient, photo_dir: Path, tmp_path: Path, file_name: str
) -> None:
    (tmp_path / "outside.png").write_bytes(png_bytes())
    (photo_dir / "sub").mkdir()
    (photo_dir / "sub" / "inner.png").write_bytes(png_bytes())
    outfit_id = add_outfit(client, sha="a" * 64, file_name=file_name)

    for path in (f"/api/outfits/{outfit_id}/photo", "/api/outfits/999/photo"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {
            "error": {"code": "PHOTO_NOT_FOUND", "message": "사진을 찾을 수 없습니다."}
        }
        assert str(tmp_path) not in response.text
        assert str(photo_dir) not in response.text
        assert file_name not in response.text


# --- POST /api/outfits/{id}/feedback -----------------------------------------


def test_feedback_201(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="cold")
    before = datetime.now(UTC)
    response = client.post(f"/api/outfits/{outfit_id}/feedback", json={"thermal_vote": "comfortable"})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"outfit_id", "thermal_vote", "created_at"}
    assert body["outfit_id"] == outfit_id and body["thermal_vote"] == "comfortable"
    created = datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
    assert created.tzinfo is not None and created >= before - timedelta(seconds=1)
    assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == "comfortable"


def test_feedback_404(client: TestClient) -> None:
    response = client.post("/api/outfits/999/feedback", json={"thermal_vote": "hot"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "OUTFIT_NOT_FOUND", "message": "착장을 찾을 수 없습니다."}
    }
    assert counts(client)["feedback"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"thermal_vote": "COLD"},
        {"thermal_vote": "Comfortable"},
        {"thermal_vote": " cold"},
        {"thermal_vote": "hot "},
        {"thermal_vote": ""},
        {"thermal_vote": "warm"},
        {"thermal_vote": 1},
        {"thermal_vote": 0.5},
        {"thermal_vote": True},
        {"thermal_vote": None},
        {"thermal_vote": ["hot"]},
        {"thermal_vote": {"v": "hot"}},
        {},
        {"vote": "hot"},
        None,
    ],
)
def test_feedback_422_variants(client: TestClient, body: object) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="cold")
    response = client.post(f"/api/outfits/{outfit_id}/feedback", json=body)
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_THERMAL_VOTE",
            "message": "체감 값은 cold, comfortable, hot 중 하나여야 합니다.",
        }
    }
    assert counts(client)["feedback"] == 1
    assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == "cold"


def test_feedback_extra_fields_not_stored(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64)
    other_id = add_outfit(client, sha="b" * 64, file_name="b.png")
    response = client.post(
        f"/api/outfits/{outfit_id}/feedback",
        json={
            "thermal_vote": "hot",
            "source": "demo_seed",
            "outfit_id": other_id,
            "created_at": "2000-01-01T00:00:00Z",
            "id": 12345,
        },
    )
    assert response.status_code == 201
    rows = feedback_rows(client)
    assert len(rows) == 1
    row = rows[0]
    # Only the vote comes from the body (Req 9.7).
    assert row.outfit_id == outfit_id
    assert row.thermal_vote == "hot"
    assert row.source == "user_action"
    assert row.id != 12345
    assert not row.created_at.startswith("2000")
    assert client.get(f"/api/outfits/{other_id}").json()["effective_vote"] is None


def test_feedback_storage_error_returns_500(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, seed_vote="cold")
    engine = client.app.state.container.engine
    # A real SQLite failure on INSERT, through the unmodified API stack.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TRIGGER fail_feedback BEFORE INSERT ON feedback "
            "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
        ))

    response = client.post(f"/api/outfits/{outfit_id}/feedback", json={"thermal_vote": "hot"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "FEEDBACK_SAVE_FAILED", "message": "피드백을 저장하지 못했습니다."}
    }
    assert "disk full" not in response.text
    assert counts(client)["feedback"] == 1
    assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == "cold"

    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER fail_feedback"))
    retry = client.post(f"/api/outfits/{outfit_id}/feedback", json={"thermal_vote": "hot"})
    assert retry.status_code == 201
    assert client.get(f"/api/outfits/{outfit_id}").json()["effective_vote"] == "hot"


# --- Scenarios ----------------------------------------------------------------


def test_unknown_scenario_404_keeps_selection(client: TestClient) -> None:
    # Nothing selected: an unknown id leaves nothing selected.
    first = client.put("/api/demo/scenarios/selected", json={"scenario_id": "no-such"})
    assert first.status_code == 404
    assert first.json()["error"]["code"] == "UNKNOWN_SCENARIO"
    assert client.get("/api/demo/scenarios").json()["selected_id"] is None
    assert client.get("/api/weather/today").json()["provider"] == "fixture"

    assert client.put(
        "/api/demo/scenarios/selected", json={"scenario_id": "chilly-morning"}
    ).status_code == 200
    unknown = client.put("/api/demo/scenarios/selected", json={"scenario_id": "CHILLY-MORNING"})
    assert unknown.status_code == 404
    assert unknown.json() == {
        "error": {"code": "UNKNOWN_SCENARIO", "message": "알 수 없는 날씨 시나리오입니다."}
    }
    assert client.get("/api/demo/scenarios").json()["selected_id"] == "chilly-morning"
    today = client.get("/api/weather/today").json()
    assert today["provider"] == "scenario" and today["scenario_id"] == "chilly-morning"
    assert today["actual_temperature_c"] == scenario_temp("chilly-morning")


# --- OpenAPI -------------------------------------------------------------------


def test_openapi_lists_all_paths(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    doc = response.json()
    operations = {(path, method) for path, item in doc["paths"].items() for method in item}
    assert operations == EXPECTED_OPERATIONS
    schemas = doc["components"]["schemas"]
    for name in ("OutfitOut", "RecommendationResponse", "FeedbackIn", "FeedbackOut", "ScanReport"):
        assert name in schemas
    assert client.get("/docs").status_code == 200


# --- POST /api/demo/scan ---------------------------------------------------------


def test_scan_body_path_ignored(client: TestClient, photo_dir: Path, tmp_path: Path) -> None:
    write_photo(photo_dir, "configured", "2025-01-10T08:00:00+09:00", seed=1)
    other = tmp_path / "other"
    other.mkdir()
    write_photo(other, "other1", "2025-01-11T08:00:00+09:00", seed=2)
    write_photo(other, "other2", "2025-01-12T08:00:00+09:00", seed=3)

    response = client.post(
        "/api/demo/scan", json={"photo_dir": str(other), "path": str(other), "PHOTO_DIR": str(other)}
    )
    assert response.status_code == 200
    assert response.json() == {"scanned": 1, "succeeded": 1, "duplicate": 0, "failed": 0, "errors": []}
    assert [r.file_name for r in repo_of(client).list_outfits()] == ["configured.png"]


def _make_unavailable(kind: str, tmp_path: Path) -> Path:
    if kind == "missing":
        return tmp_path / "secret-missing-dir"
    if kind == "not_dir":
        target = tmp_path / "secret-file"
        target.write_text("x")
        return target
    target = tmp_path / "secret-locked-dir"
    target.mkdir()
    write_photo(target, "p", "2025-01-10T08:00:00+09:00", seed=4)
    target.chmod(0)
    return target


@pytest.mark.parametrize("kind", ["missing", "not_dir", "unreadable"])
def test_photo_dir_unavailable_returns_503(tmp_path: Path, kind: str) -> None:
    if kind == "unreadable" and hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can read a chmod 000 directory")
    target = _make_unavailable(kind, tmp_path)
    try:
        app = create_app(make_config(tmp_path, PHOTO_DIR=str(target)))
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.post("/api/demo/scan")
            assert response.status_code == 503
            assert response.json() == {
                "error": {
                    "code": "PHOTO_DIR_UNAVAILABLE",
                    "message": "사진 폴더에 접근할 수 없습니다. 설정을 확인해 주세요.",
                }
            }
            assert target.name not in response.text and str(tmp_path) not in response.text
            assert counts(test_client) == {"outfits": 0, "weather_snapshots": 0, "feedback": 0}
            assert not scan_lock.locked()
    finally:
        if kind == "unreadable":
            target.chmod(0o755)


def test_concurrent_scan_returns_409(client: TestClient, photo_dir: Path) -> None:
    write_photo(photo_dir, "a", "2025-01-10T08:00:00+09:00", seed=5)
    started = threading.Event()
    release = threading.Event()
    real = client.app.state.container

    class BlockingIngestion:
        def scan(self) -> ScanReport:
            started.set()
            assert release.wait(timeout=10)
            return ScanReport(scanned=0, succeeded=0, duplicate=0, failed=0, errors=[])

    client.app.dependency_overrides[get_ingestion_service] = BlockingIngestion
    results: dict[str, int] = {}

    def first_scan() -> None:
        results["first"] = client.post("/api/demo/scan").status_code

    worker = threading.Thread(target=first_scan)
    worker.start()
    try:
        assert started.wait(timeout=10)
        second = client.post("/api/demo/scan")
    finally:
        release.set()
        worker.join(timeout=10)

    assert second.status_code == 409
    assert second.json() == {
        "error": {"code": "SCAN_IN_PROGRESS", "message": "이미 스캔이 진행 중입니다."}
    }
    assert results["first"] == 200
    assert real.repository.counts()["outfits"] == 0

    # The lock is released after the first scan; a real scan now runs.
    client.app.dependency_overrides.clear()
    assert not scan_lock.locked()
    assert client.post("/api/demo/scan").json()["succeeded"] == 1


# --- GET /api/recommendations/today ------------------------------------------------


def _missing_today_service():
    class MissingToday:
        def get_today(self) -> WeatherContext:
            return WeatherContext(
                actual_temperature_c=None, apparent_temperature_c=None,
                relative_humidity_pct=None, wind_speed_kmh=None, precipitation_mm=None,
                observed_at=None, provider="fixture", is_fixture=True,
                scenario_id=None, scenario_name=None,
            )

    return MissingToday()


@pytest.mark.parametrize("path", ["/api/recommendations/today", "/api/weather/today"])
def test_missing_today_temperature_returns_503(client: TestClient, path: str) -> None:
    add_outfit(client, sha="a" * 64)
    client.app.dependency_overrides[get_today_weather_service] = _missing_today_service
    response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "TODAY_TEMPERATURE_MISSING", "message": "오늘 기온 정보를 가져오지 못했습니다."}
    }


def test_insufficient_data_returns_200_empty(client: TestClient) -> None:
    # The only stored temperature is 10.0°C below heatwave (diff > 5.0), and
    # the Outfit recorded at the heatwave temperature has no weather at all (Req 6.9).
    heat = scenario_temp("heatwave")
    add_outfit(client, sha="a" * 64, temp=round(heat - 10.0, 1))
    bare = add_outfit(client, sha="b" * 64, file_name="b.png", temp=heat)
    with Session(client.app.state.container.engine) as session:
        session.exec(delete(WeatherSnapshot).where(WeatherSnapshot.outfit_id == bare))  # type: ignore[call-overload]
        session.commit()
    client.put("/api/demo/scenarios/selected", json={"scenario_id": "heatwave"})

    response = client.get("/api/recommendations/today")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["items"] == []
    assert body["fallback"] is True
    assert body["algorithm_version"] == "rule_based_v1"
    assert body["today"]["actual_temperature_c"] == heat


def test_strategy_injected_via_dependency_override(client: TestClient) -> None:
    outfit_id = add_outfit(client, sha="a" * 64, temp=round(scenario_temp("heatwave") - 10.0, 1))
    client.put("/api/demo/scenarios/selected", json={"scenario_id": "heatwave"})
    # The default strategy finds nothing 10.0°C away, even with the 5.0°C fallback.
    assert client.get("/api/recommendations/today").json()["status"] == "insufficient_data"

    seen: list[list[int]] = []

    class PickEverything:
        algorithm_version = "rule_based_v1"
        tolerance_c = Decimal("3.0")

        def recommend(self, today, candidates):  # noqa: ANN001
            return self.recommend_with_outcome(today, candidates).results

        def recommend_with_outcome(self, today, candidates):  # noqa: ANN001
            seen.append([c.outfit_id for c in candidates])
            results = [
                RecommendationResult(
                    outfit_id=c.outfit_id, rank=i + 1, score=42.0, temperature_diff_c=29.0,
                    selected_by_fallback=False, confidence_level="low",
                    reasons=["test strategy"], explanation="테스트 전략이 고른 착장입니다.",
                    algorithm_version="rule_based_v1",
                )
                for i, c in enumerate(candidates)
            ]
            return RecommendationOutcome(results=results, applied_tolerance_c=Decimal("3.0"), fallback=False)

    client.app.dependency_overrides[get_strategy] = PickEverything
    response = client.get("/api/recommendations/today")
    assert response.status_code == 200
    body = response.json()
    assert seen == [[outfit_id]]
    assert body["status"] == "ok" and body["fallback"] is False
    assert [item["outfit"]["id"] for item in body["items"]] == [outfit_id]
    assert body["items"][0]["score"] == 42.0
    assert body["items"][0]["explanation"] == "테스트 전략이 고른 착장입니다."

    client.app.dependency_overrides.clear()
    assert client.get("/api/recommendations/today").json()["status"] == "insufficient_data"


# --- Config loading through create_app ------------------------------------------------


def test_env_precedence_process_over_dotenv_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "photos").mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TEMPERATURE_TOLERANCE_C=2.5\n"
        "APP_TIMEZONE=Europe/London\n"
        f"DATABASE_URL=sqlite:///{tmp_path / 'dotenv.db'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEMPERATURE_TOLERANCE_C", "4")
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))

    app = create_app(env_file=env_file)
    config: AppConfig = app.state.config
    assert config.temperature_tolerance_c == Decimal("4")       # process env beats .env
    assert config.app_timezone.key == "Europe/London"           # .env beats default
    assert config.database_url == f"sqlite:///{tmp_path / 'dotenv.db'}"
    assert (config.default_latitude, config.default_longitude) == (37.5665, 126.9780)  # default
    assert config.vision_provider == "fixture" and config.weather_provider == "fixture"
    assert app.state.container.strategy.tolerance_c == Decimal("4")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        assert test_client.get("/api/meta").json()["app_timezone"] == "Europe/London"
    assert (tmp_path / "dotenv.db").exists()


@pytest.mark.parametrize(
    ("key", "model", "missing"),
    [("", "sentinel-model-value", "GEMINI_API_KEY"), (FAKE_KEY, "", "GEMINI_MODEL")],
)
def test_gemini_missing_key_falls_back_to_fixture_and_logs_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    key: str,
    model: str,
    missing: str,
) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("VISION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setenv("GEMINI_MODEL", model)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))

    with caplog.at_level(logging.INFO):
        app = create_app(env_file=None)
        with TestClient(app, raise_server_exceptions=False) as test_client:
            status = test_client.get("/api/demo/status").json()
            meta = test_client.get("/api/meta")

    assert app.state.config.vision_provider == "fixture"
    assert status["vision_provider"] == "fixture"
    assert status["gemini_api_key_configured"] is bool(key)
    assert meta.json()["vision_provider"] == "fixture"

    issue_records = [r for r in caplog.records if missing in r.getMessage()]
    assert issue_records and all(r.levelno == logging.ERROR for r in issue_records)
    # Only the variable name is logged, never a value (Req 15.6, 16.5).
    assert FAKE_KEY not in caplog.text and FAKE_KEY[:8] not in caplog.text
    assert "sentinel-model-value" not in caplog.text
    assert FAKE_KEY not in meta.text and FAKE_KEY not in str(status)
