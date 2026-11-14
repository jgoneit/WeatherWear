"""Notification, daily brief and push routes (Phase 2 tasks 4.13).

Req 13.1~13.3, 14.5, 15.4, 15.5, 15.6, 15.9, 15.10. The flow B services are
put into the app with ``app.dependency_overrides`` (tasks 7.1 wires the
container later), built by ``build_brief_runtime`` on the app's own container.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import (
    get_daily_brief_service,
    get_notification_log,
    get_notifier,
    get_push_store,
    get_vapid_keys,
)
from app.main import create_app
from app.notifications.compose import BriefMessage
from app.notifications.daily_brief import DailyBriefService
from app.notifications.runtime import BriefRuntime, build_brief_runtime
from app.notifications.vapid import PRIVATE_NAME
from app.notifications.webpush import SendResult
from app.recommendation.service import RecommendationService
from tests.notifications_support import FakeClock, FakePushSender, add_outfit, make_config, subscription_json


class Api:
    def __init__(self, tmp_path: Path, *, notifier: str = "log", key_dir: Path | None = None,
                 sender: FakePushSender | None = None) -> None:
        extra = {"VAPID_KEY_DIR": str(key_dir)} if key_dir is not None else None
        config = make_config(tmp_path, notifier=notifier, extra=extra)
        self.app = create_app(config)
        self.container = self.app.state.container
        self.clock = FakeClock(datetime(2026, 11, 13, 22, 30, tzinfo=UTC))
        self.sender = sender or FakePushSender()
        self.rt: BriefRuntime = build_brief_runtime(
            self.app.state.config, self.container, clock=self.clock, sender=self.sender
        )
        self.use_service(self.rt.daily_brief)
        overrides = self.app.dependency_overrides
        overrides[get_notification_log] = lambda: self.rt.notification_log
        overrides[get_push_store] = lambda: self.rt.push_store
        overrides[get_vapid_keys] = lambda: self.rt.vapid_keys
        overrides[get_notifier] = lambda: self.rt.notifier

    def use_service(self, service: DailyBriefService) -> None:
        self.app.dependency_overrides[get_daily_brief_service] = lambda: service

    def __enter__(self) -> TestClient:
        self.client = TestClient(self.app).__enter__()
        return self.client

    def __exit__(self, *exc) -> None:
        self.client.__exit__(*exc)


def message(n: int) -> BriefMessage:
    return BriefMessage(title=f"제목 {n}", body=f"본문 {n}", url="/", outfit_id=None)


# --- GET /api/notifications --------------------------------------------------------


def test_list_notifications_order_limit_and_kind(tmp_path: Path) -> None:
    api = Api(tmp_path)
    log = api.rt.notification_log
    ids = []
    for n, kind in enumerate(["daily_brief", "push_test", "daily_brief"]):
        ids.append(log.create(kind, message(n), brief_date=None, weather=None).id)  # same created_at
        api.clock.advance(seconds=1 if n == 0 else 0)
    with api as client:
        body = client.get("/api/notifications").json()
        assert [item["id"] for item in body] == [ids[2], ids[1], ids[0]]
        assert body[0]["delivery"] == {
            "log_recorded": False, "webpush_sent": 0, "webpush_failed": 0, "webpush_gone": 0,
        }
        assert datetime.fromisoformat(body[0]["created_at"]).utcoffset() is not None
        assert [i["id"] for i in client.get("/api/notifications?limit=1").json()] == [ids[2]]
        assert [i["id"] for i in client.get("/api/notifications?kind=push_test").json()] == [ids[1]]
        for bad in ("limit=0", "limit=101", "kind=other", "limit=x"):
            response = client.get(f"/api/notifications?{bad}")
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- POST /api/daily-brief/run ----------------------------------------------------------


def test_run_daily_brief_returns_notification(tmp_path: Path) -> None:
    api = Api(tmp_path)  # DAILY_BRIEF_ENABLED=false: manual runs still work (Req 13.3)
    outfit_id = add_outfit(api.container.repository, 14.4, vote="comfortable")
    with api as client:
        response = client.post("/api/daily-brief/run")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["trigger"] == "manual"
        note = body["notification"]
        assert note["kind"] == "daily_brief"
        assert note["title"].startswith("오늘의 착장 브리핑")
        assert note["outfit_id"] == outfit_id
        assert note["url"] == f"/outfits/{outfit_id}"
        assert note["photo_url"] == f"/api/outfits/{outfit_id}/photo"
        assert note["weather_provider"] == "fixture" and note["weather_is_fixture"] is True
        assert note["delivery"]["log_recorded"] is True
        listed = client.get("/api/notifications").json()
        assert [i["id"] for i in listed] == [note["id"]]
        assert listed[0] == note


def test_concurrent_run_returns_409(tmp_path: Path) -> None:
    api = Api(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocking() -> RecommendationService:
        entered.set()
        release.wait(5)
        return RecommendationService(api.container.today_weather, api.container.repository, api.container.strategy)

    rt = api.rt
    service = DailyBriefService(
        api.container.location, rt.current_weather, blocking, rt.notification_log, rt.notifier,
        rt.runs, api.app.state.config.app_timezone, api.clock,
    )
    api.use_service(service)
    worker = threading.Thread(target=service.run, args=("manual",))
    with api as client:
        worker.start()
        try:
            assert entered.wait(5)
            response = client.post("/api/daily-brief/run")
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "DAILY_BRIEF_IN_PROGRESS"
        finally:
            release.set()
            worker.join(5)
        assert rt.notification_log.count() == 1  # only the first brief
        assert client.post("/api/daily-brief/run").status_code == 200


# --- push ---------------------------------------------------------------------------------


def test_public_key_only_in_response(tmp_path: Path) -> None:
    api = Api(tmp_path, notifier="webpush")
    with api as client:
        response = client.get("/api/push/vapid-public-key")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"public_key"}
        key_dir = api.app.state.config.automation.vapid_key_dir
        assert body["public_key"] == (key_dir / "vapid_public.txt").read_text().strip()
        pem = (key_dir / PRIVATE_NAME).read_text()
        secret = "".join(line for line in pem.splitlines() if not line.startswith("-----"))
        assert "PRIVATE" not in response.text and secret[:16] not in response.text
        assert str(key_dir) not in response.text
        again = client.get("/api/push/vapid-public-key").json()
        assert again == body  # the key is created once


def test_vapid_dir_unwritable_returns_503(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder")
    api = Api(tmp_path, notifier="webpush", key_dir=blocker / "secrets")
    with api as client:
        for method, path in (("get", "/api/push/vapid-public-key"), ("post", "/api/push/test")):
            response = getattr(client, method)(path)
            assert response.status_code == 503, path
            assert response.json() == {
                "error": {"code": "VAPID_KEY_UNAVAILABLE", "message": "푸시 키를 준비하지 못했습니다."}
            }
            assert str(blocker) not in response.text
        response = client.post("/api/push/subscriptions", json=subscription_json(1))
        assert response.status_code == 503
        # The brief is still recorded, just not pushed (Req 15.10).
        assert client.post("/api/daily-brief/run").status_code == 200
        assert api.rt.notification_log.count() == 1


def test_create_subscription_201_and_upsert(tmp_path: Path) -> None:
    api = Api(tmp_path, notifier="webpush")
    with api as client:
        first = client.post("/api/push/subscriptions", json=subscription_json(1))
        assert first.status_code == 201
        assert set(first.json()) == {"id"}
        second_body = subscription_json(2)
        second_body["endpoint"] = subscription_json(1)["endpoint"]
        second = client.post("/api/push/subscriptions", json=second_body)
        assert second.status_code == 201 and second.json() == first.json()
        assert api.rt.push_store.count() == 1
        row = api.rt.push_store.get(first.json()["id"])
        assert row is not None and row.vapid_public_key == api.rt.vapid_keys.ensure().public_key_b64url
        assert subscription_json(1)["endpoint"] not in first.text


@pytest.mark.parametrize(
    "payload",
    [
        {"endpoint": "http://push.example.com/x", "keys": subscription_json(1)["keys"]},
        {**subscription_json(1), "keys": {"p256dh": "short", "auth": subscription_json(1)["keys"]["auth"]}},
        {**subscription_json(1), "keys": {"p256dh": subscription_json(1)["keys"]["p256dh"], "auth": "AAAA"}},
        {"endpoint": "https://push.example.com/x"},  # shape: no keys
        {"endpoint": 5, "keys": subscription_json(1)["keys"]},
        [],
    ],
)
def test_invalid_subscription_422(tmp_path: Path, payload) -> None:
    api = Api(tmp_path, notifier="webpush")
    with api as client:
        response = client.post("/api/push/subscriptions", json=payload)
        assert response.status_code == 422
        assert response.json() == {
            "error": {"code": "PUSH_SUBSCRIPTION_INVALID", "message": "푸시 구독 정보가 올바르지 않습니다."}
        }
        assert api.rt.push_store.count() == 0


def test_subscription_delete_404(tmp_path: Path) -> None:
    api = Api(tmp_path, notifier="webpush")
    with api as client:
        sub_id = client.post("/api/push/subscriptions", json=subscription_json(1)).json()["id"]
        response = client.delete(f"/api/push/subscriptions/{sub_id}")
        assert response.status_code == 204 and response.content == b""
        missing = client.delete(f"/api/push/subscriptions/{sub_id}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "PUSH_SUBSCRIPTION_NOT_FOUND"
        assert client.delete("/api/push/subscriptions/999").status_code == 404


def test_push_test_disabled_with_log_notifier(tmp_path: Path) -> None:
    api = Api(tmp_path, notifier="log")
    with api as client:
        response = client.post("/api/push/test")
        assert response.status_code == 409
        assert response.json() == {"error": {"code": "WEB_PUSH_DISABLED", "message": "웹 푸시가 꺼져 있습니다."}}
        assert api.rt.notification_log.count() == 0


def test_push_test_sends_to_every_subscription(tmp_path: Path) -> None:
    gone = subscription_json(2)["endpoint"]
    sender = FakePushSender(results={gone: SendResult(410, None)})
    api = Api(tmp_path, notifier="webpush", sender=sender)
    with api as client:
        for n in (1, 2, 3):
            assert client.post("/api/push/subscriptions", json=subscription_json(n)).status_code == 201
        response = client.post("/api/push/test")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kind"] == "push_test" and body["title"] == "알림이 켜졌어요"
        assert body["outfit_id"] is None and body["photo_url"] is None
        assert body["weather_provider"] is None and body["weather_is_fixture"] is None
        assert body["delivery"] == {"log_recorded": False, "webpush_sent": 2, "webpush_failed": 0, "webpush_gone": 1}
        assert len(sender.calls) == 3
        assert api.rt.push_store.count() == 2
        assert [i["kind"] for i in client.get("/api/notifications").json()] == ["push_test"]
