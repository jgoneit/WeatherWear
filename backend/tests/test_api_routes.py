"""Demo, weather, recommendation and meta routers (task 10.3).

Configs are built with ``resolve_config({...})``; the real ``.env`` is never
read. Every test uses its own SQLite file under ``tmp_path``. The end-to-end
test scans the real demo photo set in ``data/demo/photos`` with fixture
providers (no network).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import AppConfig, resolve_config
from app.deps import get_today_weather_service, scan_lock
from app.domain import WeatherContext
from app.ingestion.image_check import SUPPORTED_EXTENSIONS
from app.main import REPO_ROOT, create_app

DEMO_PHOTO_DIR = REPO_ROOT / "data" / "demo" / "photos"
FAKE_KEY = "fake-test-key-0123456789abcdef"


def make_config(tmp_path: Path, **overrides: str) -> AppConfig:
    raw = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        **overrides,
    }
    config, issues = resolve_config(raw)
    assert issues == []
    return config


def make_client(config: AppConfig) -> TestClient:
    return TestClient(create_app(config), raise_server_exceptions=False)


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "photos").mkdir()
    with make_client(make_config(tmp_path)) as test_client:
        yield test_client


def write_photo(photo_dir: Path, name: str, captured_at: str, vote: str | None = None) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (sum(map(ord, name)) % 256, 10, 20)).save(buf, format="PNG")
    (photo_dir / f"{name}.png").write_bytes(buf.getvalue())
    sidecar = f'{{"captured_at": "{captured_at}", "latitude": 37.5665, "longitude": 126.978'
    sidecar += f', "thermal_vote": "{vote}"}}' if vote else "}"
    (photo_dir / f"{name}.metadata.json").write_text(sidecar, encoding="utf-8")


# --- POST /api/demo/scan ----------------------------------------------------


def test_scan_reports_counts_and_ignores_body(tmp_path: Path, client: TestClient) -> None:
    photo_dir = tmp_path / "photos"
    write_photo(photo_dir, "a", "2025-01-10T08:00:00+09:00", "comfortable")
    write_photo(photo_dir, "b", "2025-01-11T08:00:00+09:00")

    # A path in the body must not change the scanned folder (Req 1.9).
    first = client.post("/api/demo/scan", json={"photo_dir": str(DEMO_PHOTO_DIR)})
    assert first.status_code == 200
    assert first.json() == {"scanned": 2, "succeeded": 2, "duplicate": 0, "failed": 0, "errors": []}

    second = client.post("/api/demo/scan")
    assert second.json()["duplicate"] == 2 and second.json()["succeeded"] == 0

    status = client.get("/api/demo/status").json()
    assert (status["outfit_count"], status["weather_snapshot_count"], status["feedback_count"]) == (2, 2, 1)


def test_scan_returns_409_while_another_scan_runs(client: TestClient) -> None:
    assert scan_lock.acquire(blocking=False)
    try:
        response = client.post("/api/demo/scan")
    finally:
        scan_lock.release()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SCAN_IN_PROGRESS"
    # The lock is free again afterwards.
    assert client.post("/api/demo/scan").status_code == 200


def test_scan_missing_photo_dir_is_503_without_path(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere-secret-dir"
    with make_client(make_config(tmp_path, PHOTO_DIR=str(missing))) as test_client:
        response = test_client.post("/api/demo/scan")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PHOTO_DIR_UNAVAILABLE"
        assert "nowhere-secret-dir" not in response.text
        # The lock was released after the failure.
        assert not scan_lock.locked()


# --- GET /api/demo/status ---------------------------------------------------


def test_status_exposes_api_key_only_as_boolean(tmp_path: Path) -> None:
    config = make_config(tmp_path, GEMINI_API_KEY=FAKE_KEY)
    with make_client(config) as test_client:
        response = test_client.get("/api/demo/status")
        meta = test_client.get("/api/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["gemini_api_key_configured"] is True
    assert body["vision_provider"] == "fixture" and body["weather_provider"] == "fixture"
    assert body["selected_scenario"] is None
    for text in (response.text, meta.text):
        assert FAKE_KEY not in text and FAKE_KEY[:8] not in text


def test_status_without_key_is_false(client: TestClient) -> None:
    assert client.get("/api/demo/status").json()["gemini_api_key_configured"] is False


# --- Scenarios --------------------------------------------------------------


def test_scenario_list_select_clear_and_unknown(client: TestClient) -> None:
    listed = client.get("/api/demo/scenarios").json()
    assert [s["id"] for s in listed["scenarios"]] == [
        "chilly-morning",
        "cold-snap",
        "early-heat",
        "heatwave",
    ]
    assert listed["selected_id"] is None

    selected = client.put("/api/demo/scenarios/selected", json={"scenario_id": "cold-snap"})
    assert selected.status_code == 200
    assert selected.json()["selected_id"] == "cold-snap"
    assert selected.json()["scenario"]["actual_temperature_c"] == -12.0

    unknown = client.put("/api/demo/scenarios/selected", json={"scenario_id": "nope"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "UNKNOWN_SCENARIO"
    assert client.get("/api/demo/scenarios").json()["selected_id"] == "cold-snap"
    assert client.get("/api/demo/status").json()["selected_scenario"]["id"] == "cold-snap"

    cleared = client.delete("/api/demo/scenarios/selected")
    assert cleared.status_code == 204 and cleared.content == b""
    assert client.get("/api/demo/scenarios").json()["selected_id"] is None


def test_select_without_scenario_id_is_validation_error(client: TestClient) -> None:
    response = client.put("/api/demo/scenarios/selected", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- GET /api/weather/today -------------------------------------------------


def test_weather_today_uses_provider_then_scenario(client: TestClient) -> None:
    provider = client.get("/api/weather/today")
    assert provider.status_code == 200
    body = provider.json()
    assert body["provider"] == "fixture" and body["is_fixture"] is True
    assert body["scenario_id"] is None and isinstance(body["actual_temperature_c"], float)

    client.put("/api/demo/scenarios/selected", json={"scenario_id": "chilly-morning"})
    scenario = client.get("/api/weather/today").json()
    assert scenario["provider"] == "scenario" and scenario["is_fixture"] is True
    assert scenario["scenario_id"] == "chilly-morning"
    assert scenario["scenario_name"] == "쌀쌀한 아침"
    assert scenario["actual_temperature_c"] == 9.0


def _missing_today_service():
    class MissingToday:
        def get_today(self) -> WeatherContext:
            return WeatherContext(
                actual_temperature_c=None,
                apparent_temperature_c=None,
                relative_humidity_pct=None,
                wind_speed_kmh=None,
                precipitation_mm=None,
                observed_at=None,
                provider="fixture",
                is_fixture=True,
                scenario_id=None,
                scenario_name=None,
            )

    return MissingToday()


@pytest.mark.parametrize("path", ["/api/weather/today", "/api/recommendations/today"])
def test_missing_today_temperature_is_503(client: TestClient, path: str) -> None:
    client.app.dependency_overrides[get_today_weather_service] = _missing_today_service
    response = client.get(path)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TODAY_TEMPERATURE_MISSING"


# --- GET /api/recommendations/today -----------------------------------------


def test_recommendations_empty_db_is_insufficient_data(client: TestClient) -> None:
    response = client.get("/api/recommendations/today")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["fallback"] is True and body["items"] == []
    assert body["algorithm_version"] == "rule_based_v1"


# --- GET /api/meta ----------------------------------------------------------


def test_meta(client: TestClient) -> None:
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {
        "app_timezone": "Asia/Seoul",
        "algorithm_version": "rule_based_v1",
        "vision_provider": "fixture",
        "weather_provider": "fixture",
    }


def test_openapi_lists_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/demo/scan",
        "/api/demo/status",
        "/api/demo/scenarios",
        "/api/demo/scenarios/selected",
        "/api/weather/today",
        "/api/recommendations/today",
        "/api/meta",
    ):
        assert path in paths
    assert set(paths["/api/demo/scenarios/selected"]) == {"put", "delete"}


# Every route in design.md §11 with its method (Req 10.6). A router module that
# fails to register would drop its paths and fail this test.
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


def test_all_router_modules_registered(client: TestClient) -> None:
    from app.main import ROUTER_MODULES

    assert set(ROUTER_MODULES) == {"outfits", "demo", "weather", "recommendations", "meta"}
    paths = client.get("/openapi.json").json()["paths"]
    operations = {(path, method) for path, item in paths.items() for method in item}
    assert operations == EXPECTED_OPERATIONS


def test_export_openapi_script_matches_app(tmp_path: Path) -> None:
    import importlib.util

    script = REPO_ROOT / "scripts" / "export_openapi.py"
    spec = importlib.util.spec_from_file_location("export_openapi", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out = tmp_path / "openapi.json"
    assert module.main(["-o", str(out)]) == 0
    import json

    doc = json.loads(out.read_text(encoding="utf-8"))
    operations = {(path, method) for path, item in doc["paths"].items() for method in item}
    assert operations == EXPECTED_OPERATIONS
    assert "RecommendationResponse" in doc["components"]["schemas"]


# --- End to end with the real demo data -------------------------------------


def demo_images_present() -> bool:
    # The sidecar *.metadata.json files are tracked but the photos are
    # git-ignored, so a fresh clone has the folder without any images.
    return DEMO_PHOTO_DIR.is_dir() and any(
        p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        for p in DEMO_PHOTO_DIR.iterdir()
    )


@pytest.mark.skipif(
    not demo_images_present(),
    reason=(
        "data/demo/photos has no images (they are git-ignored); "
        "run scripts/prepare_demo_photos.py to copy the 159 demo photos"
    ),
)
def test_demo_data_end_to_end(tmp_path: Path) -> None:
    config = make_config(tmp_path, PHOTO_DIR=str(DEMO_PHOTO_DIR))
    app = create_app(config)
    repo = app.state.container.repository
    with TestClient(app, raise_server_exceptions=False) as test_client:
        report = test_client.post("/api/demo/scan").json()
        assert report["succeeded"] == 159, report["errors"][:3]
        assert report["failed"] == 0 and report["duplicate"] == 0

        file_by_id = {r.id: r.file_name for r in repo.list_outfits()}

        test_client.put("/api/demo/scenarios/selected", json={"scenario_id": "chilly-morning"})
        chilly = test_client.get("/api/recommendations/today").json()
        assert chilly["status"] == "ok" and chilly["fallback"] is False
        assert len(chilly["items"]) == 5
        top = chilly["items"][0]
        assert top["rank"] == 1 and top["score"] == 115.0
        assert file_by_id[top["outfit"]["id"]].startswith("081_")
        assert all(item["temperature_diff_c"] <= 3.0 for item in chilly["items"])

        test_client.put("/api/demo/scenarios/selected", json={"scenario_id": "heatwave"})
        heat = test_client.get("/api/recommendations/today")
        assert heat.status_code == 200
        assert heat.json()["status"] == "insufficient_data" and heat.json()["items"] == []

        unknown = test_client.put("/api/demo/scenarios/selected", json={"scenario_id": "no-such"})
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "UNKNOWN_SCENARIO"
        assert test_client.get("/api/demo/scenarios").json()["selected_id"] == "heatwave"
        assert test_client.get("/api/weather/today").json()["scenario_id"] == "heatwave"
