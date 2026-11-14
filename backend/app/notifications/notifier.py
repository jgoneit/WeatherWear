"""Notifier interface, the offline Log_Notifier and the factory (design.md §15, Req 14.2, 14.4).

``send(notification) -> list[DeliveryResult]`` is the only interface. The
factory ``build_notifier`` is the one place that turns NOTIFIER into an
implementation. ``deliver`` is how every caller sends: the notification is
already stored (Req 14.1); a Notifier that raises is recorded as one
``failed`` result with ``NOTIFIER_ERROR`` and the record stays.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, runtime_checkable

from app.config import AppConfig
from app.notifications.push_store import PushSubscriptionStore
from app.notifications.records import (
    NOTIFIER_ERROR,
    DeliveryResult,
    NotificationLog,
    NotificationRecord,
)
from app.notifications.vapid import VapidKeyStore
from app.notifications.webpush import PushSender, WebPushNotifier

logger = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    name: Literal["log", "webpush"]

    def send(self, notification: NotificationRecord) -> list[DeliveryResult]: ...


class LogNotifier:
    """Default Notifier: no network. Logs the id and kind only (Req 14.2)."""

    name: Literal["log"] = "log"

    def send(self, notification: NotificationRecord) -> list[DeliveryResult]:
        logger.info("알림 기록: id=%d kind=%s", notification.id, notification.kind)
        return [DeliveryResult(channel="log", status="sent")]


def build_notifier(
    cfg: AppConfig,
    *,
    push_store: PushSubscriptionStore,
    keys: VapidKeyStore,
    sender: PushSender,
) -> Notifier:
    """NOTIFIER → implementation (Req 14.4): ``log`` → LogNotifier, ``webpush`` → WebPushNotifier."""
    if cfg.automation.notifier == "webpush":
        return WebPushNotifier(push_store, keys, sender, subject=cfg.automation.vapid_subject)
    return LogNotifier()


def deliver(log: NotificationLog, notifier: Notifier, notification: NotificationRecord) -> list[DeliveryResult]:
    """Send a stored notification and store the delivery results (Req 14.1, 14.8).

    Never raises because of the Notifier. A failure to store the results is
    logged; the notification itself was stored before and stays.
    """
    try:
        results = list(notifier.send(notification))
    except Exception as exc:
        logger.error(
            "Notifier %s 전달 실패: id=%d (%s)",
            getattr(notifier, "name", "?"),
            notification.id,
            type(exc).__name__,
        )
        channel = "webpush" if getattr(notifier, "name", "log") == "webpush" else "log"
        results = [DeliveryResult(channel=channel, status="failed", error_code=NOTIFIER_ERROR)]
    try:
        log.add_deliveries(notification.id, results)
    except Exception as exc:
        logger.error("전달 결과를 기록하지 못했습니다: id=%d (%s)", notification.id, type(exc).__name__)
    return results
