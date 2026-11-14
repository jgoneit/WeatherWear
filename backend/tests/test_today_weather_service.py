"""Unit tests for scenarios and TodayWeatherService (task 8.3, Req 5.1~5.8)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import resolve_config
from app.domain import WeatherReading
from app.providers.weather.fixture import DEFAULT_WEATHER_FIXTURE_PATH, FixtureWeatherProvider
from app.today_weather import (
    DEFAULT_SCENARIOS_PATH,
    ScenarioStore,
    TodayWeatherService,
    UnknownScenario,
    WeatherScenario,
    load_scenarios,
)

FIXED_NOW = datetime(2026, 11, 14, 3, 25, tzinfo=UTC)  # 12:25 KST


class RecordingProvider:
    """Weather_Provider stand-in that records calls and returns a fixed reading."""

    def __init__(self, name: str = "open_meteo", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[datetime, float, float]] = []

    def get_weather(self, at: datetime, latitude: float, longitude: float) -> WeatherReading:
        self.calls.append((at, latitude, longitude))
        if self.fail:
            raise RuntimeError("boom")
        return WeatherReading(
            observed_at=at.replace(minute=0, second=0, microsecond=0),
            actual_temperature_c=11.5,
            apparent_temperature_c=None,
            relative_humidity_pct=48.0,
            wind_speed_kmh=None,
            precipitation_mm=0.0,
            weather_code=None,
            provider=self.name,  # type: ignore[arg-type]
            is_fixture=self.name == "fixture",
        )


def _real_scenarios() -> list[dict[str, object]]:
    """Entries of the real data/demo/scenarios.json, in file order."""
    return json.loads(DEFAULT_SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]


def _real_scenario_temp(scenario_id: str) -> object:
    return next(s["actual_temperature_c"] for s in _real_scenarios() if s["id"] == scenario_id)


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _scenario(id_: str, temp: float = 9.0, **extra: object) -> dict[str, object]:
    return {"id": id_, "name": f"name-{id_}", "actual_temperature_c": temp, **extra}


def _service(store: ScenarioStore, provider: object, **kwargs: object) -> TodayWeatherService:
    cfg, _ = resolve_config({"DEFAULT_LATITUDE": "35.1", "DEFAULT_LONGITUDE": "129.04"})
    return TodayWeatherService(cfg, provider, store, now=lambda: FIXED_NOW, **kwargs)  # type: ignore[arg-type]


# --- load_scenarios ---------------------------------------------------------


def test_real_demo_scenarios_load() -> None:
    scenarios, errors = load_scenarios(DEFAULT_SCENARIOS_PATH)
    assert errors == []
    # Every entry loads unchanged and in file order (nothing is dropped).
    assert [s.model_dump() for s in scenarios] == _real_scenarios()
    assert [s.id for s in scenarios] == ["chilly-morning", "cold-snap", "early-heat", "heatwave"]
    # The recorded weather has no apparent temperature, so no scenario has one (Req 7.8).
    assert [s.apparent_temperature_c for s in scenarios] == [None, None, None, None]


def test_missing_file_returns_empty_and_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        scenarios, errors = load_scenarios(tmp_path / "nope.json")
    assert scenarios == [] and len(errors) == 1
    assert "not found" in caplog.text


@pytest.mark.parametrize("text", ["{not json", "[1, 2", "\"just a string\"", "{\"scenarios\": 3}"])
def test_unparsable_file_returns_empty_and_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "scenarios.json"
    path.write_text(text, encoding="utf-8")
    scenarios, errors = load_scenarios(path)
    assert scenarios == [] and len(errors) == 1


def test_invalid_entries_dropped_valid_kept_in_order(tmp_path: Path) -> None:
    path = _write(tmp_path, {"scenarios": [
        _scenario("a", 1.0),
        {"id": "no-temp", "name": "x"},                              # missing actual_temperature_c
        _scenario("str-temp", "9.0"),                                # type: ignore[arg-type]
        _scenario("bool-temp", True),                                # type: ignore[arg-type]
        {"name": "no id", "actual_temperature_c": 1.0},
        _scenario("", 2.0),
        _scenario("b", -3, relative_humidity_pct=150),               # humidity out of range
        "not an object",
        _scenario("c", 5, note="extra fields are ignored"),
    ]})
    scenarios, errors = load_scenarios(path)
    assert [s.id for s in scenarios] == ["a", "c"]
    assert scenarios[1].actual_temperature_c == 5.0
    assert len(errors) == 7


def test_duplicate_id_keeps_first(tmp_path: Path) -> None:
    path = _write(tmp_path, {"scenarios": [_scenario("a", 1.0), _scenario("b", 2.0), _scenario("a", 99.0)]})
    scenarios, errors = load_scenarios(path)
    assert [(s.id, s.actual_temperature_c) for s in scenarios] == [("a", 1.0), ("b", 2.0)]
    assert len(errors) == 1 and "duplicate" in errors[0]


# --- ScenarioStore ----------------------------------------------------------


def _store() -> ScenarioStore:
    return ScenarioStore([WeatherScenario.model_validate(_scenario(i, t)) for i, t in [("a", 1.0), ("b", 2.0)]])


def test_store_starts_unselected_and_selects() -> None:
    store = _store()
    assert store.selected() is None
    assert store.select("b").id == "b"
    assert store.selected().id == "b"  # type: ignore[union-attr]
    store.clear()
    assert store.selected() is None


def test_unknown_id_raises_and_keeps_selection() -> None:
    store = _store()
    with pytest.raises(UnknownScenario):
        store.select("zzz")
    assert store.selected() is None
    store.select("a")
    with pytest.raises(UnknownScenario):
        store.select("A")
    assert store.selected().id == "a"  # type: ignore[union-attr]


# --- TodayWeatherService ----------------------------------------------------


def test_no_scenario_uses_provider_with_default_coordinates() -> None:
    provider = RecordingProvider()
    ctx = _service(_store(), provider).get_today()
    assert len(provider.calls) == 1
    at, lat, lon = provider.calls[0]
    assert (lat, lon) == (35.1, 129.04)
    assert at == FIXED_NOW and at.utcoffset().total_seconds() == 9 * 3600  # type: ignore[union-attr]
    assert ctx.actual_temperature_c == 11.5
    assert ctx.provider == "open_meteo" and ctx.is_fixture is False
    assert ctx.scenario_id is None and ctx.scenario_name is None


def test_selected_scenario_overrides_provider_until_cleared() -> None:
    provider = RecordingProvider()
    store = _store()
    service = _service(store, provider)
    store.select("b")
    for _ in range(2):  # selection persists across requests
        ctx = service.get_today()
        assert ctx.actual_temperature_c == 2.0
        assert ctx.provider == "scenario" and ctx.is_fixture is True
        assert (ctx.scenario_id, ctx.scenario_name) == ("b", "name-b")
        assert ctx.observed_at == datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # 12:00 KST
        assert ctx.observed_at.utcoffset().total_seconds() == 9 * 3600  # type: ignore[union-attr]
    assert provider.calls == []
    store.clear()
    assert service.get_today().provider == "open_meteo"


def test_real_scenarios_drive_today_weather() -> None:
    scenarios, _ = load_scenarios()
    store = ScenarioStore(scenarios)
    service = _service(store, RecordingProvider())
    store.select("cold-snap")
    ctx = service.get_today()
    assert (ctx.actual_temperature_c, ctx.apparent_temperature_c, ctx.scenario_id) == (
        _real_scenario_temp("cold-snap"),
        None,
        "cold-snap",
    )


def test_empty_scenario_list_still_serves_provider_weather(tmp_path: Path) -> None:
    scenarios, _ = load_scenarios(tmp_path / "missing.json")
    ctx = _service(ScenarioStore(scenarios), RecordingProvider()).get_today()
    assert ctx.actual_temperature_c == 11.5


def test_provider_exception_falls_back_to_fixture() -> None:
    fixture = FixtureWeatherProvider(now=lambda: FIXED_NOW)
    ctx = _service(_store(), RecordingProvider(fail=True), fallback=fixture).get_today()
    assert ctx.provider == "fixture" and ctx.is_fixture is True
    today_default = json.loads(DEFAULT_WEATHER_FIXTURE_PATH.read_text(encoding="utf-8"))["today_default"]
    assert ctx.actual_temperature_c == today_default["actual_temperature_c"]


def test_provider_exception_without_fallback_propagates() -> None:
    with pytest.raises(RuntimeError):
        _service(_store(), RecordingProvider(fail=True)).get_today()
