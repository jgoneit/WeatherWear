"""Unit tests for app/db.py: tables, constraints, pragmas and ISO text helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import (
    Feedback,
    Outfit,
    WeatherSnapshot,
    create_db_engine,
    from_iso,
    session_scope,
    to_iso,
    to_utc_iso,
)

KST = timezone(timedelta(hours=9))
NOW = "2026-11-14T00:00:00.000000+00:00"


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'nested' / 'dir' / 'test.db'}")
    yield eng
    eng.dispose()


def make_outfit(sha: str = "a" * 64, captured_at: datetime | None = None, **overrides) -> Outfit:
    captured = captured_at or datetime(2025, 11, 20, 8, 30, tzinfo=KST)
    values = dict(
        sha256=sha,
        file_name="021_cafe_cream_sweater.png",
        captured_at=to_iso(captured),
        captured_at_utc=to_utc_iso(captured),
        latitude=37.5665,
        longitude=126.9780,
        upper_type="sweater",
        outer_type="none",
        lower_type="unknown",
        colors_json=json.dumps(["cream", "navy"]),
        layers_visible=1,
        vision_confidence=0.8,
        vision_provider="fixture",
        created_at=NOW,
    )
    values.update(overrides)
    return Outfit(**values)


def make_snapshot(outfit_id: int, **overrides) -> WeatherSnapshot:
    values = dict(
        outfit_id=outfit_id,
        observed_at="2025-11-20T08:00:00+09:00",
        actual_temperature_c=8.0,
        provider="fixture",
        is_fixture=True,
        created_at=NOW,
    )
    values.update(overrides)
    return WeatherSnapshot(**values)


def add_outfit(engine, **kwargs) -> int:
    with session_scope(engine) as session:
        outfit = make_outfit(**kwargs)
        session.add(outfit)
        session.flush()
        return outfit.id


def test_creates_parent_dir_and_tables(tmp_path: Path, engine) -> None:
    assert (tmp_path / "nested" / "dir" / "test.db").exists()
    assert set(inspect(engine).get_table_names()) >= {"outfits", "weather_snapshots", "feedback"}


def test_pragmas_on_every_connection(engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"


def test_indexes_exist(engine) -> None:
    insp = inspect(engine)
    assert "ix_outfits_captured_at_utc_id" in {i["name"] for i in insp.get_indexes("outfits")}
    feedback_idx = {i["name"]: i for i in insp.get_indexes("feedback")}
    assert feedback_idx["ix_feedback_outfit_created_id"]["column_names"] == ["outfit_id", "created_at", "id"]


def test_sha256_unique(engine) -> None:  # Req 1.4, 1.5
    add_outfit(engine)
    with pytest.raises(IntegrityError):
        add_outfit(engine)
    with Session(engine) as session:
        assert len(session.exec(select(Outfit)).all()) == 1


def test_one_snapshot_per_outfit(engine) -> None:  # Req 4.4
    outfit_id = add_outfit(engine)
    with session_scope(engine) as session:
        session.add(make_snapshot(outfit_id))
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add(make_snapshot(outfit_id))


def test_foreign_keys_enforced(engine) -> None:
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add(make_snapshot(999))
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add(Feedback(outfit_id=999, thermal_vote="cold", source="user_action", created_at=NOW))


def test_snapshot_cascades_on_outfit_delete(engine) -> None:
    outfit_id = add_outfit(engine)
    with session_scope(engine) as session:
        session.add(make_snapshot(outfit_id))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM outfits WHERE id = :id"), {"id": outfit_id})
        assert conn.execute(text("SELECT COUNT(*) FROM weather_snapshots")).scalar() == 0


@pytest.mark.parametrize(
    ("model", "field", "bad"),
    [
        ("feedback", "thermal_vote", "COLD"),
        ("feedback", "source", "admin"),
        ("outfit", "vision_provider", "openai"),
        ("outfit", "source", "upload"),
        ("outfit", "sha256", "short"),
        ("snapshot", "provider", "scenario"),
    ],
)
def test_check_constraints(engine, model: str, field: str, bad: str) -> None:
    outfit_id = add_outfit(engine, sha="b" * 64)
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            if model == "feedback":
                row = Feedback(outfit_id=outfit_id, thermal_vote="cold", source="user_action", created_at=NOW)
            elif model == "snapshot":
                row = make_snapshot(outfit_id)
            else:
                row = make_outfit(sha="c" * 64)
            setattr(row, field, bad)
            session.add(row)


def test_session_scope_rolls_back_whole_bundle(engine) -> None:  # Req 1.10, 2.6
    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            outfit = make_outfit()
            session.add(outfit)
            session.flush()
            session.add(make_snapshot(outfit.id, provider="bogus"))
    with Session(engine) as session:
        assert session.exec(select(Outfit)).all() == []


def test_snapshot_nulls_and_offsets_preserved(engine) -> None:  # Req 2.2, 4.3
    captured = datetime(2025, 1, 8, 8, 15, tzinfo=timezone(timedelta(hours=-3, minutes=-30)))
    outfit_id = add_outfit(engine, captured_at=captured)
    with session_scope(engine) as session:
        session.add(make_snapshot(outfit_id, relative_humidity_pct=55.0))
    with Session(engine) as session:
        outfit = session.get(Outfit, outfit_id)
        snap = session.exec(select(WeatherSnapshot)).one()
    restored = from_iso(outfit.captured_at)
    assert restored == captured and restored.utcoffset() == captured.utcoffset()
    assert snap.apparent_temperature_c is None and snap.wind_speed_kmh is None
    assert snap.relative_humidity_pct == 55.0 and snap.is_fixture is True


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        to_iso(datetime(2025, 1, 1))
    with pytest.raises(ValueError):
        from_iso("2025-01-01T00:00:00")


aware = st.datetimes(
    min_value=datetime(1970, 1, 2), max_value=datetime(9998, 12, 30),
    timezones=st.builds(timezone, st.integers(-12 * 60, 14 * 60).map(lambda m: timedelta(minutes=m))),
)


@given(aware)
def test_iso_round_trip_keeps_instant_and_offset(value: datetime) -> None:
    restored = from_iso(to_iso(value))
    assert restored == value and restored.utcoffset() == value.utcoffset()
    assert from_iso(to_utc_iso(value)) == value


@given(aware, aware)
def test_utc_text_orders_like_instants(a: datetime, b: datetime) -> None:
    assert (to_utc_iso(a) < to_utc_iso(b)) == (a.astimezone(UTC) < b.astimezone(UTC))
