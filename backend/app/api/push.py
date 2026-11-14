"""Web Push routes (design.md §19, flow B, Req 15).

Services come from ``deps.get_vapid_keys``, ``deps.get_push_store``,
``deps.get_notifier`` and ``deps.get_notification_log``. A body that does not
match ``PushSubscriptionIn`` becomes 422 ``PUSH_SUBSCRIPTION_INVALID`` in
``main.py``; the format rules of Req 15.5 are checked here with
``validate_subscription`` and answer the same code. The VAPID private key,
endpoints and subscription keys are never returned (Req 15.4, 18.7).

A folder that cannot hold the VAPID key answers 503 ``VAPID_KEY_UNAVAILABLE``
on the public key and test routes (Req 15.10), and on subscription create,
which needs the key the subscription was made with.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Response, status

from app.api.notifications import deferred, to_notification_out
from app.deps import ApiError, get_notification_log, get_notifier, get_push_store, get_vapid_keys
from app.notifications.compose import BriefMessage
from app.notifications.notifier import deliver
from app.notifications.push_store import validate_subscription
from app.notifications.vapid import VapidKeyUnavailable
from app.schemas import ErrorResponse
from app.schemas_automation import (
    NotificationOut,
    PushSubscriptionCreatedOut,
    PushSubscriptionIn,
    VapidPublicKeyOut,
)

router = APIRouter(prefix="/api/push", tags=["push"])

VAPID_UNAVAILABLE_MESSAGE = "푸시 키를 준비하지 못했습니다."
SUBSCRIPTION_INVALID_MESSAGE = "푸시 구독 정보가 올바르지 않습니다."
SUBSCRIPTION_NOT_FOUND_MESSAGE = "푸시 구독을 찾을 수 없습니다."
WEB_PUSH_DISABLED_MESSAGE = "웹 푸시가 꺼져 있습니다."

TEST_TITLE = "알림이 켜졌어요"
TEST_BODY = "이제 매일 아침 착장 브리핑을 이 기기로 받아요."
TEST_URL = "/notifications"


def _keys_or_503(keys: Any):
    try:
        return keys.ensure()
    except VapidKeyUnavailable:
        raise ApiError(503, "VAPID_KEY_UNAVAILABLE", VAPID_UNAVAILABLE_MESSAGE) from None


@router.get(
    "/vapid-public-key",
    response_model=VapidPublicKeyOut,
    responses={503: {"model": ErrorResponse}},
)
def vapid_public_key(keys: Callable[[], Any] = Depends(deferred(get_vapid_keys))) -> VapidPublicKeyOut:
    """base64url public key only; 503 ``VAPID_KEY_UNAVAILABLE`` (Req 15.1, 15.4, 15.10)."""
    return VapidPublicKeyOut(public_key=_keys_or_503(keys()).public_key_b64url)


@router.post(
    "/subscriptions",
    response_model=PushSubscriptionCreatedOut,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorResponse}},
)
def create_push_subscription(
    body: PushSubscriptionIn,
    store: Callable[[], Any] = Depends(deferred(get_push_store)),
    keys: Callable[[], Any] = Depends(deferred(get_vapid_keys)),
) -> PushSubscriptionCreatedOut:
    """Upsert by endpoint; 422 ``PUSH_SUBSCRIPTION_INVALID`` (Req 15.5)."""
    if validate_subscription(body) is not None:
        raise ApiError(422, "PUSH_SUBSCRIPTION_INVALID", SUBSCRIPTION_INVALID_MESSAGE)
    subscriptions, vapid = store(), keys()
    public_key = _keys_or_503(vapid).public_key_b64url
    return PushSubscriptionCreatedOut(id=subscriptions.upsert(body, public_key))


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
def delete_push_subscription(
    subscription_id: int, store: Callable[[], Any] = Depends(deferred(get_push_store))
) -> Response:
    """204; 404 ``PUSH_SUBSCRIPTION_NOT_FOUND`` (Req 15.6)."""
    if not store().delete(subscription_id):
        raise ApiError(404, "PUSH_SUBSCRIPTION_NOT_FOUND", SUBSCRIPTION_NOT_FOUND_MESSAGE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/test",
    response_model=NotificationOut,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def send_push_test(
    notifier_dep: Callable[[], Any] = Depends(deferred(get_notifier)),
    keys: Callable[[], Any] = Depends(deferred(get_vapid_keys)),
    log_dep: Callable[[], Any] = Depends(deferred(get_notification_log)),
) -> NotificationOut:
    """"알림이 켜졌어요" test push; 409 ``WEB_PUSH_DISABLED`` with NOTIFIER=log (Req 15.9)."""
    notifier = notifier_dep()
    if getattr(notifier, "name", "log") != "webpush":
        raise ApiError(409, "WEB_PUSH_DISABLED", WEB_PUSH_DISABLED_MESSAGE)
    _keys_or_503(keys())
    log = log_dep()
    record = log.create(
        "push_test",
        BriefMessage(title=TEST_TITLE, body=TEST_BODY, url=TEST_URL, outfit_id=None),
        brief_date=None,
        weather=None,
    )
    deliver(log, notifier, record)
    return to_notification_out(log.get(record.id))
