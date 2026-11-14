"""Notification_Log, Log_Notifier and the Notifier factory (Phase 2 tasks 4.6, Req 14.1, 14.2, 14.4, 14.8)."""

from __future__ import annotations

import logging
import socket
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.db import create_db_engine
from app.domain import WeatherContext
from app.notifications.compose import BriefMessage
from app.notifications.notifier import LogNotifier, Notifier, build_notifier, deliver
from app.notifications.push_store import PushSubscriptionStore
from app.notifications.records import (
    NOTIFIER_ERROR,
    DeliveryResult,
    DeliverySummary,
    NotificationLog,
    NotificationRecord,
)
from app.notifications.vapid import VapidKeyStore
from app.notifications.webpush import WebPushNotifier
from app.schemas_automation import PushSubscriptionIn
from tests.notifications_support import FakeClock, FakePushSender, make_config, subscription_json

SEOUL = ZoneInfo("Asia/Seoul")
MESSAGE = BriefMessage(title="오늘의 착장 브리핑", body="본문", url="/", outfit_id=None)
WEATHER = WeatherContext(
    actual_temperature_c=14.5,
    apparent_temperature_c=None,
    relative_humidity_pct=55.0,
    wind_speed_kmh=None,
    precipitation_mm=None,
    observed_at=datetime(2026, 11, 14, 7, 0, tzinfo=SEOUL),
    provider="fixture",
    is_fixture=True,
    scenario_id=None,
    scenario_name=None,
)


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'records.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 13, 22, 30, tzinfo=UTC))  # 07:30 KST


@pytest.fixture
def log(engine, clock: FakeClock) -> NotificationLog:
    return NotificationLog(engine, clock)


class ExplodingNotifier:
    name = "log"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, notification: NotificationRecord) -> list[DeliveryResult]:
        self.calls += 1
        raise RuntimeError("boom")


def test_create_stores_all_fields(log: NotificationLog, clock: FakeClock) -> None:
    record = log.create("daily_brief", MESSAGE, brief_date=date(2026, 11, 14), weather=WEATHER)
    assert record.id > 0
    assert record.kind == "daily_brief"
    assert (record.title, record.body, record.url, record.outfit_id) == ("오늘의 착장 브리핑", "본문", "/", None)
    assert record.brief_date == date(2026, 11, 14)
    assert record.weather_provider == "fixture" and record.weather_is_fixture is True
    assert record.created_at == clock.now
    stored = log.get(record.id)
    assert stored is not None and stored.notification == record
    assert stored.delivery == DeliverySummary()


def test_record_stored_before_delivery(log: NotificationLog) -> None:
    seen: list[int] = []

    class CountingNotifier:
        name = "log"

        def send(self, notification: NotificationRecord) -> list[DeliveryResult]:
            seen.append(log.count())  # the record already exists when the notifier runs
            return [DeliveryResult("log", "sent")]

    record = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    results = deliver(log, CountingNotifier(), record)
    assert seen == [1]
    assert results == [DeliveryResult("log", "sent")]
    assert log.get(record.id).delivery == DeliverySummary(log_recorded=True)  # type: ignore[union-attr]


def test_notifier_exception_keeps_record_and_records_failure(log: NotificationLog) -> None:
    record = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    notifier = ExplodingNotifier()
    results = deliver(log, notifier, record)
    assert notifier.calls == 1
    assert results == [DeliveryResult("log", "failed", error_code=NOTIFIER_ERROR)]
    assert log.count() == 1
    assert log.deliveries_of(record.id) == results
    assert log.get(record.id).delivery == DeliverySummary(log_recorded=False)  # type: ignore[union-attr]


def test_log_notifier_logs_id_and_kind_only(
    log: NotificationLog, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_connect(self, address):  # noqa: ANN001
        raise AssertionError("LogNotifier must not use the network")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    record = log.create("daily_brief", BriefMessage("제목XYZ", "본문XYZ", "/", None), brief_date=None, weather=None)
    caplog.set_level(logging.INFO, logger="app.notifications.notifier")
    results = LogNotifier().send(record)
    assert results == [DeliveryResult("log", "sent")]
    lines = [r.getMessage() for r in caplog.records if r.name == "app.notifications.notifier"]
    assert lines == [f"알림 기록: id={record.id} kind=daily_brief"]
    assert "XYZ" not in caplog.text


def test_list_order_and_kind_filter(log: NotificationLog, clock: FakeClock) -> None:
    first = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    same_time = log.create("push_test", MESSAGE, brief_date=None, weather=None)  # same created_at
    clock.advance(seconds=1)
    newest = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    clock.advance(seconds=-30)  # clock went backwards
    older = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)

    ids = [item.notification.id for item in log.list_recent(20)]
    assert ids == [newest.id, same_time.id, first.id, older.id]
    assert [i.notification.id for i in log.list_recent(2)] == [newest.id, same_time.id]
    assert [i.notification.id for i in log.list_recent(20, "push_test")] == [same_time.id]
    assert [i.notification.id for i in log.list_recent(20, "daily_brief")] == [newest.id, first.id, older.id]


def test_delivery_summary_counts(log: NotificationLog, engine) -> None:
    store = PushSubscriptionStore(engine)
    subs = [store.upsert(PushSubscriptionIn.model_validate(subscription_json(i)), "pk") for i in (1, 2, 3)]
    record = log.create("push_test", MESSAGE, brief_date=None, weather=None)
    log.add_deliveries(
        record.id,
        [
            DeliveryResult("webpush", "sent", subscription_id=subs[0], http_status=201),
            DeliveryResult("webpush", "failed", subscription_id=subs[1], http_status=500),
            DeliveryResult("webpush", "gone", subscription_id=None, http_status=410),
            DeliveryResult("webpush", "sent", subscription_id=subs[2], http_status=201),
        ],
    )
    other = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    log.add_deliveries(other.id, [DeliveryResult("log", "sent")])

    summary = {i.notification.id: i.delivery for i in log.list_recent(10)}
    assert summary[record.id] == DeliverySummary(False, 2, 1, 1)
    assert summary[other.id] == DeliverySummary(True, 0, 0, 0)
    assert len(log.deliveries_of(record.id)) == 4


def test_deliveries_for_deleted_subscription_are_kept(log: NotificationLog, engine) -> None:
    store = PushSubscriptionStore(engine)
    sub_id = store.upsert(PushSubscriptionIn.model_validate(subscription_json(1)), "pk")
    store.delete(sub_id)
    record = log.create("push_test", MESSAGE, brief_date=None, weather=None)
    log.add_deliveries(record.id, [DeliveryResult("webpush", "sent", subscription_id=sub_id, http_status=201)])
    assert log.deliveries_of(record.id) == [DeliveryResult("webpush", "sent", None, 201)]


def test_latest_for_local_date(log: NotificationLog, clock: FakeClock) -> None:
    clock.set(datetime(2026, 11, 13, 14, 59, tzinfo=UTC))  # 23:59 KST on the 13th
    log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    clock.set(datetime(2026, 11, 13, 15, 0, tzinfo=UTC))  # 00:00 KST on the 14th
    first_today = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    clock.advance(hours=2)
    latest_today = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    log.create("push_test", MESSAGE, brief_date=None, weather=None)

    found = log.latest_for_date("daily_brief", date(2026, 11, 14), SEOUL)
    assert found is not None and found.id == latest_today.id != first_today.id
    assert log.latest_for_date("daily_brief", date(2026, 11, 15), SEOUL) is None


def test_build_notifier_chooses_by_config(tmp_path: Path, engine) -> None:
    store = PushSubscriptionStore(engine)
    keys = VapidKeyStore(tmp_path / "secrets")
    log_cfg = make_config(tmp_path, notifier="log")
    push_cfg = make_config(tmp_path, notifier="webpush")
    log_notifier = build_notifier(log_cfg, push_store=store, keys=keys, sender=FakePushSender())
    push_notifier = build_notifier(push_cfg, push_store=store, keys=keys, sender=FakePushSender())
    assert isinstance(log_notifier, LogNotifier) and log_notifier.name == "log"
    assert isinstance(push_notifier, WebPushNotifier) and push_notifier.name == "webpush"
    assert isinstance(log_notifier, Notifier) and isinstance(push_notifier, Notifier)


def test_notification_count_matches_requests(log: NotificationLog) -> None:
    notifiers = [LogNotifier(), ExplodingNotifier(), LogNotifier()]
    for notifier in notifiers:
        record = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
        deliver(log, notifier, record)
    assert log.count() == 3
    assert len(log.list_recent(100)) == 3


def test_created_at_has_microseconds(log: NotificationLog, clock: FakeClock) -> None:
    clock.set(datetime(2026, 11, 14, 0, 0, 0, 123456, tzinfo=UTC))
    record = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    assert record.created_at.microsecond == 123456
    clock.advance(microseconds=1)
    later = log.create("daily_brief", MESSAGE, brief_date=None, weather=None)
    assert [i.notification.id for i in log.list_recent(2)] == [later.id, record.id]
    assert later.created_at - record.created_at == timedelta(microseconds=1)
