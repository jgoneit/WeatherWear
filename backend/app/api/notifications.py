"""Notification_Log route (design.md §19, flow B, Req 14.5).

Uses the NotificationLog from ``deps.get_notification_log``.
``to_notification_out`` and ``deferred`` are shared with the daily brief and
push routes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, Query, Request

from app.deps import get_notification_log
from app.notifications.records import NotificationWithSummary
from app.recommendation.service import photo_url
from app.schemas_automation import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    DeliverySummaryOut,
    NotificationKind,
    NotificationOut,
)

router = APIRouter(prefix="/api", tags=["notifications"])

T = TypeVar("T")


def deferred(getter: Callable[..., T]) -> Callable[[Request], Callable[[], T]]:
    """Dependency that hands the route a resolver for ``getter`` (a ``deps.get_*``).

    ``deps.get_*`` answers 501 ``NOT_IMPLEMENTED`` while its service is not
    wired (until tasks 7.1). FastAPI runs dependencies before it validates the
    query and body, so ``Depends(getter)`` would turn a 422 into a 501. The
    resolver is called in the route body, after validation, and honours
    ``app.dependency_overrides[getter]`` (zero-argument overrides, as in tests).
    """

    def dependency(request: Request) -> Callable[[], T]:
        def resolve() -> T:
            override = request.app.dependency_overrides.get(getter)
            if override is not None:
                return override()
            return getter(request.app.state.container)

        return resolve

    return dependency


def to_notification_out(item: NotificationWithSummary) -> NotificationOut:
    """API view of one notification. ``photo_url`` points at the linked Outfit's photo."""
    n, d = item.notification, item.delivery
    return NotificationOut(
        id=n.id,
        kind=n.kind,
        title=n.title,
        body=n.body,
        url=n.url,
        outfit_id=n.outfit_id,
        photo_url=photo_url(n.outfit_id) if n.outfit_id is not None else None,
        weather_provider=n.weather_provider,  # type: ignore[arg-type]
        weather_is_fixture=n.weather_is_fixture,
        created_at=n.created_at,
        delivery=DeliverySummaryOut(
            log_recorded=d.log_recorded,
            webpush_sent=d.webpush_sent,
            webpush_failed=d.webpush_failed,
            webpush_gone=d.webpush_gone,
        ),
    )


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    kind: NotificationKind | None = Query(None),
    log: Callable[[], Any] = Depends(deferred(get_notification_log)),
) -> list[NotificationOut]:
    """Newest first (``created_at`` desc, then ``id`` desc), with delivery summaries (Req 14.5)."""
    return [to_notification_out(item) for item in log().list_recent(limit, kind)]
