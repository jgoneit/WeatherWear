"""Web Push routes (design.md §19, flow B, Req 15).

Skeleton from tasks 1.7; bodies are filled by tasks 4.13 with the services from
``deps.get_vapid_keys``, ``deps.get_push_store`` and ``deps.get_notifier``.
A body that does not match ``PushSubscriptionIn`` becomes 422
``PUSH_SUBSCRIPTION_INVALID`` in ``main.py``. The VAPID private key, endpoints
and subscription keys are never returned (Req 15.4, 18.7).
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.deps import not_implemented
from app.schemas import ErrorResponse
from app.schemas_automation import (
    NotificationOut,
    PushSubscriptionCreatedOut,
    PushSubscriptionIn,
    VapidPublicKeyOut,
)

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get(
    "/vapid-public-key",
    response_model=VapidPublicKeyOut,
    responses={503: {"model": ErrorResponse}},
)
def vapid_public_key() -> VapidPublicKeyOut:
    """base64url public key only; 503 ``VAPID_KEY_UNAVAILABLE`` (Req 15.1, 15.4, 15.10)."""
    # TODO(4.13): VapidKeyStore.ensure() -> public key.
    not_implemented("4.13")


@router.post(
    "/subscriptions",
    response_model=PushSubscriptionCreatedOut,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorResponse}},
)
def create_push_subscription(body: PushSubscriptionIn) -> PushSubscriptionCreatedOut:
    """Upsert by endpoint; 422 ``PUSH_SUBSCRIPTION_INVALID`` (Req 15.5)."""
    # TODO(4.13): PushSubscriptionStore.upsert(body) (format rules in 4.9).
    not_implemented("4.13")


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
def delete_push_subscription(subscription_id: int) -> Response:
    """204; 404 ``PUSH_SUBSCRIPTION_NOT_FOUND`` (Req 15.6)."""
    # TODO(4.13): PushSubscriptionStore.delete(subscription_id).
    not_implemented("4.13")


@router.post(
    "/test",
    response_model=NotificationOut,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def send_push_test() -> NotificationOut:
    """"알림이 켜졌어요" test push; 409 ``WEB_PUSH_DISABLED`` with NOTIFIER=log (Req 15.9)."""
    # TODO(4.13): store a push_test notification and send it to every subscription.
    not_implemented("4.13")
