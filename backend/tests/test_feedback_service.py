"""Unit tests for app.feedback.FeedbackService (task 8.1, Req 9.1, 9.3, 9.5, 9.7, 9.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import create_db_engine
from app.domain import ClothingTags, WeatherReading
from app.feedback import FeedbackSaveFailed, FeedbackService, OutfitNotFound
from app.repository import OutfitCreate, OutfitRepository

KST = timezone(timedelta(hours=9))


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'feedback.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> OutfitRepository:
    return OutfitRepository(engine)


def add_outfit(repo: OutfitRepository, sha: str = "a" * 64, seed_vote=None) -> int:
    outfit = OutfitCreate(
        sha256=sha,
        file_name="021_cafe_cream_sweater.png",
        captured_at=datetime(2025, 11, 20, 8, 30, tzinfo=KST),
        latitude=37.5665,
        longitude=126.9780,
        tags=ClothingTags(
            upper_type="knit", outer_type="none", lower_type="slacks",
            colors=["cream"], layers_visible=1, confidence=0.8,
        ),
        vision_provider="fixture",
    )
    weather = WeatherReading(
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
    return repo.create_outfit_bundle(outfit, weather, seed_vote)


def feedback_rows(engine, outfit_id: int) -> list[tuple]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT id, thermal_vote, source, created_at FROM feedback "
                    "WHERE outfit_id = :o ORDER BY id"
                ),
                {"o": outfit_id},
            )
        )


def test_submit_stores_user_action_with_utc_microsecond_time(repo, engine) -> None:
    outfit_id = add_outfit(repo)
    now = datetime(2026, 11, 14, 13, 5, 7, 123456, tzinfo=KST)
    service = FeedbackService(repo, clock=lambda: now)

    record = service.submit(outfit_id, "comfortable")

    assert record.outfit_id == outfit_id
    assert record.thermal_vote == "comfortable"
    assert record.source == "user_action"
    assert record.created_at == now
    assert record.created_at.utcoffset() == timedelta(0)
    assert record.created_at.microsecond == 123456
    rows = feedback_rows(engine, outfit_id)
    assert [(r.thermal_vote, r.source) for r in rows] == [("comfortable", "user_action")]
    assert rows[0].created_at == "2026-11-14T04:05:07.123456+00:00"
    assert repo.get_outfit(outfit_id).effective_vote == "comfortable"


def test_default_clock_is_utc_now(repo) -> None:
    outfit_id = add_outfit(repo)
    before = datetime.now(UTC)
    record = FeedbackService(repo).submit(outfit_id, "hot")
    after = datetime.now(UTC)
    assert before <= record.created_at <= after
    assert record.created_at.utcoffset() == timedelta(0)


def test_repeated_submissions_are_kept_as_history(repo, engine) -> None:
    outfit_id = add_outfit(repo, seed_vote="cold")
    service = FeedbackService(repo)

    for vote in ["hot", "hot", "comfortable"]:
        service.submit(outfit_id, vote)

    rows = feedback_rows(engine, outfit_id)
    assert [(r.thermal_vote, r.source) for r in rows] == [
        ("cold", "demo_seed"),
        ("hot", "user_action"),
        ("hot", "user_action"),
        ("comfortable", "user_action"),
    ]
    assert repo.get_outfit(outfit_id).effective_vote == "comfortable"


def test_unknown_outfit_raises_and_stores_nothing(repo) -> None:
    service = FeedbackService(repo)
    with pytest.raises(OutfitNotFound) as excinfo:
        service.submit(999, "cold")
    assert excinfo.value.outfit_id == 999
    assert repo.counts()["feedback"] == 0


def test_invalid_vote_is_rejected_before_storage(repo) -> None:
    outfit_id = add_outfit(repo)
    with pytest.raises(ValueError):
        FeedbackService(repo).submit(outfit_id, "COLD")  # type: ignore[arg-type]
    assert repo.counts()["feedback"] == 0


def test_storage_error_rolls_back_and_keeps_effective_vote(repo, engine) -> None:
    outfit_id = add_outfit(repo, seed_vote="cold")
    # A real SQLite failure on insert: the trigger aborts every new feedback row.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TRIGGER fail_feedback BEFORE INSERT ON feedback "
                "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
            )
        )

    with pytest.raises(FeedbackSaveFailed) as excinfo:
        FeedbackService(repo).submit(outfit_id, "hot")

    assert excinfo.value.outfit_id == outfit_id
    assert excinfo.value.__cause__ is not None
    assert [(r.thermal_vote, r.source) for r in feedback_rows(engine, outfit_id)] == [
        ("cold", "demo_seed")
    ]
    assert repo.get_outfit(outfit_id).effective_vote == "cold"
