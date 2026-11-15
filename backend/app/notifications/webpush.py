"""Web Push delivery (design.md §17, Req 14.3, 15.7, 15.8, 15.10, 18.7).

- ``PushSender`` sends one request. ``PyWebPushSender`` is the only place that
  imports ``pywebpush`` (RFC 8291 encryption, RFC 8292 VAPID signature; its
  HTTP goes through ``requests``, so tests inject a fake sender instead).
  Checked against pywebpush 2.5.0: ``webpush(subscription_info, data,
  vapid_private_key, vapid_claims, content_encoding, curl, timeout, ttl, ...)``;
  ``vapid_private_key`` may be a PEM file path; any status above 202 raises
  ``WebPushException`` carrying ``.response``.
- ``classify_push_status``: 2xx → ``sent``, 404/410 → ``gone``, everything
  else (other 4xx, 5xx, timeout, connection error) → ``failed``.
- ``WebPushNotifier`` sends each stored subscription exactly once, deletes a
  ``gone`` subscription and never resends. Without VAPID keys it delivers
  nothing (the notification record stays, Req 15.10).

Logs carry the subscription id and the endpoint host name only (Req 18.7);
pywebpush exception messages (which include response bodies) are never logged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.notifications.push_store import (
    PushSubscriptionRow,
    PushSubscriptionStore,
    endpoint_allowed,
    endpoint_host,
)
from app.notifications.records import DeliveryResult, DeliveryStatus, NotificationRecord
from app.notifications.vapid import VapidKeys, VapidKeyStore, VapidKeyUnavailable

logger = logging.getLogger(__name__)

PUSH_TTL_SECONDS = 12 * 3600
MAX_PAYLOAD_BYTES = 3000
SEND_TIMEOUT_S = 10.0

PUSH_TIMEOUT = "PUSH_TIMEOUT"
PUSH_CONNECTION_ERROR = "PUSH_CONNECTION_ERROR"
PUSH_SEND_ERROR = "PUSH_SEND_ERROR"  # encryption/signing or another local error
# A stored endpoint whose host is not on the allowlist (e.g. saved before the
# allowlist existed). Nothing is sent to it (review fix 2).
PUSH_ENDPOINT_NOT_ALLOWED = "PUSH_ENDPOINT_NOT_ALLOWED"


@dataclass(frozen=True)
class SendResult:
    status_code: int | None
    error_code: str | None  # None / PUSH_TIMEOUT / PUSH_CONNECTION_ERROR / PUSH_SEND_ERROR


@runtime_checkable
class PushSender(Protocol):
    def send(
        self, sub: PushSubscriptionRow, payload: bytes, *, ttl: int, keys: VapidKeys, subject: str
    ) -> SendResult: ...


class PyWebPushSender:
    """Real sender: ``pywebpush.webpush`` with a 10 s timeout."""

    def __init__(self, timeout_s: float = SEND_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def send(
        self, sub: PushSubscriptionRow, payload: bytes, *, ttl: int, keys: VapidKeys, subject: str
    ) -> SendResult:
        import requests
        from pywebpush import WebPushException, webpush

        try:
            response = webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload.decode("utf-8"),
                vapid_private_key=str(keys.private_key_path),
                vapid_claims={"sub": subject},  # pywebpush adds aud/exp to this dict
                ttl=ttl,
                timeout=self._timeout_s,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is None:
                return SendResult(status_code=None, error_code=PUSH_SEND_ERROR)
            return SendResult(status_code=int(status), error_code=None)
        except requests.Timeout:
            return SendResult(status_code=None, error_code=PUSH_TIMEOUT)
        except requests.ConnectionError:
            return SendResult(status_code=None, error_code=PUSH_CONNECTION_ERROR)
        except requests.RequestException:
            return SendResult(status_code=None, error_code=PUSH_CONNECTION_ERROR)
        status_code = getattr(response, "status_code", None)
        return SendResult(status_code=int(status_code) if status_code is not None else None, error_code=None)


def classify_push_status(result: SendResult) -> DeliveryStatus:
    """2xx → sent, 404/410 → gone, anything else → failed (Req 15.7)."""
    code = result.status_code
    if code is not None and 200 <= code <= 299:
        return "sent"
    if code in (404, 410):
        return "gone"
    return "failed"


def _dump(title: str, body: str, n: NotificationRecord) -> bytes:
    return json.dumps(
        {"title": title, "body": body, "url": n.url, "notification_id": n.id, "kind": n.kind},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def build_payload(n: NotificationRecord, max_bytes: int = MAX_PAYLOAD_BYTES) -> bytes:
    """JSON {"title","body","url","notification_id","kind"} in UTF-8, at most
    ``max_bytes``; the body (then the title) is shortened when needed (Req 15.8)."""
    title, body = n.title, n.body
    data = _dump(title, body, n)
    while len(data) > max_bytes and body:
        body = body[: max(0, len(body) - max(1, (len(data) - max_bytes) // 3))]
        data = _dump(title, body, n)
    while len(data) > max_bytes and title:
        title = title[: max(0, len(title) - max(1, (len(data) - max_bytes) // 3))]
        data = _dump(title, body, n)
    return data


class WebPushNotifier:
    name: Literal["webpush"] = "webpush"

    def __init__(
        self,
        store: PushSubscriptionStore,
        keys: VapidKeyStore,
        sender: PushSender,
        *,
        subject: str,
        ttl_seconds: int = PUSH_TTL_SECONDS,
    ) -> None:
        self._store = store
        self._keys = keys
        self._sender = sender
        self._subject = subject
        self._ttl = ttl_seconds

    def send(self, notification: NotificationRecord) -> list[DeliveryResult]:
        try:
            keys = self._keys.ensure()
        except VapidKeyUnavailable:
            logger.warning("VAPID 키가 없어 웹 푸시 없이 알림만 기록합니다: id=%d", notification.id)
            return []
        payload = build_payload(notification)
        results: list[DeliveryResult] = []
        for sub in self._store.list_active():
            if not endpoint_allowed(sub.endpoint, self._store.allowed_hosts):
                logger.warning(
                    "허용 목록 밖 푸시 서비스라 보내지 않습니다: subscription_id=%d host=%s",
                    sub.id,
                    endpoint_host(sub.endpoint),
                )
                self._store.mark_failure(sub.id)
                results.append(
                    DeliveryResult(
                        channel="webpush",
                        status="failed",
                        subscription_id=sub.id,
                        http_status=None,
                        error_code=PUSH_ENDPOINT_NOT_ALLOWED,
                    )
                )
                continue
            try:
                outcome = self._sender.send(sub, payload, ttl=self._ttl, keys=keys, subject=self._subject)
            except Exception as exc:
                logger.warning("웹 푸시 전송 예외: subscription_id=%d (%s)", sub.id, type(exc).__name__)
                outcome = SendResult(status_code=None, error_code=PUSH_SEND_ERROR)
            status = classify_push_status(outcome)
            logger.info(
                "웹 푸시 전달: notification_id=%d subscription_id=%d host=%s status=%s http=%s",
                notification.id,
                sub.id,
                endpoint_host(sub.endpoint),
                status,
                outcome.status_code,
            )
            subscription_id: int | None = sub.id
            if status == "gone":
                self._store.delete(sub.id)
                subscription_id = None  # the row is gone (ON DELETE SET NULL)
            elif status == "sent":
                self._store.mark_success(sub.id)
            else:
                self._store.mark_failure(sub.id)
            results.append(
                DeliveryResult(
                    channel="webpush",
                    status=status,
                    subscription_id=subscription_id,
                    http_status=outcome.status_code,
                    error_code=outcome.error_code if status != "sent" else None,
                )
            )
        return results
