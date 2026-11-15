"""Push subscriptions and WebPush_Notifier with a fake Push_Sender (Phase 2 tasks 4.9).

Req 14.3, 15.5, 15.7, 15.8, 18.7. ``pywebpush`` is imported but never sends:
``PyWebPushSender`` is exercised with a patched ``webpush`` function only.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from app.db import create_db_engine
from app.notifications.compose import BriefMessage
from app.notifications.notifier import deliver
from app.notifications.push_store import (
    DEFAULT_PUSH_HOSTS,
    InvalidSubscription,
    PushSubscriptionStore,
    endpoint_host,
    validate_subscription,
)
from app.notifications.records import DeliveryResult, DeliverySummary, NotificationLog
from app.notifications.vapid import VapidKeyStore
from app.notifications.webpush import (
    MAX_PAYLOAD_BYTES,
    PUSH_ENDPOINT_NOT_ALLOWED,
    PUSH_TTL_SECONDS,
    PyWebPushSender,
    SendResult,
    WebPushNotifier,
    build_payload,
    classify_push_status,
)
from app.schemas_automation import PushSubscriptionIn
from tests.notifications_support import FakeClock, FakePushSender, b64url, subscription_json

SUBJECT = "mailto:weatherwear@example.com"


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'push.db'}")
    yield eng
    eng.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 13, 22, 30, tzinfo=UTC))


@pytest.fixture
def store(engine, clock) -> PushSubscriptionStore:
    return PushSubscriptionStore(engine, clock)


@pytest.fixture
def keys(tmp_path: Path) -> VapidKeyStore:
    return VapidKeyStore(tmp_path / "secrets")


@pytest.fixture
def log(engine, clock) -> NotificationLog:
    return NotificationLog(engine, clock)


def sub_in(n: int = 1, **override) -> PushSubscriptionIn:
    data = subscription_json(n)
    if "endpoint" in override:
        data["endpoint"] = override.pop("endpoint")
    data["keys"].update(override)
    return PushSubscriptionIn.model_validate(data)


def add(store: PushSubscriptionStore, keys: VapidKeyStore, *numbers: int) -> list[int]:
    public = keys.ensure().public_key_b64url
    return [store.upsert(sub_in(n), public) for n in numbers]


def brief(log: NotificationLog, body: str = "본문") -> object:
    message = BriefMessage(title="오늘의 착장 브리핑", body=body, url="/outfits/3", outfit_id=None)
    return log.create("daily_brief", message, brief_date=None, weather=None)


def notifier(store, keys, sender) -> WebPushNotifier:
    return WebPushNotifier(store, keys, sender, subject=SUBJECT)


# --- validation ----------------------------------------------------------------


def test_valid_subscription_passes() -> None:
    assert validate_subscription(sub_in()) is None
    padded = subscription_json(1)
    padded["keys"]["auth"] += "=="
    assert validate_subscription(PushSubscriptionIn.model_validate(padded)) is None


@pytest.mark.parametrize(
    "override",
    [
        {"endpoint": "http://push.example.com/x"},
        {"endpoint": "ftp://push.example.com/x"},
        {"endpoint": "https://"},
        {"endpoint": "https://push.example.com/" + "a" * 2030},
        {"endpoint": "https://push.example.com/a b"},
        {"p256dh": b64url(b"\x04" + b"\x01" * 63)},  # 64 bytes
        {"p256dh": b64url(b"\x03" + b"\x01" * 64)},  # compressed marker
        {"p256dh": "not base64!"},
        {"p256dh": ""},
        {"auth": b64url(b"\x00" * 15)},
        {"auth": b64url(b"\x00" * 17)},
        {"auth": "+/+/+/+/+/+/+/+/+/+/+w"},  # standard base64, not base64url
    ],
)
def test_invalid_subscriptions_rejected(override: dict, store: PushSubscriptionStore) -> None:
    payload = sub_in(1, **override)
    reason = validate_subscription(payload)
    assert reason is not None
    assert payload.endpoint not in reason and "push.example.com" not in reason
    for value in (payload.keys.p256dh, payload.keys.auth):
        assert not value or value not in reason
    with pytest.raises(InvalidSubscription):
        store.upsert(payload, "pk")
    assert store.count() == 0


def test_endpoint_length_boundary() -> None:
    base = "https://fcm.googleapis.com/"
    assert validate_subscription(sub_in(endpoint=base + "a" * (2048 - len(base)))) is None
    assert validate_subscription(sub_in(endpoint=base + "a" * (2049 - len(base)))) is not None


# --- store ---------------------------------------------------------------------------


def test_duplicate_endpoint_upserts(store: PushSubscriptionStore) -> None:
    first = store.upsert(sub_in(1), "pk1")
    data = subscription_json(2)
    data["endpoint"] = subscription_json(1)["endpoint"]
    second = store.upsert(PushSubscriptionIn.model_validate(data), "pk2")
    assert first == second
    assert store.count() == 1
    row = store.get(first)
    assert row is not None
    assert row.p256dh == data["keys"]["p256dh"] and row.auth == data["keys"]["auth"]
    assert row.vapid_public_key == "pk2"
    assert "secret-token" not in repr(row)


def test_delete_and_list(store: PushSubscriptionStore) -> None:
    a = store.upsert(sub_in(1), "pk")
    b = store.upsert(sub_in(2), "pk")
    assert [r.id for r in store.list_active()] == [a, b]
    assert store.delete(a) is True
    assert store.delete(a) is False
    assert [r.id for r in store.list_active()] == [b]


def test_delete_not_matching_key(store: PushSubscriptionStore) -> None:
    store.upsert(sub_in(1), "old")
    keep = store.upsert(sub_in(2), "new")
    store.upsert(sub_in(3), "old")
    assert store.delete_not_matching_key("new") == 2
    assert [r.id for r in store.list_active()] == [keep]


def test_corrupt_key_rotation_clears_old_subscriptions(tmp_path: Path, store: PushSubscriptionStore) -> None:
    key_dir = tmp_path / "rot"
    first = VapidKeyStore(key_dir)
    old_public = first.ensure().public_key_b64url
    store.upsert(sub_in(1), old_public)
    (key_dir / "vapid_private.pem").write_text("broken")
    rotating = VapidKeyStore(key_dir, on_rotated=store.delete_not_matching_key)
    new_public = rotating.ensure().public_key_b64url
    assert new_public != old_public
    assert store.count() == 0


# --- classification and payload --------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (SendResult(200, None), "sent"),
        (SendResult(201, None), "sent"),
        (SendResult(202, None), "sent"),
        (SendResult(404, None), "gone"),
        (SendResult(410, None), "gone"),
        (SendResult(400, None), "failed"),
        (SendResult(403, None), "failed"),
        (SendResult(413, None), "failed"),
        (SendResult(429, None), "failed"),
        (SendResult(500, None), "failed"),
        (SendResult(503, None), "failed"),
        (SendResult(None, "PUSH_TIMEOUT"), "failed"),
        (SendResult(None, "PUSH_CONNECTION_ERROR"), "failed"),
    ],
)
def test_classify_push_status(result: SendResult, status: str) -> None:
    assert classify_push_status(result) == status


def test_payload_is_truncated_to_limit(log: NotificationLog) -> None:
    record = brief(log, body="가" * 2000)  # 6000 bytes of body
    payload = build_payload(record)
    assert len(payload) <= MAX_PAYLOAD_BYTES
    data = json.loads(payload)
    assert set(data) == {"title", "body", "url", "notification_id", "kind"}
    assert data["body"] and set(data["body"]) == {"가"}


# --- WebPushNotifier ------------------------------------------------------------------


def test_payload_shape_ttl_subject(store, keys, log) -> None:
    (sub_id,) = add(store, keys, 1)
    sender = FakePushSender()
    record = brief(log)
    results = notifier(store, keys, sender).send(record)
    assert results == [DeliveryResult("webpush", "sent", subscription_id=sub_id, http_status=201)]
    (call,) = sender.calls
    assert call["ttl"] == PUSH_TTL_SECONDS == 43200
    assert call["subject"] == SUBJECT
    assert call["keys"] == keys.ensure()
    assert json.loads(call["payload"]) == {
        "title": "오늘의 착장 브리핑",
        "body": "본문",
        "url": "/outfits/3",
        "notification_id": record.id,
        "kind": "daily_brief",
    }
    assert len(call["payload"]) <= MAX_PAYLOAD_BYTES


def test_410_deletes_subscription(store, keys, log) -> None:
    a, b = add(store, keys, 1, 2)
    sender = FakePushSender(results={subscription_json(1)["endpoint"]: SendResult(410, None)})
    record = brief(log)
    results = deliver(log, notifier(store, keys, sender), record)
    assert [r.status for r in results] == ["gone", "sent"]
    assert results[0].subscription_id is None and results[0].http_status == 410
    assert [r.id for r in store.list_active()] == [b]
    assert log.get(record.id).delivery == DeliverySummary(False, 1, 0, 1)  # type: ignore[union-attr]
    assert len(sender.calls) == 2


def test_404_deletes_subscription(store, keys, log) -> None:
    (a,) = add(store, keys, 1)
    sender = FakePushSender(default=SendResult(404, None))
    results = deliver(log, notifier(store, keys, sender), brief(log))
    assert [r.status for r in results] == ["gone"]
    assert store.count() == 0


def test_500_keeps_subscription(store, keys, log) -> None:
    (a,) = add(store, keys, 1)
    sender = FakePushSender(default=SendResult(500, None))
    record = brief(log)
    results = deliver(log, notifier(store, keys, sender), record)
    assert results == [DeliveryResult("webpush", "failed", subscription_id=a, http_status=500)]
    assert [r.id for r in store.list_active()] == [a]
    assert len(sender.calls) == 1  # not retried
    assert log.get(record.id).delivery == DeliverySummary(False, 0, 1, 0)  # type: ignore[union-attr]


def test_timeout_and_sender_exception_are_failed(store, keys, log) -> None:
    a, b = add(store, keys, 1, 2)
    sender = FakePushSender(
        results={
            subscription_json(1)["endpoint"]: SendResult(None, "PUSH_TIMEOUT"),
            subscription_json(2)["endpoint"]: RuntimeError("encrypt failed"),
        }
    )
    results = notifier(store, keys, sender).send(brief(log))
    assert [(r.status, r.error_code) for r in results] == [
        ("failed", "PUSH_TIMEOUT"),
        ("failed", "PUSH_SEND_ERROR"),
    ]
    assert store.count() == 2


def test_zero_subscriptions_records_only(store, keys, log) -> None:
    sender = FakePushSender()
    record = brief(log)
    results = deliver(log, notifier(store, keys, sender), record)
    assert results == [] and sender.calls == []
    assert log.count() == 1
    assert log.get(record.id).delivery == DeliverySummary()  # type: ignore[union-attr]


def test_missing_keys_records_only(tmp_path: Path, store, log) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    broken_keys = VapidKeyStore(blocker / "secrets")
    store.upsert(sub_in(1), "pk")
    sender = FakePushSender()
    record = brief(log)
    assert deliver(log, notifier(store, broken_keys, sender), record) == []
    assert sender.calls == [] and log.count() == 1


def test_logs_have_id_and_host_only(store, keys, log, caplog: pytest.LogCaptureFixture) -> None:
    add(store, keys, 1)
    data = subscription_json(1)
    caplog.set_level(logging.DEBUG)
    sender = FakePushSender(default=SendResult(410, None))
    notifier(store, keys, sender).send(brief(log))
    text = caplog.text
    assert "fcm.googleapis.com" in text
    assert data["endpoint"] not in text and "secret-token" not in text
    assert data["keys"]["p256dh"] not in text and data["keys"]["auth"] not in text
    assert endpoint_host(data["endpoint"]) == "fcm.googleapis.com"


# --- PyWebPushSender (pywebpush patched, no network) ----------------------------------------


def test_pywebpush_sender_maps_results(monkeypatch: pytest.MonkeyPatch, store, keys) -> None:
    import pywebpush

    (sub_id,) = add(store, keys, 1)
    sub = store.get(sub_id)
    calls: list[dict] = []

    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    outcomes: list[object] = [
        Response(201),
        pywebpush.WebPushException("Push failed: 410 Gone body", response=Response(410)),
        requests.Timeout("slow"),
        requests.ConnectionError("refused"),
        pywebpush.WebPushException("no response"),
    ]

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    sender = PyWebPushSender()
    vapid = keys.ensure()
    got = [sender.send(sub, b'{"a":1}', ttl=PUSH_TTL_SECONDS, keys=vapid, subject=SUBJECT) for _ in range(5)]
    assert got == [
        SendResult(201, None),
        SendResult(410, None),
        SendResult(None, "PUSH_TIMEOUT"),
        SendResult(None, "PUSH_CONNECTION_ERROR"),
        SendResult(None, "PUSH_SEND_ERROR"),
    ]
    first = calls[0]
    assert first["subscription_info"] == {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }
    assert first["vapid_private_key"] == str(vapid.private_key_path)
    assert first["vapid_claims"] == {"sub": SUBJECT}
    assert first["ttl"] == PUSH_TTL_SECONDS and first["timeout"] == 10.0
    assert first["data"] == '{"a":1}'


# --- review fix (2): push endpoints are limited to known push service hosts ------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://android.googleapis.com/gcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
        "https://web.push.apple.com/QGx",
        "https://fcm.googleapis.com:443/fcm/send/abc",
    ],
)
def test_known_push_service_hosts_accepted(endpoint: str) -> None:
    assert validate_subscription(sub_in(endpoint=endpoint)) is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://attacker.example/collect",
        "https://169.254.169.254/latest/meta-data",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "https://fcm.googleapis.com.evil.example/x",
        "https://evilfcm.googleapis.com/x",
        "https://user@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
        "https://notify.windows.com/x",  # the wildcard needs a subdomain
    ],
)
def test_other_hosts_rejected_without_echoing_them(endpoint: str, store: PushSubscriptionStore) -> None:
    reason = validate_subscription(sub_in(endpoint=endpoint))
    assert reason is not None and endpoint not in reason
    with pytest.raises(InvalidSubscription):
        store.upsert(sub_in(endpoint=endpoint), "public")
    assert store.count() == 0


def test_push_allowed_hosts_extends_the_list(engine, clock) -> None:
    extended = PushSubscriptionStore(engine, clock, allowed_hosts=(*DEFAULT_PUSH_HOSTS, "push.example.com"))
    assert extended.upsert(sub_in(endpoint="https://push.example.com/x"), "public") > 0
    assert validate_subscription(sub_in(endpoint="https://push.example.com/x")) is not None  # not by default


def test_stored_endpoint_outside_allowlist_is_not_contacted(engine, clock, keys, log) -> None:
    """A row saved before the allowlist (or by hand) never makes the server call its host."""
    from app.db import session_scope
    from app.db_automation import PushSubscription

    data = subscription_json(1)
    with session_scope(engine) as session:
        session.add(PushSubscription(
            endpoint="https://attacker.example/collect", p256dh=data["keys"]["p256dh"],
            auth=data["keys"]["auth"], vapid_public_key=keys.ensure().public_key_b64url,
            created_at="2026-11-14T00:00:00.000000+00:00",
        ))
    store = PushSubscriptionStore(engine, clock)
    sender = FakePushSender()
    results = notifier(store, keys, sender).send(brief(log))
    assert sender.calls == []
    assert [(r.status, r.error_code) for r in results] == [("failed", PUSH_ENDPOINT_NOT_ALLOWED)]
