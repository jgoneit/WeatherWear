"""Manual daily brief route (design.md §19, flow B, Req 13).

Uses ``deps.get_daily_brief_service``. It runs regardless of
DAILY_BRIEF_ENABLED (Req 13.3) and answers 409 ``DAILY_BRIEF_IN_PROGRESS``
while another brief is being made (Req 13.2). A manual run never blocks the
day's scheduled brief (Req 13.1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from app.api.notifications import deferred, to_notification_out
from app.deps import ApiError, get_daily_brief_service, get_notification_log
from app.notifications.daily_brief import DailyBriefInProgress
from app.schemas import ErrorResponse
from app.schemas_automation import DailyBriefRunOut

router = APIRouter(prefix="/api/daily-brief", tags=["daily-brief"])

IN_PROGRESS_MESSAGE = "브리핑을 만드는 중입니다. 잠시 뒤 다시 시도해 주세요."


@router.post(
    "/run",
    response_model=DailyBriefRunOut,
    responses={409: {"model": ErrorResponse}},
)
def run_daily_brief(
    service: Callable[[], Any] = Depends(deferred(get_daily_brief_service)),
    log: Callable[[], Any] = Depends(deferred(get_notification_log)),
) -> DailyBriefRunOut:
    """Make one manual Daily_Brief now (Req 13.1)."""
    brief, notifications = service(), log()
    try:
        result = brief.run("manual")
    except DailyBriefInProgress:
        raise ApiError(409, "DAILY_BRIEF_IN_PROGRESS", IN_PROGRESS_MESSAGE) from None
    item = notifications.get(result.notification.id)
    return DailyBriefRunOut(notification=to_notification_out(item), trigger="manual")
