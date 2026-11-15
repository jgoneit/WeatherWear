"""Automation status route (design.md §19, lead, tasks 7.2, Req 17.1, 17.2, 1.5).

``GET /api/automation/status`` collects four parts:

- **gallery**: with watching on, ``GalleryWatcher.status()`` (running flag, last
  poll, Gallery_Dir availability without its path, pending and uncertain counts).
  With watching off the folder is never read (Req 1.3): ``dir_available`` is
  ``False`` and the counts come from the database only.
- **daily_brief**: the setting, ``HH:MM``, the timezone name, the scheduler's
  next run (``None`` when the brief is off) and the latest scheduled run.
- **notifier**: NOTIFIER, whether the VAPID key pair is loaded, the number of
  push subscriptions.
- **location**: whether a browser location exists, when it arrived and its
  Place_Label. Coordinates are not returned.

No server path, key, push endpoint or raw coordinate is in the response (Req 17.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import AppContainer, get_container, require
from app.gallery.store import gallery_key
from app.schemas_automation import (
    AutomationStatusOut,
    DailyBriefStatusOut,
    GalleryStatusOut,
    LastScheduledBriefOut,
    LocationStatusOut,
    NotifierStatusOut,
)

router = APIRouter(prefix="/api/automation", tags=["automation"])


def gallery_status(container: AppContainer) -> GalleryStatusOut:
    auto = container.config.automation
    watcher = container.gallery_watcher
    if watcher is not None:
        status = watcher.status()
        return GalleryStatusOut(
            enabled=status.enabled,
            running=status.running,
            dir_available=status.dir_available,
            last_poll_at=status.last_poll_at,
            pending=status.pending,
            uncertain=status.uncertain,
        )
    store = require(container.gallery_store, "7.1")
    counts = store.counts(gallery_key(auto.gallery_dir))  # database only, no folder access
    return GalleryStatusOut(
        enabled=auto.gallery_watch_enabled,
        running=False,
        dir_available=False,
        last_poll_at=None,
        pending=counts["pending"] + counts["processing"] + counts["retry_wait"],
        uncertain=counts["uncertain"],
    )


def daily_brief_status(container: AppContainer) -> DailyBriefStatusOut:
    config = container.config
    auto = config.automation
    runs = require(container.brief_runs, "7.1")
    latest = runs.latest_scheduled()
    scheduler = container.brief_scheduler
    return DailyBriefStatusOut(
        enabled=auto.daily_brief_enabled,
        time=auto.daily_brief_time.strftime("%H:%M"),
        timezone=config.app_timezone.key,
        next_run_at=scheduler.next_run_at() if scheduler is not None else None,
        last_scheduled=(
            None
            if latest is None
            else LastScheduledBriefOut(date=latest.brief_date.isoformat(), status=runs.effective_status(latest))
        ),
    )


def notifier_status(container: AppContainer) -> NotifierStatusOut:
    vapid_keys = require(container.vapid_keys, "7.1")
    push_store = require(container.push_store, "7.1")
    return NotifierStatusOut(
        name=container.config.automation.notifier,
        vapid_ready=vapid_keys.ready,
        subscription_count=push_store.count(),
    )


def location_status(container: AppContainer) -> LocationStatusOut:
    location = require(container.location, "1.8")
    last = location.last()
    if last is None:
        return LocationStatusOut(has_last=False, received_at=None, place_label=None)
    label = (container.place_label or (lambda _lat, _lon: None))(last.latitude, last.longitude)
    return LocationStatusOut(has_last=True, received_at=last.received_at, place_label=label)


@router.get("/status", response_model=AutomationStatusOut)
def automation_status(container: AppContainer = Depends(get_container)) -> AutomationStatusOut:
    """Watcher, daily brief, Notifier and location state (Req 17.1)."""
    return AutomationStatusOut(
        gallery=gallery_status(container),
        daily_brief=daily_brief_status(container),
        notifier=notifier_status(container),
        location=location_status(container),
    )
