"""DailyBriefService, the run table, the scheduler and the CLI (Phase 2 tasks 4.11).

Req 11.1, 11.9, 11.13, 12.1~12.8, 13.1, 6.9. No thread is started: the
scheduler's ``tick()`` is called directly with a fake clock (Req 20.2).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.deps import build_container
from app.domain import UNKNOWN_TAGS, ClothingTags, VisionAnalysis
from app.ingestion.service import ImageIngestionService, NotOutfit
from app.notifications.compose import INSUFFICIENT_DATA_TEXT
from app.notifications.daily_brief import (
    DailyBriefInProgress,
    DailyBriefService,
    RunRow,
)
from app.notifications.records import DeliveryResult, DeliverySummary
from app.notifications.runtime import build_brief_runtime
from app.notifications.scheduler import (
    CATCH_UP,
    DailyBriefScheduler,
    next_run_at,
    scheduled_decision,
)
from app.providers.weather.fixture import DEFAULT_WEATHER_FIXTURE_PATH
from app.recommendation.service import RecommendationService
from tests.notifications_support import FakeClock, FakePushSender, add_outfit, clothing, make_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SEOUL = ZoneInfo("Asia/Seoul")
BRIEF_TIME = time(7, 30)
TODAY_DEFAULT = json.loads(DEFAULT_WEATHER_FIXTURE_PATH.read_text(encoding="utf-8"))["today_default"]


def kst(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=SEOUL).astimezone(UTC)


class Env:
    """One "server process": container + flow B runtime on a shared database file."""

    def __init__(self, tmp_path: Path, clock: FakeClock, **config_kw) -> None:
        self.config = make_config(tmp_path, daily_brief_enabled=True, **config_kw)
        self.clock = clock
        self.container = build_container(self.config)
        self.rt = build_brief_runtime(self.config, self.container, clock=clock, sender=FakePushSender())
        self.scheduler = self.rt.brief_scheduler
        assert self.scheduler is not None

    def service_with(self, recommendations) -> DailyBriefService:
        rt = self.rt
        return DailyBriefService(
            self.container.location, rt.current_weather, recommendations, rt.notification_log,
            rt.notifier, rt.runs, self.config.app_timezone, self.clock,
        )

    def scheduler_for(self, service: DailyBriefService) -> DailyBriefScheduler:
        return DailyBriefScheduler(
            service, self.rt.runs, self.config.automation.daily_brief_time, self.config.app_timezone, self.clock
        )

    def recommendations(self) -> RecommendationService:
        return RecommendationService(self.container.today_weather, self.container.repository, self.container.strategy)

    def notifications(self) -> list:
        return self.rt.notification_log.list_recent(100)

    def close(self) -> None:
        self.container.engine.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(kst(14, 6, 0))


@pytest.fixture
def env(tmp_path: Path, clock: FakeClock):
    environment = Env(tmp_path, clock)
    yield environment
    environment.close()


def run_row(status: str, attempts: int, started: datetime, finished: datetime | None = None) -> RunRow:
    return RunRow(1, date(2026, 11, 14), "scheduled", status, attempts, None, None, started, finished)  # type: ignore[arg-type]


# --- scheduled_decision / next_run_at (pure) ---------------------------------------


def test_scheduled_decision_table() -> None:
    def at(h: int, m: int, s: int = 0) -> datetime:
        return datetime(2026, 11, 14, h, m, s, tzinfo=SEOUL)

    assert scheduled_decision(at(7, 29, 59), BRIEF_TIME, None) == "wait"
    assert scheduled_decision(at(7, 30), BRIEF_TIME, None) == "run"
    assert scheduled_decision(at(10, 29, 59), BRIEF_TIME, None) == "run"
    assert scheduled_decision(at(10, 30), BRIEF_TIME, None) == "missed"
    sent = run_row("sent", 1, at(7, 30))
    assert scheduled_decision(at(8, 0), BRIEF_TIME, sent) == "done"
    assert scheduled_decision(at(11, 0), BRIEF_TIME, sent) == "done"
    running = run_row("running", 1, at(7, 30))
    assert scheduled_decision(at(9, 0), BRIEF_TIME, running) == "done"
    failed = run_row("failed", 1, at(7, 30), at(7, 31))
    assert scheduled_decision(at(7, 40, 59), BRIEF_TIME, failed) == "done"
    assert scheduled_decision(at(7, 41), BRIEF_TIME, failed) == "retry"
    assert scheduled_decision(at(10, 30), BRIEF_TIME, failed) == "done"  # never past the window
    exhausted = run_row("failed", 3, at(7, 50), at(7, 51))
    assert scheduled_decision(at(9, 0), BRIEF_TIME, exhausted) == "done"


def test_scheduled_decision_needs_zoneinfo() -> None:
    with pytest.raises(ValueError):
        scheduled_decision(datetime(2026, 11, 14, 8, 0, tzinfo=UTC), BRIEF_TIME, None)


def test_next_run_at() -> None:
    before = kst(14, 6)
    assert next_run_at(before, BRIEF_TIME, SEOUL, False) == datetime(2026, 11, 14, 7, 30, tzinfo=SEOUL)
    inside = kst(14, 8)
    assert next_run_at(inside, BRIEF_TIME, SEOUL, False) == inside.astimezone(SEOUL)
    assert next_run_at(inside, BRIEF_TIME, SEOUL, True) == datetime(2026, 11, 15, 7, 30, tzinfo=SEOUL)
    after = kst(14, 11)
    assert next_run_at(after, BRIEF_TIME, SEOUL, False) == datetime(2026, 11, 15, 7, 30, tzinfo=SEOUL)


# --- once per day -------------------------------------------------------------------------


def test_scheduled_once_per_day_across_restarts(tmp_path: Path, env: Env, clock: FakeClock) -> None:
    add_outfit(env.container.repository, TODAY_DEFAULT["actual_temperature_c"], vote="comfortable")
    assert env.scheduler.tick() == "wait"
    clock.set(kst(14, 7, 29, 59))
    assert env.scheduler.tick() == "wait"
    clock.set(kst(14, 7, 30))
    assert env.scheduler.tick() == "run"
    clock.advance(seconds=30)
    assert env.scheduler.tick() == "done"
    assert len(env.notifications()) == 1

    # Restart: a new process on the same database file.
    env.close()
    restarted = Env(tmp_path, clock)
    try:
        clock.set(kst(14, 8, 0))
        assert restarted.scheduler.tick() == "done"
        # A second process ticking at the same time cannot claim the date either.
        assert restarted.rt.daily_brief.run("scheduled", date(2026, 11, 14)).skipped is True
        # The clock jumps backwards inside the window: still done.
        clock.set(kst(14, 7, 31))
        assert restarted.scheduler.tick() == "done"
        assert len(restarted.notifications()) == 1

        clock.set(kst(15, 7, 30, 10))
        assert restarted.scheduler.tick() == "run"
        notes = restarted.notifications()
        assert len(notes) == 2
        assert [n.notification.brief_date for n in notes] == [date(2026, 11, 15), date(2026, 11, 14)]
        assert {(r.brief_date, r.trigger, r.status) for r in restarted.rt.runs.all()} == {
            (date(2026, 11, 14), "scheduled", "sent"),
            (date(2026, 11, 15), "scheduled", "sent"),
        }
    finally:
        restarted.close()


def test_start_after_catch_up_window_skips_today(env: Env, clock: FakeClock) -> None:
    clock.set(kst(14, 10, 30))  # 07:30 + 3 h: the window is closed
    assert env.scheduler.tick() == "missed"
    clock.advance(hours=5)
    assert env.scheduler.tick() == "missed"
    assert env.notifications() == [] and env.rt.runs.all() == []
    clock.set(kst(15, 7, 30))
    assert env.scheduler.tick() == "run"
    assert len(env.notifications()) == 1


def test_manual_run_does_not_block_scheduled(env: Env, clock: FakeClock) -> None:
    clock.set(kst(14, 7, 0))
    manual = env.rt.daily_brief.run("manual")
    assert manual.skipped is False and manual.trigger == "manual"
    env.rt.daily_brief.run("manual")  # as often as the demo wants
    clock.set(kst(14, 7, 30))
    assert env.scheduler.tick() == "run"
    assert len(env.notifications()) == 3
    runs = env.rt.runs.all()
    assert [(r.trigger, r.status) for r in runs] == [("manual", "sent"), ("manual", "sent"), ("scheduled", "sent")]
    # A manual run after the scheduled one is still allowed.
    assert env.rt.daily_brief.run("manual").skipped is False


def test_lock_held_raises_in_progress_and_scheduler_waits(env: Env, clock: FakeClock) -> None:
    entered, release = threading.Event(), threading.Event()

    def blocking_recommendations() -> RecommendationService:
        entered.set()
        release.wait(5)
        return env.recommendations()

    service = env.service_with(blocking_recommendations)
    scheduler = env.scheduler_for(service)
    clock.set(kst(14, 7, 30))
    worker = threading.Thread(target=service.run, args=("manual",))
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(DailyBriefInProgress):
            service.run("manual")
        assert scheduler.tick() == "busy"
        assert env.rt.runs.get_scheduled(date(2026, 11, 14)) is None  # nothing claimed while busy
    finally:
        release.set()
        worker.join(5)
    clock.advance(seconds=30)
    assert scheduler.tick() == "run"
    assert [r.trigger for r in env.rt.runs.all()] == ["manual", "scheduled"]


def test_failed_scheduled_run_retried_10_minutes_later(env: Env, clock: FakeClock) -> None:
    failures = {"left": 1}

    def flaky() -> RecommendationService:
        if failures["left"] > 0:
            failures["left"] -= 1
            raise RuntimeError("database is locked")
        return env.recommendations()

    scheduler = env.scheduler_for(env.service_with(flaky))
    clock.set(kst(14, 7, 30))
    assert scheduler.tick() == "run"  # fails inside, never raises
    row = env.rt.runs.get_scheduled(date(2026, 11, 14))
    assert row is not None and (row.status, row.attempts, row.error_code) == ("failed", 1, "DAILY_BRIEF_ERROR")
    assert env.notifications() == []

    clock.set(kst(14, 7, 39, 59))
    assert scheduler.tick() == "done"  # less than 10 minutes since the failure
    clock.set(kst(14, 7, 40))
    assert scheduler.tick() == "retry"
    row = env.rt.runs.get_scheduled(date(2026, 11, 14))
    assert row is not None and (row.status, row.attempts) == ("sent", 2)
    assert len(env.notifications()) == 1
    clock.set(kst(14, 7, 50))
    assert scheduler.tick() == "done"


def test_failed_scheduled_run_stops_after_three_attempts(env: Env, clock: FakeClock) -> None:
    def broken() -> RecommendationService:
        raise RuntimeError("always")

    scheduler = env.scheduler_for(env.service_with(broken))
    decisions = []
    clock.set(kst(14, 7, 30))
    while clock.now < kst(14, 10, 40):
        decisions.append(scheduler.tick())
        clock.advance(seconds=30)
    assert decisions.count("run") == 1 and decisions.count("retry") == 2
    row = env.rt.runs.get_scheduled(date(2026, 11, 14))
    assert row is not None and (row.status, row.attempts) == ("failed", 3)
    assert env.notifications() == []


def test_unexpected_tick_error_does_not_escape(env: Env, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_day):
        raise RuntimeError("db gone")

    monkeypatch.setattr(env.rt.runs, "get_scheduled", boom)
    clock.set(kst(14, 7, 30))
    assert env.scheduler.tick() == "error"


@pytest.mark.parametrize(
    ("zone", "local_day", "brief"),
    [
        ("America/New_York", date(2026, 3, 8), time(2, 30)),  # spring forward: 02:30 never happens
        ("America/New_York", date(2026, 11, 1), time(1, 30)),  # fall back: 01:30 happens twice
    ],
)
def test_dst_days_run_once(tmp_path: Path, zone: str, local_day: date, brief: time) -> None:
    tz = ZoneInfo(zone)
    start = datetime.combine(local_day, time(0), tzinfo=tz).astimezone(UTC)
    clock = FakeClock(start)
    environment = Env(
        tmp_path, clock, brief_time=brief.strftime("%H:%M"),
        extra={"APP_TIMEZONE": zone, "DEFAULT_LATITUDE": "40.71", "DEFAULT_LONGITUDE": "-74.0"},
    )
    try:
        runs = []
        while clock.now < start + timedelta(hours=8):
            if environment.scheduler.tick() == "run":
                runs.append(clock.now.astimezone(tz))
            clock.advance(seconds=30)
        assert len(runs) == 1
        assert runs[0].date() == local_day
        window_start = datetime.combine(local_day, brief, tzinfo=tz)
        assert window_start <= runs[0] < window_start + CATCH_UP
        assert len(environment.notifications()) == 1
    finally:
        environment.close()


# --- brief content --------------------------------------------------------------------------


def test_brief_uses_location_weather_and_top_recommendation(env: Env, clock: FakeClock) -> None:
    repo = env.container.repository
    temp = TODAY_DEFAULT["actual_temperature_c"]
    far = add_outfit(repo, temp + 4.0, clothing("t_shirt", "none", "shorts", ["white"]), "hot")
    near = add_outfit(repo, temp - 0.1, clothing("knit", "none", "jeans", ["cream"]), "comfortable")
    env.container.location.report(37.5665, 126.978, 20.0)
    result = env.rt.daily_brief.run("manual")
    record = result.notification
    assert record is not None and far != near
    assert record.outfit_id == near and record.url == f"/outfits/{near}"
    assert record.title == "오늘의 착장 브리핑 (fixture 날씨)"
    assert record.body.startswith(f"오늘 서울 {temp:.1f}°C")
    assert "크림 니트를 입고 '적당했어'라고 했어요." in record.body
    assert record.weather_provider == "fixture" and record.weather_is_fixture is True
    assert record.brief_date == date(2026, 11, 14)
    assert result.deliveries == [DeliveryResult("log", "sent")]


def test_insufficient_data_brief_is_sent(env: Env, clock: FakeClock) -> None:
    clock.set(kst(14, 7, 30))
    assert env.scheduler.tick() == "run"
    (item,) = env.notifications()
    assert item.notification.body.endswith(INSUFFICIENT_DATA_TEXT)
    assert item.notification.url == "/" and item.notification.outfit_id is None
    assert item.delivery == DeliverySummary(log_recorded=True)
    row = env.rt.runs.get_scheduled(date(2026, 11, 14))
    assert row is not None and row.status == "sent" and row.notification_id == item.notification.id


class FakeVision:
    name = "fixture"

    def __init__(self, analyses: dict[str, VisionAnalysis]) -> None:
        self.analyses = analyses

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis:
        return self.analyses[sha256]


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def test_skipped_and_uncertain_photos_not_in_brief(tmp_path: Path, env: Env) -> None:
    import hashlib

    folder = tmp_path / "gate"
    folder.mkdir()
    knit = ClothingTags(upper_type="knit", outer_type="none", lower_type="jeans",
                        colors=["cream"], layers_visible=1, confidence=0.9)
    cases = {
        "food.png": ((10, 10, 10), VisionAnalysis(photo_type="non_outfit", photo_type_confidence=0.99,
                                                  reason="Food.", tags=UNKNOWN_TAGS)),
        "blurry.png": ((20, 20, 20), VisionAnalysis(photo_type="uncertain", photo_type_confidence=0.5,
                                                    reason="Blurry.", tags=knit)),
        "low.png": ((30, 30, 30), VisionAnalysis(photo_type="outfit", photo_type_confidence=0.69,
                                                 reason="Maybe.", tags=knit)),
    }
    analyses: dict[str, VisionAnalysis] = {}
    for name, (color, analysis) in cases.items():
        data = _png(color)
        (folder / name).write_bytes(data)
        (folder / f"{Path(name).stem}.metadata.json").write_text(
            json.dumps({"captured_at": "2025-11-20T08:30:00+09:00", "latitude": 37.5665, "longitude": 126.978})
        )
        analyses[hashlib.sha256(data).hexdigest()] = analysis
    ingestion = ImageIngestionService(
        folder, FakeVision(analyses), env.container.fixture_weather, env.container.repository, SEOUL
    )
    for name in cases:
        assert isinstance(ingestion.ingest_file(folder / name), NotOutfit)
    assert env.container.repository.list_outfits() == []

    body = env.rt.daily_brief.run("manual").notification.body  # type: ignore[union-attr]
    assert body.endswith(INSUFFICIENT_DATA_TEXT)

    stored = add_outfit(env.container.repository, TODAY_DEFAULT["actual_temperature_c"], vote="cold")
    second = env.rt.daily_brief.run("manual").notification
    assert second is not None and second.outfit_id == stored
    assert "'추웠어'" in second.body


# --- runtime --------------------------------------------------------------------------------


def test_runtime_scheduler_off_by_default(tmp_path: Path, clock: FakeClock) -> None:
    config = make_config(tmp_path)  # DAILY_BRIEF_ENABLED=false
    container = build_container(config)
    try:
        rt = build_brief_runtime(config, container, clock=clock)
        assert rt.brief_scheduler is None
        assert rt.daily_brief is not None  # manual runs still work (Req 13.3)
        assert rt.notifier.name == "log"
        assert rt.forecast is None
        assert rt.daily_brief.run("manual").notification is not None
    finally:
        container.engine.dispose()


# --- CLI ------------------------------------------------------------------------------------


def _load_cli():
    spec = importlib.util.spec_from_file_location("daily_brief_cli", REPO_ROOT / "scripts" / "daily_brief.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["daily_brief_cli"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_cli_daily_brief_idempotent(tmp_path: Path, clock: FakeClock, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()
    config = make_config(tmp_path)
    clock.set(kst(14, 6, 0))  # before DAILY_BRIEF_TIME: cron may call it any time

    assert cli.main([], config=config, clock=clock) == 0
    first = capsys.readouterr().out
    assert "오늘의 착장 브리핑" in first and "오늘 서울" in first

    assert cli.main([], config=config, clock=clock) == 0
    second = capsys.readouterr().out
    assert second.startswith("이미 보냄")

    assert cli.main(["--force"], config=config, clock=clock) == 0
    assert "오늘의 착장 브리핑" in capsys.readouterr().out

    container = build_container(config)
    try:
        rt = build_brief_runtime(config, container, clock=clock)
        assert len(rt.notification_log.list_recent(10)) == 2
        assert [(r.trigger, r.status) for r in rt.runs.all()] == [("scheduled", "sent"), ("manual", "sent")]
        # The server's scheduler sees the CLI's run and does not send again.
        clock.set(kst(14, 7, 30))
        enabled = make_config(tmp_path, daily_brief_enabled=True)
        scheduler = build_brief_runtime(enabled, container, clock=clock).brief_scheduler
        assert scheduler is not None and scheduler.tick() == "done"
    finally:
        container.engine.dispose()
