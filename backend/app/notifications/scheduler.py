"""Daily_Brief_Scheduler (design.md §18, Req 12.1, 12.4, 12.5, 12.7, 12.8).

Every 30 s the scheduler asks ``scheduled_decision`` what to do for the local
date of "now" (APP_TIMEZONE via ``zoneinfo``):

- before DAILY_BRIEF_TIME → ``wait``;
- at or after the end of the Catch_Up_Window (time + 3 h) → ``missed`` when that
  date has no scheduled run, otherwise ``done`` (Req 12.4);
- no run yet → ``run``;
- a ``failed`` run with fewer than 3 attempts whose last attempt is at least
  10 minutes old → ``retry`` (Req 12.5). A run left ``running`` without a
  notification by a process that died counts as failed once it is
  ``STALE_RUNNING_AFTER`` (10 minutes) old (``counts_as_failed``);
- otherwise → ``done``.

``run``/``retry`` call ``DailyBriefService.run("scheduled")``, which claims the
date in the database, so the decision being wrong or stale (two processes, a
clock that jumps back) can never send twice (Req 12.3, 12.8). The window is
computed on the local wall clock of the date, so a DST gap or overlap still
gives one date key and at most one run (Req 12.7).

``tick()`` never raises; ``run(stop)`` loops until ``stop`` is set. Tests call
``tick()`` directly with a fake clock (no thread, Req 20.2).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.notifications.daily_brief import (
    MAX_SCHEDULED_ATTEMPTS,
    DailyBriefInProgress,
    DailyBriefRuns,
    DailyBriefService,
    RunRow,
    counts_as_failed,
)

logger = logging.getLogger(__name__)

CATCH_UP = timedelta(hours=3)
RETRY_AFTER = timedelta(minutes=10)
TICK_SECONDS = 30.0

Decision = Literal["run", "retry", "wait", "missed", "done"]


def _window(local_date: date, brief_time: time, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(local_date, brief_time, tzinfo=tz)
    # Same tzinfo on both sides: arithmetic and comparisons use the local wall clock.
    return start, start + CATCH_UP


def scheduled_decision(now_local: datetime, brief_time: time, run: RunRow | None) -> Decision:
    """What the scheduler should do now for ``now_local.date()`` (pure function).

    ``now_local`` must be aware and expressed in APP_TIMEZONE (a ``ZoneInfo``);
    ``run`` is that date's scheduled run, if any.
    """
    tz = now_local.tzinfo
    if not isinstance(tz, ZoneInfo):
        raise ValueError("now_local must use a ZoneInfo time zone")
    start, end = _window(now_local.date(), brief_time, tz)
    if now_local < start:
        return "wait"
    if now_local >= end:
        return "missed" if run is None else "done"
    if run is None:
        return "run"
    if (
        counts_as_failed(run, now_local)
        and run.attempts < MAX_SCHEDULED_ATTEMPTS
        and now_local >= run.last_attempt_at + RETRY_AFTER
    ):
        return "retry"
    return "done"


def next_run_at(now: datetime, brief_time: time, tz: ZoneInfo, done_today: bool) -> datetime:
    """Next scheduled brief time for Automation_Status (aware, in ``tz``).

    Today's DAILY_BRIEF_TIME while it is still ahead; "now" while today's
    Catch_Up_Window is open and nothing ran yet; otherwise tomorrow's time.
    """
    local = now.astimezone(tz)
    start, end = _window(local.date(), brief_time, tz)
    if not done_today:
        if local < start:
            return start
        if local < end:
            return local
    elif local < start:
        return start
    tomorrow, _ = _window(local.date() + timedelta(days=1), brief_time, tz)
    return tomorrow


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DailyBriefScheduler:
    def __init__(
        self,
        service: DailyBriefService,
        runs: DailyBriefRuns,
        brief_time: time,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = _utc_now,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._service = service
        self._runs = runs
        self._brief_time = brief_time
        self._tz = tz
        self._clock = clock
        self._tick_seconds = tick_seconds
        self.last_tick_at: datetime | None = None
        self.last_decision: str | None = None

    @property
    def brief_time(self) -> time:
        return self._brief_time

    def next_run_at(self) -> datetime:
        local = self._clock().astimezone(self._tz)
        run = self._runs.get_scheduled(local.date())
        done = run is not None and not (
            counts_as_failed(run, local) and run.attempts < MAX_SCHEDULED_ATTEMPTS
        )
        return next_run_at(local, self._brief_time, self._tz, done)

    def tick(self) -> str:
        """One check; never raises.

        Returns the decision. ``run``/``retry`` are returned even when the brief
        itself failed (the service already marked the run ``failed`` for a
        retry); ``busy`` means a manual brief held the lock, and ``error`` that
        the decision itself could not be made.
        """
        try:
            now_local = self._clock().astimezone(self._tz)
            self.last_tick_at = now_local
            run = self._runs.get_scheduled(now_local.date())
            decision: str = scheduled_decision(now_local, self._brief_time, run)
        except Exception as exc:
            logger.error("매일 브리핑 스케줄러 오류 (%s)", type(exc).__name__)
            self.last_decision = "error"
            return "error"
        if decision in ("run", "retry"):
            try:
                self._service.run("scheduled", now_local.date())
            except DailyBriefInProgress:
                decision = "busy"  # a manual brief holds the lock; the next tick tries again
            except Exception as exc:
                logger.error("예약 브리핑이 실패했습니다; 10분 뒤 다시 시도합니다 (%s)", type(exc).__name__)
        self.last_decision = decision
        return decision

    def run(self, stop: threading.Event) -> None:
        self.tick()
        while not stop.wait(self._tick_seconds):
            self.tick()
