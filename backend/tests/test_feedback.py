"""Feedback update unit tests, area (e) of Req 17.4 (task 8.6).

Covers the Effective_Vote rule (Req 9.4), insert-only history (Req 9.5) and
rollback on storage failure (Req 9.8) against a real SQLite database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import create_db_engine
from app.domain import ClothingTags, WeatherReading
from app.feedback import FeedbackSaveFailed, FeedbackService, effective_vote
from app.repository import FeedbackRecord, OutfitCreate, OutfitRepository

KST = timezone(timedelta(hours=9))


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'feedback.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> OutfitRepository:
    return OutfitRepository(engine)


def add_outfit(repo: OutfitRepository, sha: str = "b" * 64, seed_vote=None) -> int:
    outfit = OutfitCreate(
        sha256=sha,
        file_name="033_home_sage_sweater.png",
        captured_at=datetime(2025, 11, 12, 9, 0, tzinfo=KST),
        latitude=37.5665,
        longitude=126.9780,
        tags=ClothingTags(
            upper_type="knit", outer_type="none", lower_type="jeans",
            colors=["sage"], layers_visible=1, confidence=0.9,
        ),
        vision_provider="fixture",
    )
    weather = WeatherReading(
        observed_at=datetime(2025, 11, 12, 9, 0, tzinfo=KST),
        actual_temperature_c=9.0,
        apparent_temperature_c=7.0,
        relative_humidity_pct=50.0,
        wind_speed_kmh=10.0,
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


def candidate_vote(repo: OutfitRepository, outfit_id: int):
    (candidate,) = [c for c in repo.list_candidates() if c.outfit_id == outfit_id]
    return candidate.effective_vote


def test_latest_vote_becomes_effective(repo) -> None:
    outfit_id = add_outfit(repo)
    base = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)
    times = iter([base, base + timedelta(minutes=1), base + timedelta(minutes=2)])
    service = FeedbackService(repo, clock=lambda: next(times))

    for vote in ["cold", "hot", "comfortable"]:
        service.submit(outfit_id, vote)
        assert repo.get_outfit(outfit_id).effective_vote == vote
        assert candidate_vote(repo, outfit_id) == vote

    # created_at decides, not insertion order: an older record listed last loses.
    records = [
        FeedbackRecord(id=2, outfit_id=1, thermal_vote="hot", source="user_action",
                       created_at=base + timedelta(seconds=5)),
        FeedbackRecord(id=3, outfit_id=1, thermal_vote="cold", source="user_action",
                       created_at=base),
    ]
    assert effective_vote(records) == "hot"
    assert effective_vote([]) is None


def test_same_timestamp_uses_larger_id(repo, engine) -> None:
    outfit_id = add_outfit(repo)
    fixed = datetime(2026, 11, 14, 4, 0, 0, 500000, tzinfo=UTC)
    service = FeedbackService(repo, clock=lambda: fixed)

    first = service.submit(outfit_id, "hot")
    second = service.submit(outfit_id, "cold")

    assert first.created_at == second.created_at
    assert second.id > first.id
    assert repo.get_outfit(outfit_id).effective_vote == "cold"

    # Same rule in the pure function, regardless of list order.
    assert effective_vote([second, first]) == "cold"
    assert effective_vote([first, second]) == "cold"


def test_seed_then_user_vote_user_wins(repo, engine) -> None:
    outfit_id = add_outfit(repo, seed_vote="cold")
    assert repo.get_outfit(outfit_id).effective_vote == "cold"

    FeedbackService(repo).submit(outfit_id, "comfortable")

    rows = feedback_rows(engine, outfit_id)
    assert [(r.thermal_vote, r.source) for r in rows] == [
        ("cold", "demo_seed"),
        ("comfortable", "user_action"),
    ]
    assert repo.get_outfit(outfit_id).effective_vote == "comfortable"
    assert candidate_vote(repo, outfit_id) == "comfortable"


def test_repeated_same_vote_creates_separate_records(repo, engine) -> None:
    outfit_id = add_outfit(repo)
    service = FeedbackService(repo)

    records = [service.submit(outfit_id, "hot") for _ in range(3)]

    assert len({r.id for r in records}) == 3
    rows = feedback_rows(engine, outfit_id)
    assert [r.id for r in rows] == [r.id for r in records]
    assert [(r.thermal_vote, r.source) for r in rows] == [("hot", "user_action")] * 3
    assert repo.counts()["feedback"] == 3

    # A later different vote is one more row; earlier rows stay untouched.
    service.submit(outfit_id, "cold")
    after = feedback_rows(engine, outfit_id)
    assert after[:3] == rows
    assert len(after) == 4
    assert repo.get_outfit(outfit_id).effective_vote == "cold"


def test_storage_error_rolls_back_and_keeps_vote(repo, engine) -> None:
    outfit_id = add_outfit(repo, seed_vote="cold")
    service = FeedbackService(repo)
    service.submit(outfit_id, "comfortable")
    before = feedback_rows(engine, outfit_id)

    # A real SQLite failure on insert: the trigger aborts every new feedback row.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TRIGGER fail_feedback BEFORE INSERT ON feedback "
                "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
            )
        )

    with pytest.raises(FeedbackSaveFailed) as excinfo:
        service.submit(outfit_id, "hot")

    assert excinfo.value.outfit_id == outfit_id
    assert feedback_rows(engine, outfit_id) == before
    assert repo.get_outfit(outfit_id).effective_vote == "comfortable"
    assert candidate_vote(repo, outfit_id) == "comfortable"

    # After the fault is removed, the next submit works normally.
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER fail_feedback"))
    service.submit(outfit_id, "hot")
    assert repo.get_outfit(outfit_id).effective_vote == "hot"
