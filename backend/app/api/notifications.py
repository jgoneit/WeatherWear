"""Notification_Log route (design.md §19, flow B).

Skeleton from tasks 1.7; the body is filled by tasks 4.13 with the
NotificationLog from ``deps.get_notification_log`` (overridable in tests).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import not_implemented
from app.schemas_automation import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    NotificationKind,
    NotificationOut,
)

router = APIRouter(prefix="/api", tags=["notifications"])


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    kind: NotificationKind | None = Query(None),
) -> list[NotificationOut]:
    """Newest first (``created_at`` desc, then ``id`` desc), with delivery summaries (Req 14.5)."""
    # TODO(4.13): NotificationLog.list(limit, kind) -> NotificationOut.
    not_implemented("4.13")
