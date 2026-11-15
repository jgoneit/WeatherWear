"""Notification_Log: notifications and their per-channel delivery results (design.md §15).

- A notification is stored **before** any Notifier sees it (Req 14.1), so a
  failed or crashing delivery never loses the record (``deliver``).
- ``list_recent`` orders by ``created_at`` DESC, then ``id`` DESC (Req 14.5,
  14.8); ``created_at`` is fixed-width UTC ISO text with microseconds, so text
  order is time order.
- Delivery summaries count ``notification_deliveries`` rows: log recorded,
  webpush sent / failed / gone (Req 14.5).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.db import from_iso, session_scope, to_utc_iso
from app.db_automation import Notification, NotificationDelivery, PushSubscription
from app.domain import WeatherContext
from app.notifications.compose import BriefMessage

logger = logging.getLogger(__name__)

NotificationKind = Literal["daily_brief", "push_test"]
Channel = Literal["log", "webpush"]
DeliveryStatus = Literal["sent", "failed", "gone"]

NOTIFIER_ERROR = "NOTIFIER_ERROR"


@dataclass(frozen=True)
class NotificationRecord:
    id: int
    kind: NotificationKind
    title: str
    body: str
    url: str
    outfit_id: int | None
    brief_date: date | None
    weather_provider: str | None
    weather_is_fixture: bool | None
    created_at: datetime  # aware, UTC


@dataclass(frozen=True)
class DeliveryResult:
    channel: Channel
    status: DeliveryStatus
    subscription_id: int | None = None
    http_status: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class DeliverySummary:
    log_recorded: bool = False
    webpush_sent: int = 0
    webpush_failed: int = 0
    webpush_gone: int = 0


@dataclass(frozen=True)
class NotificationWithSummary:
    notification: NotificationRecord
    delivery: DeliverySummary


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _record(row: Notification) -> NotificationRecord:
    assert row.id is not None
    return NotificationRecord(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        body=row.body,
        url=row.url,
        outfit_id=row.outfit_id,
        brief_date=date.fromisoformat(row.brief_date) if row.brief_date else None,
        weather_provider=row.weather_provider,
        weather_is_fixture=row.weather_is_fixture,
        created_at=from_iso(row.created_at),
    )


def summarize(rows: Sequence[tuple[str, str]]) -> DeliverySummary:
    """Summary of (channel, status) pairs."""
    webpush = [status for channel, status in rows if channel == "webpush"]
    return DeliverySummary(
        log_recorded=any(channel == "log" and status == "sent" for channel, status in rows),
        webpush_sent=webpush.count("sent"),
        webpush_failed=webpush.count("failed"),
        webpush_gone=webpush.count("gone"),
    )


class NotificationLog:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def create(
        self,
        kind: NotificationKind,
        message: BriefMessage,
        *,
        brief_date: date | None,
        weather: WeatherContext | None,
        before_commit: Callable[[Session, int], None] | None = None,
    ) -> NotificationRecord:
        """Store one notification (Req 14.1). Raises on a database error.

        ``before_commit(session, notification_id)`` runs inside the same
        transaction, so whatever it writes commits together with the row.
        """
        row = Notification(
            kind=kind,
            title=message.title,
            body=message.body,
            url=message.url,
            outfit_id=message.outfit_id,
            brief_date=brief_date.isoformat() if brief_date else None,
            weather_provider=weather.provider if weather else None,
            weather_is_fixture=weather.is_fixture if weather else None,
            created_at=to_utc_iso(self._clock()),
        )
        with session_scope(self._engine) as session:
            session.add(row)
            session.flush()
            record = _record(row)
            if before_commit is not None:
                before_commit(session, record.id)
        logger.info("알림을 기록했습니다: id=%d kind=%s", record.id, record.kind)
        return record

    def add_deliveries(self, notification_id: int, results: Sequence[DeliveryResult]) -> None:
        """Store delivery results. A subscription that no longer exists (deleted
        while sending, or ``gone``) is stored as NULL, matching ON DELETE SET NULL."""
        if not results:
            return
        now = to_utc_iso(self._clock())
        with session_scope(self._engine) as session:
            wanted = {r.subscription_id for r in results if r.subscription_id is not None}
            existing = set(
                session.execute(
                    select(PushSubscription.id).where(PushSubscription.id.in_(wanted))  # type: ignore[union-attr]
                ).scalars()
            ) if wanted else set()
            for result in results:
                session.add(
                    NotificationDelivery(
                        notification_id=notification_id,
                        channel=result.channel,
                        subscription_id=result.subscription_id
                        if result.subscription_id in existing
                        else None,
                        status=result.status,
                        http_status=result.http_status,
                        error_code=result.error_code,
                        created_at=now,
                    )
                )

    def get(self, notification_id: int) -> NotificationWithSummary | None:
        with Session(self._engine) as session:
            row = session.get(Notification, notification_id)
            if row is None:
                return None
            summaries = self._summaries(session, [notification_id])
            return NotificationWithSummary(_record(row), summaries.get(notification_id, DeliverySummary()))

    def list_recent(self, limit: int, kind: NotificationKind | None = None) -> list[NotificationWithSummary]:
        """Newest first: ``created_at`` DESC, ``id`` DESC (Req 14.5, 14.8)."""
        statement = select(Notification)
        if kind is not None:
            statement = statement.where(Notification.kind == kind)
        statement = statement.order_by(
            Notification.created_at.desc(),  # type: ignore[attr-defined]
            Notification.id.desc(),  # type: ignore[union-attr]
        ).limit(limit)
        with Session(self._engine) as session:
            rows = list(session.execute(statement).scalars())
            summaries = self._summaries(session, [r.id for r in rows if r.id is not None])
            return [
                NotificationWithSummary(_record(r), summaries.get(r.id, DeliverySummary()))  # type: ignore[arg-type]
                for r in rows
            ]

    def latest_for_date(self, kind: NotificationKind, local_date: date, tz: ZoneInfo) -> NotificationRecord | None:
        """Newest notification of ``kind`` created on ``local_date`` in ``tz``."""
        start = datetime.combine(local_date, time(0), tzinfo=tz)
        end = datetime.combine(local_date + timedelta(days=1), time(0), tzinfo=tz)
        statement = (
            select(Notification)
            .where(Notification.kind == kind)
            .where(Notification.created_at >= to_utc_iso(start))
            .where(Notification.created_at < to_utc_iso(end))
            .order_by(Notification.created_at.desc(), Notification.id.desc())  # type: ignore[attr-defined,union-attr]
            .limit(1)
        )
        with Session(self._engine) as session:
            row = session.execute(statement).scalars().first()
            return _record(row) if row is not None else None

    def count(self) -> int:
        with Session(self._engine) as session:
            return int(session.execute(select(func.count()).select_from(Notification)).scalar_one())

    def deliveries_of(self, notification_id: int) -> list[DeliveryResult]:
        statement = (
            select(NotificationDelivery)
            .where(NotificationDelivery.notification_id == notification_id)
            .order_by(NotificationDelivery.id)  # type: ignore[arg-type]
        )
        with Session(self._engine) as session:
            return [
                DeliveryResult(
                    channel=row.channel,  # type: ignore[arg-type]
                    status=row.status,  # type: ignore[arg-type]
                    subscription_id=row.subscription_id,
                    http_status=row.http_status,
                    error_code=row.error_code,
                )
                for row in session.execute(statement).scalars()
            ]

    @staticmethod
    def _summaries(session: Session, ids: list[int]) -> dict[int, DeliverySummary]:
        if not ids:
            return {}
        pairs: dict[int, list[tuple[str, str]]] = {i: [] for i in ids}
        statement = select(
            NotificationDelivery.notification_id, NotificationDelivery.channel, NotificationDelivery.status
        ).where(NotificationDelivery.notification_id.in_(ids))  # type: ignore[attr-defined]
        for notification_id, channel, status in session.execute(statement):
            pairs[notification_id].append((channel, status))
        return {i: summarize(p) for i, p in pairs.items()}
