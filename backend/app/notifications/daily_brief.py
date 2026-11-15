"""Daily_Brief_Service and the run table (design.md §18, Req 11.1, 11.9, 11.13, 12.3~12.6, 13.1).

``DailyBriefService.run(trigger)``:

1. A non-blocking lock; when another brief is being made →
   ``DailyBriefInProgress`` (API 409 ``DAILY_BRIEF_IN_PROGRESS``, Req 13.2).
2. ``scheduled``: claim the date in ``daily_brief_runs``. The partial unique
   index ``ux_daily_brief_scheduled`` (one scheduled row per date) is what
   makes a scheduled brief happen at most once per local date, across
   restarts, worker processes and the CLI (Req 12.3). A ``failed`` row with
   fewer than 3 attempts is taken back with a conditional UPDATE (Req 12.5).
   So is an *interrupted* row (``is_interrupted``): still ``running`` without a
   notification ``STALE_RUNNING_AFTER`` after it started, because the process
   that claimed it died. Neither possible → ``BriefRunResult(skipped=True)``. ``manual`` runs add a
   row of their own and never block the scheduled one (Req 13.1).
3. Brief location → current weather → ``recommendations_for`` → ``compose_brief``
   → Notification_Log → Notifier → delivery results → run ``sent``.

Weather problems and "no data" are not failures: the Current_Weather_Service
falls back to fixture weather and the composer has an insufficient-data text
(Req 11.9, 12.5). Any other exception marks the run ``failed`` and propagates.
The notification row and the run's ``notification_id`` are written in one
transaction. Once a notification is stored, the run is never taken back (a
process dying during delivery leaves ``running`` *with* a notification id) and
counts as sent even if recording the delivery results fails, so a retry never
sends the same day's brief twice.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session

from app.db import from_iso, session_scope, to_utc_iso
from app.db_automation import DailyBriefRun
from app.location import LocationService
from app.notifications.compose import BriefInput, compose_brief
from app.notifications.current_weather import CurrentWeatherService
from app.notifications.notifier import Notifier, deliver
from app.notifications.places import place_label as default_place_label
from app.notifications.records import DeliveryResult, NotificationLog, NotificationRecord
from app.recommendation.service import RecommendationService

logger = logging.getLogger(__name__)

Trigger = Literal["scheduled", "manual"]
RunStatus = Literal["running", "sent", "failed"]

MAX_SCHEDULED_ATTEMPTS = 3
# A brief stores its notification within seconds (weather calls have short
# timeouts). A scheduled run still ``running`` without one this long after it
# started belongs to a process that died; it counts as failed (review fix 4).
STALE_RUNNING_AFTER = timedelta(minutes=10)
STORAGE_ERROR = "STORAGE_ERROR"
BRIEF_ERROR = "DAILY_BRIEF_ERROR"


class DailyBriefInProgress(Exception):
    """Another brief is being made in this process (API 409)."""


@dataclass(frozen=True)
class RunRow:
    id: int
    brief_date: date
    trigger: Trigger
    status: RunStatus
    attempts: int
    notification_id: int | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None

    @property
    def last_attempt_at(self) -> datetime:
        return self.finished_at or self.started_at


@dataclass(frozen=True)
class BriefRunResult:
    trigger: Trigger
    brief_date: date
    skipped: bool
    run_id: int | None = None
    notification: NotificationRecord | None = None
    deliveries: list[DeliveryResult] = field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_interrupted(run: RunRow, now: datetime) -> bool:
    """A ``running`` row with no notification, started at least ``STALE_RUNNING_AFTER`` ago."""
    return run.status == "running" and run.notification_id is None and now >= run.started_at + STALE_RUNNING_AFTER


def counts_as_failed(run: RunRow, now: datetime) -> bool:
    """``failed``, or interrupted: what the retry rules of Req 12.5 apply to."""
    return run.status == "failed" or is_interrupted(run, now)


def _run_row(row: DailyBriefRun) -> RunRow:
    assert row.id is not None
    return RunRow(
        id=row.id,
        brief_date=date.fromisoformat(row.brief_date),
        trigger=row.trigger,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        attempts=row.attempts,
        notification_id=row.notification_id,
        error_code=row.error_code,
        started_at=from_iso(row.started_at),
        finished_at=from_iso(row.finished_at) if row.finished_at else None,
    )


class DailyBriefRuns:
    """Rows of ``daily_brief_runs``. Claims rely on the database, not on memory."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def claim(self, brief_date: date) -> RunRow | None:
        """INSERT the scheduled row of ``brief_date``; None when it already exists."""
        row = DailyBriefRun(
            brief_date=brief_date.isoformat(),
            trigger="scheduled",
            status="running",
            attempts=1,
            started_at=to_utc_iso(self._clock()),
        )
        try:
            with session_scope(self._engine) as session:
                session.add(row)
                session.flush()
                return _run_row(row)
        except IntegrityError:
            return None

    def retry(self, brief_date: date) -> RunRow | None:
        """Take back the scheduled row of ``brief_date`` for one more attempt; None otherwise.

        Only a row that ``counts_as_failed`` (``failed``, or interrupted) with
        attempts < 3 (Req 12.5). The UPDATE repeats the status, attempts and
        empty notification id just read. Every attempt changes one of them, so
        when two processes race for the same row exactly one gets it.
        """
        now = self._clock()
        current = self.get_scheduled(brief_date)
        if current is None or current.attempts >= MAX_SCHEDULED_ATTEMPTS or not counts_as_failed(current, now):
            return None
        statement = (
            update(DailyBriefRun)
            .where(DailyBriefRun.id == current.id)
            .where(DailyBriefRun.status == current.status)
            .where(DailyBriefRun.attempts == current.attempts)
            .where(DailyBriefRun.notification_id.is_(None))  # type: ignore[union-attr]
            .values(
                status="running",
                attempts=current.attempts + 1,
                started_at=to_utc_iso(now),
                finished_at=None,
                error_code=None,
            )
        )
        with self._engine.begin() as conn:
            result = conn.execute(statement)
        if (result.rowcount or 0) != 1:
            return None
        if current.status == "running":
            logger.warning(
                "%s 예약 브리핑이 중단된 채 남아 있어 실패로 보고 다시 시도합니다 (시도 %d회째)",
                brief_date.isoformat(),
                current.attempts + 1,
            )
        return self.get_scheduled(brief_date)

    @staticmethod
    def link_notification(session: Session, run_id: int, notification_id: int) -> None:
        """Record the run's notification inside the transaction that stores it."""
        session.execute(
            update(DailyBriefRun).where(DailyBriefRun.id == run_id).values(notification_id=notification_id)
        )

    def effective_status(self, run: RunRow) -> RunStatus:
        """The stored status, but ``failed`` for an interrupted run (Automation_Status)."""
        return "failed" if is_interrupted(run, self._clock()) else run.status

    def start_manual(self, brief_date: date) -> RunRow:
        row = DailyBriefRun(
            brief_date=brief_date.isoformat(),
            trigger="manual",
            status="running",
            attempts=1,
            started_at=to_utc_iso(self._clock()),
        )
        with session_scope(self._engine) as session:
            session.add(row)
            session.flush()
            return _run_row(row)

    def finish(
        self,
        run_id: int,
        status: Literal["sent", "failed"],
        *,
        notification_id: int | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(DailyBriefRun)
                .where(DailyBriefRun.id == run_id)
                .values(
                    status=status,
                    notification_id=notification_id,
                    error_code=error_code,
                    finished_at=to_utc_iso(self._clock()),
                )
            )

    def get_scheduled(self, brief_date: date) -> RunRow | None:
        statement = (
            select(DailyBriefRun)
            .where(DailyBriefRun.brief_date == brief_date.isoformat())
            .where(DailyBriefRun.trigger == "scheduled")
        )
        with Session(self._engine) as session:
            row = session.execute(statement).scalars().first()
            return _run_row(row) if row is not None else None

    def latest_scheduled(self) -> RunRow | None:
        """The scheduled run of the latest date (Automation_Status ``last_scheduled``)."""
        statement = (
            select(DailyBriefRun)
            .where(DailyBriefRun.trigger == "scheduled")
            .order_by(DailyBriefRun.brief_date.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        with Session(self._engine) as session:
            row = session.execute(statement).scalars().first()
            return _run_row(row) if row is not None else None

    def all(self) -> list[RunRow]:
        with Session(self._engine) as session:
            rows = session.execute(select(DailyBriefRun).order_by(DailyBriefRun.id)).scalars()  # type: ignore[arg-type]
            return [_run_row(r) for r in rows]


def _error_code(exc: BaseException) -> str:
    return STORAGE_ERROR if isinstance(exc, SQLAlchemyError) else BRIEF_ERROR


class DailyBriefService:
    def __init__(
        self,
        location: LocationService,
        current_weather: CurrentWeatherService,
        recommendations: Callable[[], RecommendationService],
        log: NotificationLog,
        notifier: Notifier,
        runs: DailyBriefRuns,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = _utc_now,
        *,
        place_label: Callable[[float, float], str] = default_place_label,
        lock: threading.Lock | None = None,
    ) -> None:
        self._location = location
        self._current_weather = current_weather
        self._recommendations = recommendations
        self._log = log
        self._notifier = notifier
        self._runs = runs
        self._tz = tz
        self._clock = clock
        self._place_label = place_label
        self._lock = lock or threading.Lock()

    @property
    def runs(self) -> DailyBriefRuns:
        return self._runs

    @property
    def notifier(self) -> Notifier:
        return self._notifier

    def today(self) -> date:
        return self._clock().astimezone(self._tz).date()

    def run(self, trigger: Trigger, brief_date: date | None = None) -> BriefRunResult:
        """Make one brief. Raises ``DailyBriefInProgress`` while another one runs."""
        if not self._lock.acquire(blocking=False):
            raise DailyBriefInProgress()
        try:
            day = brief_date or self.today()
            if trigger == "scheduled":
                run = self._runs.claim(day) or self._runs.retry(day)
                if run is None:
                    logger.info("%s 예약 브리핑은 이미 처리되었습니다.", day.isoformat())
                    return BriefRunResult(trigger=trigger, brief_date=day, skipped=True)
            else:
                run = self._runs.start_manual(day)
            return self._make(run, trigger, day)
        finally:
            self._lock.release()

    def _make(self, run: RunRow, trigger: Trigger, day: date) -> BriefRunResult:
        try:
            point, _source = self._location.brief_location()
            today = self._current_weather.get_current(point)
            response = self._recommendations().recommendations_for(today)
            top = response.items[0] if response.items else None
            message = compose_brief(
                BriefInput(place=self._place_label(point.latitude, point.longitude), today=today, top=top)
            )
            record = self._log.create(
                "daily_brief",
                message,
                brief_date=day,
                weather=today,
                before_commit=lambda session, notification_id: self._runs.link_notification(
                    session, run.id, notification_id
                ),
            )
        except Exception as exc:
            code = _error_code(exc)
            logger.error("매일 브리핑 실패(%s, %s): %s", trigger, day.isoformat(), type(exc).__name__)
            try:
                self._runs.finish(run.id, "failed", error_code=code)
            except Exception as finish_exc:  # pragma: no cover - database gone
                logger.error("브리핑 실패 기록도 실패했습니다 (%s)", type(finish_exc).__name__)
            raise

        # The notification exists from here on: deliver, then mark the run sent
        # whatever happens, so a retry cannot send the same brief again.
        results = deliver(self._log, self._notifier, record)
        try:
            self._runs.finish(run.id, "sent", notification_id=record.id)
        except Exception as exc:
            logger.error("브리핑 실행 결과를 기록하지 못했습니다 (%s)", type(exc).__name__)
        logger.info("매일 브리핑을 보냈습니다: trigger=%s date=%s id=%d", trigger, day.isoformat(), record.id)
        return BriefRunResult(
            trigger=trigger,
            brief_date=day,
            skipped=False,
            run_id=run.id,
            notification=record,
            deliveries=results,
        )
