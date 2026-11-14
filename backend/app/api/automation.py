"""Automation status route (design.md §19, lead, Req 17.1, 17.2, 1.5).

Skeleton from tasks 1.7; the body is filled by tasks 7.2 after 7.1 wires the
gallery and brief runtimes into the container. The response never carries a
server path, a key, a push endpoint or raw coordinates (Req 17.2).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import not_implemented
from app.schemas_automation import AutomationStatusOut

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/status", response_model=AutomationStatusOut)
def automation_status() -> AutomationStatusOut:
    """Watcher, daily brief, Notifier and location state (Req 17.1)."""
    # TODO(7.2): collect GalleryStore / brief runs / Notifier / LocationService state.
    not_implemented("7.2")
