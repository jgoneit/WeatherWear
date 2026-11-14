"""Manual daily brief route (design.md §19, flow B, Req 13).

Skeleton from tasks 1.7; the body is filled by tasks 4.13 with
``deps.get_daily_brief_service``. It runs regardless of DAILY_BRIEF_ENABLED
(Req 13.3) and answers 409 ``DAILY_BRIEF_IN_PROGRESS`` while another brief is
being made (Req 13.2).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import not_implemented
from app.schemas import ErrorResponse
from app.schemas_automation import DailyBriefRunOut

router = APIRouter(prefix="/api/daily-brief", tags=["daily-brief"])


@router.post(
    "/run",
    response_model=DailyBriefRunOut,
    responses={409: {"model": ErrorResponse}},
)
def run_daily_brief() -> DailyBriefRunOut:
    """Make one manual Daily_Brief now (Req 13.1)."""
    # TODO(4.13): DailyBriefService.run("manual").
    not_implemented("4.13")
