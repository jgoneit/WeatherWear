"""LocationService and the location API (Phase 2 Req 9, tasks 1.8)."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import resolve_config
from app.db import create_db_engine
from app.location import (
    InvalidLocation,
    LatLon,
    LocationService,
    round_location,
    validate_location,
)
from app.main import create_app

DEFAULT = LatLon(37.5665, 126.978)
REASONS = {
    "latitude must be a finite number between -90 and 90",
    "longitude must be a finite number between -180 and 180",
    "accuracy_m must be a finite number >= 0",
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'location.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "photos").mkdir()
    config, issues = resolve_config(
        {"DATABASE_URL": f"sqlite:///{tmp_path / 'api.db'}", "PHOTO_DIR": str(tmp_path / "photos")}
    )
    assert issues == []
    with TestClient(create_app(config)) as test_client:
        yield test_client


# --- pure helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(37.565, 37.57), (37.564, 37.56), (-37.565, -37.57), (126.975, 126.98), (0.005, 0.01),
     (90.0, 90.0), (-180.0, -180.0), (1.0049999, 1.0)],
)
def test_round_half_up_two_places(value: float, expected: float) -> None:
    assert round_location(value, value / 2).latitude == expected


@pytest.mark.parametrize(
    ("lat", "lon", "accuracy"),
    [
        (True, 127.0, None), (37.0, False, None), ("37", 127.0, None), (None, 127.0, None),
        (math.nan, 127.0, None), (37.0, math.inf, None), (90.01, 127.0, None),
        (-90.01, 127.0, None), (37.0, 180.01, None), (37.0, -180.01, None),
        (37.0, 127.0, -1.0), (37.0, 127.0, True), (37.0, 127.0, math.nan), (37.0, 127.0, "5"),
    ],
)
def test_validate_location_rejects(lat: object, lon: object, accuracy: object) -> None:
    result = validate_location(lat, lon, accuracy)
    assert result in REASONS  # fixed text: the reason never echoes the value


def test_validate_location_accepts_ints_and_bounds() -> None:
    assert validate_location(37, 127, 0) == LatLon(37.0, 127.0)
    assert validate_location(-90.0, 180.0, None) == LatLon(-90.0, 180.0)


# --- service --------------------------------------------------------------------


def test_location_round_and_replace(engine) -> None:
    service = LocationService(engine, DEFAULT, Clock())
    first = service.report(37.56789, 126.97812, 15.0)
    assert (first.latitude, first.longitude, first.accuracy_m) == (37.57, 126.98, 15.0)
    second = service.report(35.1796, 129.0756, None)
    assert (second.latitude, second.longitude, second.accuracy_m) == (35.18, 129.08, None)
    assert second.received_at > first.received_at
    assert service.last() == second  # only the latest report is kept
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM user_location")).scalar() == 1


def test_invalid_report_keeps_previous(engine) -> None:
    service = LocationService(engine, DEFAULT, Clock())
    kept = service.report(37.5, 127.0, None)
    with pytest.raises(InvalidLocation):
        service.report(91.0, 127.0, None)
    assert service.last() == kept


def test_brief_location_default_then_browser(engine) -> None:
    service = LocationService(engine, DEFAULT, Clock())
    assert service.last() is None
    assert service.brief_location() == (DEFAULT, "default")
    service.report(35.1796, 129.0756, None)
    assert service.brief_location() == (LatLon(35.18, 129.08), "browser")


def test_location_not_logged(engine, caplog) -> None:
    service = LocationService(engine, DEFAULT, Clock())
    with caplog.at_level(logging.DEBUG):
        service.report(35.1796, 129.0756, 12.5)
        service.brief_location()
        with pytest.raises(InvalidLocation):
            service.report(35.1796, 999.0, None)
    for token in ("35.1796", "129.0756", "35.18", "129.08", "999"):
        assert token not in caplog.text


# --- API ------------------------------------------------------------------------------


def test_location_api_round_and_replace(client: TestClient) -> None:
    response = client.post("/api/location", json={"latitude": 37.56789, "longitude": 126.97812})
    assert response.status_code == 200
    body = response.json()
    assert (body["latitude"], body["longitude"], body["accuracy_m"]) == (37.57, 126.98, None)
    assert body["place_label"]
    assert body["received_at"].endswith("Z") or body["received_at"].endswith("+00:00")

    client.post("/api/location", json={"latitude": 35.1796, "longitude": 129.0756, "accuracy_m": 30})
    state = client.get("/api/location").json()
    assert state["last"]["latitude"] == 35.18 and state["last"]["longitude"] == 129.08
    assert state["brief_source"] == "browser"
    assert (state["default_latitude"], state["default_longitude"]) == (37.5665, 126.978)


@pytest.mark.parametrize(
    "body",
    [
        {"latitude": True, "longitude": 127.0},
        {"latitude": "37.5", "longitude": 127.0},
        {"longitude": 127.0},
        {"latitude": 90.5, "longitude": 127.0},
        {"latitude": 37.5, "longitude": -181},
        {"latitude": 37.5, "longitude": 127.0, "accuracy_m": -1},
        {"latitude": 37.5, "longitude": 127.0, "accuracy_m": False},
        None,
    ],
)
def test_location_invalid_422_keeps_previous(client: TestClient, body) -> None:
    assert client.post("/api/location", json={"latitude": 37.5, "longitude": 127.0}).status_code == 200
    before = client.get("/api/location").json()["last"]
    response = client.post("/api/location", json=body) if body is not None else client.post(
        "/api/location", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_LOCATION"
    assert client.get("/api/location").json()["last"] == before


def test_location_api_default_when_no_report(client: TestClient) -> None:
    state = client.get("/api/location").json()
    assert state["last"] is None
    assert state["brief_source"] == "default"
    assert state["brief_place_label"]


def test_location_nan_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/location",
        content=b'{"latitude": NaN, "longitude": 127.0}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_LOCATION"
