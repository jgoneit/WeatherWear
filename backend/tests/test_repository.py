"""Unit tests for app/repository.py and app/feedback.effective_vote."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import create_db_engine
from app.domain import ClothingTags, OutfitWithWeatherAndFeedback, WeatherReading
from app.feedback import effective_vote
from app.repository import OutfitCreate, OutfitRepository

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'repo.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> OutfitRepository:
    return OutfitRepository(engine)


def make_outfit(sha: str = "a" * 64, captured_at: datetime | None = None, **overrides) -> OutfitCreate:
    values = dict(
        sha256=sha,
        file_name="021_cafe_cream_sweater.png",
        captured_at=captured_at or datetime(2025, 11, 20, 8, 30, tzinfo=KST),
        latitude=37.5665,
        longitude=126.9780,
        tags=ClothingTags(
            upper_type="sweater", outer_type="none", lower_type="slacks",
            colors=["크림", "navy"], layers_visible=1, confidence=0.8,
        ),
        vision_provider="fixture",
    )
    values.update(overrides)
    return OutfitCreate(**values)


def make_weather(**overrides) -> WeatherReading:
    values = dict(
        observed_at=datetime(2025, 11, 20, 8, 0, tzinfo=KST),
        actual_temperature_c=8.0,
        apparent_temperature_c=None,
        relative_humidity_pct=55.0,
        wind_speed_kmh=None,
        precipitation_mm=0.0,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )
    values.update(overrides)
    return WeatherReading(**values)


# -- effective_vote -----------------------------------------------------------


@dataclass
class Rec:
    id: int
    created_at: datetime
    thermal_vote: str
    source: str = "user_action"


def test_effective_vote_empty_is_none() -> None:
    assert effective_vote([]) is None


def test_effective_vote_latest_created_at_wins_regardless_of_order_and_source() -> None:
    records = [
        Rec(5, T0, "cold", "user_action"),
        Rec(1, T0 + timedelta(microseconds=1), "hot", "demo_seed"),
        Rec(9, T0 - timedelta(days=1), "comfortable"),
    ]
    assert effective_vote(records) == "hot"


def test_effective_vote_tie_uses_larger_id() -> None:
    assert effective_vote([Rec(7, T0, "hot"), Rec(3, T0, "cold")]) == "hot"


# -- create_outfit_bundle -----------------------------------------------------


def test_bundle_round_trip_keeps_offset_tags_and_nulls(repo: OutfitRepository) -> None:
    captured = datetime(2025, 1, 8, 8, 15, tzinfo=timezone(timedelta(hours=-3, minutes=-30)))
    outfit_id = repo.create_outfit_bundle(make_outfit(captured_at=captured), make_weather(), None)

    record = repo.get_outfit(outfit_id)
    assert record is not None
    assert record.captured_at == captured and record.captured_at.utcoffset() == captured.utcoffset()
    assert record.source == "demo_sidecar"
    assert record.vision_provider == "fixture"
    assert record.tags == make_outfit().tags  # colors order preserved
    assert record.weather == make_weather()
    assert record.weather.apparent_temperature_c is None and record.weather.wind_speed_kmh is None
    assert record.effective_vote is None
    assert repo.counts() == {"outfits": 1, "weather_snapshots": 1, "feedback": 0}


def test_vision_provider_gemini_stored(repo: OutfitRepository) -> None:
    outfit_id = repo.create_outfit_bundle(make_outfit(vision_provider="gemini"), make_weather(), None)
    assert repo.get_outfit(outfit_id).vision_provider == "gemini"


def test_seed_vote_saved_as_demo_seed(repo: OutfitRepository, engine) -> None:
    outfit_id = repo.create_outfit_bundle(make_outfit(), make_weather(), "comfortable")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT thermal_vote, source FROM feedback")).all()
    assert rows == [("comfortable", "demo_seed")]
    assert repo.get_outfit(outfit_id).effective_vote == "comfortable"


def test_exists_sha(repo: OutfitRepository) -> None:
    assert repo.exists_sha("a" * 64) is False
    repo.create_outfit_bundle(make_outfit(), make_weather(), None)
    assert repo.exists_sha("a" * 64) is True
    assert repo.exists_sha("b" * 64) is False


def test_duplicate_sha_rolls_back_whole_bundle(repo: OutfitRepository) -> None:  # Req 1.4, 1.10
    repo.create_outfit_bundle(make_outfit(), make_weather(), None)
    with pytest.raises(IntegrityError):
        repo.create_outfit_bundle(make_outfit(), make_weather(), "cold")
    assert repo.counts() == {"outfits": 1, "weather_snapshots": 1, "feedback": 0}


def test_failed_seed_insert_rolls_back_outfit_and_snapshot(repo: OutfitRepository) -> None:  # Req 2.6
    with pytest.raises(IntegrityError):
        repo.create_outfit_bundle(make_outfit(), make_weather(), "COLD")  # violates CHECK
    assert repo.counts() == {"outfits": 0, "weather_snapshots": 0, "feedback": 0}
    assert repo.exists_sha("a" * 64) is False


# -- reads --------------------------------------------------------------------


def test_get_outfit_missing_returns_none(repo: OutfitRepository) -> None:
    assert repo.get_outfit(999) is None


def test_list_outfits_orders_by_utc_instant_then_id_desc(repo: OutfitRepository) -> None:  # Req 10.1
    instant = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    a = repo.create_outfit_bundle(make_outfit("1" * 64, instant.astimezone(KST)), make_weather(), None)
    b = repo.create_outfit_bundle(make_outfit("2" * 64, instant), make_weather(), None)  # same instant
    c = repo.create_outfit_bundle(  # later instant, earlier local clock time
        make_outfit("3" * 64, datetime(2025, 3, 1, 1, 0, tzinfo=timezone(timedelta(hours=-5)))),
        make_weather(), None,
    )
    d = repo.create_outfit_bundle(make_outfit("4" * 64, instant - timedelta(days=1)), make_weather(), None)
    assert [r.id for r in repo.list_outfits()] == [c, b, a, d]


def test_list_outfits_empty(repo: OutfitRepository) -> None:
    assert repo.list_outfits() == []


def test_list_candidates_carries_weather_and_effective_vote(repo: OutfitRepository) -> None:
    first = repo.create_outfit_bundle(make_outfit("1" * 64), make_weather(actual_temperature_c=9.0), "cold")
    second = repo.create_outfit_bundle(make_outfit("2" * 64), make_weather(), None)
    repo.add_feedback(first, "comfortable", "user_action", datetime.now(UTC) + timedelta(seconds=1))

    candidates = {c.outfit_id: c for c in repo.list_candidates()}
    assert set(candidates) == {first, second}
    assert all(isinstance(c, OutfitWithWeatherAndFeedback) for c in candidates.values())
    assert candidates[first].effective_vote == "comfortable"
    assert candidates[first].weather.actual_temperature_c == 9.0
    assert candidates[second].effective_vote is None
    assert candidates[second].tags == make_outfit().tags


# -- add_feedback -------------------------------------------------------------


def test_add_feedback_inserts_only_and_keeps_history(repo: OutfitRepository, engine) -> None:  # Req 9.4, 9.5
    outfit_id = repo.create_outfit_bundle(make_outfit(), make_weather(), "hot")
    created = datetime.now(UTC) + timedelta(seconds=1)
    first = repo.add_feedback(outfit_id, "cold", "user_action", created)
    second = repo.add_feedback(outfit_id, "cold", "user_action", created)  # same value and time

    assert first.outfit_id == outfit_id and first.thermal_vote == "cold"
    assert first.source == "user_action" and first.created_at == created
    assert second.id > first.id
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT thermal_vote, source FROM feedback ORDER BY id")).all()
    assert rows == [("hot", "demo_seed"), ("cold", "user_action"), ("cold", "user_action")]
    assert repo.get_outfit(outfit_id).effective_vote == "cold"


def test_add_feedback_stores_utc_with_microseconds(repo: OutfitRepository, engine) -> None:
    outfit_id = repo.create_outfit_bundle(make_outfit(), make_weather(), None)
    record = repo.add_feedback(outfit_id, "hot", "user_action", datetime(2026, 1, 1, 9, 0, tzinfo=KST))
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT created_at FROM feedback")).scalar_one()
    assert stored == "2026-01-01T00:00:00.000000+00:00"
    assert record.created_at.utcoffset() == timedelta(0)


def test_add_feedback_unknown_outfit_raises_and_stores_nothing(repo: OutfitRepository) -> None:
    with pytest.raises(IntegrityError):
        repo.add_feedback(999, "cold", "user_action", T0)
    assert repo.counts()["feedback"] == 0
