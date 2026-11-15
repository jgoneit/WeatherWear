"""Flow B integration: real repository, RecommendationService and brief text (Phase 2 tasks 4.15).

Req 20.3 (h), 20.1, 11.2. ``test_brief_with_fixture_demo_data`` scans the
real demo album in fixture mode and expects the brief to be built from the
merged ``today_default`` and the rank-1 recommendation of that moment, so it
keeps working when merged demo data changes (main had 9.0°C, this branch
14.5°C). The exact user example sentence is pinned by
``test_brief_compose.py::test_compose_matches_user_example``. The other tests
use synthetic Outfits and control today's weather with a scenario or a
respx-mocked Forecast ``current`` response (Live mode).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import resolve_config
from app.deps import get_daily_brief_service, get_notification_log
from app.domain import WeatherContext
from app.main import create_app
from app.notifications.compose import BriefInput, compose_brief
from app.notifications.runtime import build_brief_runtime
from app.providers.weather.forecast import FORECAST_ENDPOINT
from app.schemas import RecommendationResponse
from tests.notifications_support import add_outfit, clothing, make_config
from tests.test_demo_data import DEMO_PHOTO_DIR, requires_demo_photos

SPOKEN = {"cold": "추웠어", "comfortable": "적당했어", "hot": "더웠어"}


def wire(app) -> None:
    """Put the flow B runtime into the app (what tasks 7.1 will do in the container)."""
    rt = build_brief_runtime(app.state.config, app.state.container)
    app.dependency_overrides[get_daily_brief_service] = lambda: rt.daily_brief
    app.dependency_overrides[get_notification_log] = lambda: rt.notification_log


def run_brief(client: TestClient) -> dict:
    response = client.post("/api/daily-brief/run")
    assert response.status_code == 200, response.text
    return response.json()["notification"]


def one_decimal(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}°C"


def forecast_current(temp: float, humidity: float | None) -> dict:
    current = {"time": "2026-11-14T07:30", "interval": 900, "temperature_2m": temp}
    if humidity is not None:
        current["relative_humidity_2m"] = humidity
    return {"current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%"}, "current": current}


# --- demo data ------------------------------------------------------------------------------


@requires_demo_photos
def test_brief_with_fixture_demo_data(tmp_path: Path) -> None:
    config, issues = resolve_config(
        {"DATABASE_URL": f"sqlite:///{tmp_path / 'demo.db'}", "PHOTO_DIR": str(DEMO_PHOTO_DIR)}
    )
    assert issues == [] and config.vision_provider == "fixture" and config.weather_provider == "fixture"
    app = create_app(config)
    wire(app)
    with TestClient(app) as client:
        scan = client.post("/api/demo/scan")
        assert scan.status_code == 200, scan.text
        assert scan.json()["succeeded"] > 0

        recommendation = RecommendationResponse.model_validate(client.get("/api/recommendations/today").json())
        note = run_brief(client)

    today = recommendation.today
    assert today.provider == "fixture"  # merged today_default, no scenario
    top = recommendation.items[0] if recommendation.items else None
    expected = compose_brief(BriefInput(place="서울", today=WeatherContext(**today.model_dump()), top=top))
    assert note["title"] == "오늘의 착장 브리핑 (fixture 날씨)"
    assert note["body"] == expected.body
    assert note["body"].startswith(f"오늘 서울 {one_decimal(today.actual_temperature_c)}")
    assert top is not None, "the demo data always has a recommendation for today_default"
    assert note["outfit_id"] == top.outfit.id and note["url"] == f"/outfits/{top.outfit.id}"
    assert top.outfit.weather is not None
    assert f"{one_decimal(top.outfit.weather.actual_temperature_c)} 날 " in note["body"]
    vote = top.outfit.effective_vote
    if vote is None:
        assert note["body"].endswith("그날 어땠는지 알려 주세요.")
    else:
        assert f"'{SPOKEN[vote]}'라고 했어요." in note["body"]
    assert note["weather_provider"] == "fixture" and note["weather_is_fixture"] is True
    assert len(note["body"]) <= 200


# --- synthetic outfits ----------------------------------------------------------------------


@pytest.fixture
def live(tmp_path: Path):
    """Live mode app with synthetic outfits; Forecast responses come from respx."""
    app = create_app(make_config(tmp_path, weather_provider="open_meteo"))
    wire(app)
    with respx.mock(assert_all_called=False) as mock, TestClient(app) as client:
        client.mock = mock  # type: ignore[attr-defined]
        yield client


def set_today(client: TestClient, temp: float, humidity: float | None = 55.0) -> respx.Route:
    return client.mock.get(FORECAST_ENDPOINT).mock(  # type: ignore[attr-defined]
        return_value=httpx.Response(200, json=forecast_current(temp, humidity))
    )


def repo(client: TestClient):
    return client.app.state.container.repository


def test_brief_without_vote(live: TestClient) -> None:
    set_today(live, 14.5)
    outfit_id = add_outfit(repo(live), 14.0, clothing("hoodie", "none", "jeans", ["grey"]), None)
    note = run_brief(live)
    assert note["body"] == (
        "오늘 서울 14.5°C, 습도 55%. 비슷했던 14.0°C 날 회색 후드티를 입었어요. 그날 어땠는지 알려 주세요."
    )
    assert note["title"] == "오늘의 착장 브리핑"
    assert note["url"] == f"/outfits/{outfit_id}"


def test_brief_cold_tail(live: TestClient) -> None:
    set_today(live, 3.2)
    add_outfit(repo(live), 3.0, clothing("knit", "coat", "slacks", ["camel"]), "cold")
    note = run_brief(live)
    assert note["body"] == (
        "오늘 서울 3.2°C, 습도 55%. 비슷했던 3.0°C 날 카멜 니트에 코트를 입고 '추웠어'라고 했어요. "
        "오늘은 한 겹 더 챙겨 보세요."
    )


def test_brief_feedback_vote_overrides_seed(live: TestClient) -> None:
    set_today(live, 25.0)
    outfit_id = add_outfit(repo(live), 25.4, clothing("t_shirt", "none", "shorts", ["white"]), "comfortable")
    response = live.post(f"/api/outfits/{outfit_id}/feedback", json={"thermal_vote": "hot"})
    assert response.status_code in (200, 201), response.text
    note = run_brief(live)
    assert note["body"].endswith("흰색 티셔츠를 입고 '더웠어'라고 했어요. 오늘은 조금 가볍게 입어 보세요.")


def test_brief_fallback_wording(live: TestClient) -> None:
    set_today(live, 14.5)
    add_outfit(repo(live), 10.5, clothing("unknown", "trench_coat", "jeans", ["beige"]), "comfortable")
    note = run_brief(live)  # 4.0°C apart: outside 3.0, inside the 5.0 fallback
    assert note["body"] == (
        "오늘 서울 14.5°C, 습도 55%. 기온이 가장 가까웠던 10.5°C 날 베이지 트렌치코트를 입고 '적당했어'라고 했어요."
    )


def test_brief_insufficient_data_when_nothing_close(live: TestClient) -> None:
    set_today(live, 30.0)
    add_outfit(repo(live), 10.0, vote="comfortable")  # 20°C away: no recommendation
    note = run_brief(live)
    assert note["body"].endswith("비슷한 기온에서 입었던 착장 기록이 아직 없어요. 오늘 입은 옷을 찍어 두면 다음에 알려 드릴게요.")
    assert note["url"] == "/" and note["outfit_id"] is None


def test_brief_without_humidity(live: TestClient) -> None:
    set_today(live, 14.5, humidity=None)
    add_outfit(repo(live), 14.4, clothing("knit", "none", "jeans", ["cream"]), "comfortable")
    note = run_brief(live)
    assert note["body"] == "오늘 서울 14.5°C. 비슷했던 14.4°C 날 크림 니트를 입고 '적당했어'라고 했어요."


def test_brief_uses_reported_location(live: TestClient) -> None:
    route = set_today(live, 14.5)
    add_outfit(repo(live), 14.4, vote="comfortable")
    assert live.post("/api/location", json={"latitude": 35.1796, "longitude": 129.0756}).status_code == 200
    note = run_brief(live)
    assert note["body"].startswith("오늘 부산 14.5°C")
    params = route.calls.last.request.url.params
    assert (params["latitude"], params["longitude"]) == ("35.18", "129.08")  # rounded Last_Location


def test_title_marks_fixture_and_scenario(tmp_path: Path) -> None:
    # Live: no mark. Forecast failure: fixture mark. Scenario: scenario mark (body unmarked).
    app = create_app(make_config(tmp_path, weather_provider="open_meteo"))
    wire(app)
    with respx.mock(assert_all_called=False) as mock, TestClient(app) as client:
        add_outfit(client.app.state.container.repository, 14.4, vote="comfortable")
        route = mock.get(FORECAST_ENDPOINT).mock(return_value=httpx.Response(200, json=forecast_current(14.5, 55)))
        assert run_brief(client)["title"] == "오늘의 착장 브리핑"

        route.mock(return_value=httpx.Response(503))
        failed = run_brief(client)
        assert failed["title"] == "오늘의 착장 브리핑 (fixture 날씨)"
        assert failed["weather_provider"] == "fixture" and failed["weather_is_fixture"] is True

        assert client.put("/api/demo/scenarios/selected", json={"scenario_id": "chilly-morning"}).status_code == 200
        scenario = run_brief(client)
        assert scenario["title"] == "오늘의 착장 브리핑 (시나리오 날씨)"
        assert scenario["weather_provider"] == "scenario"
        assert "시나리오" not in scenario["body"] and "fixture" not in scenario["body"]
        assert scenario["body"].startswith("오늘 서울 14.5°C, 습도 55%.")

    fixture_app = create_app(make_config(tmp_path / "fx"))
    wire(fixture_app)
    with TestClient(fixture_app) as client:
        assert run_brief(client)["title"] == "오늘의 착장 브리핑 (fixture 날씨)"
