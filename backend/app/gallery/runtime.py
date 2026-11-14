"""Builds the gallery runtime for the container and the CLI (design.md §6, §7, §20).

``build_gallery_runtime(config, container)`` returns the store, the importer,
the watcher (``None`` when GALLERY_WATCH_ENABLED is off) and the uncertain
confirmation service. Building touches neither Gallery_Dir nor
User_Photo_Dir, so a disabled watcher never reads the gallery (Req 1.3).
``deps.py`` is not changed here; tasks 7.1 wires these fields into
``AppContainer``.

The gallery import gets its own ``ImageIngestionService``:
``photo_dir=user_photo_dir``, the API server's Vision provider (so fixture
mode sees the Vision_Fixture_Overlay) and ``RecentWeatherProvider`` (Phase 1
fixture provider in fixture mode, Req 7.4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.config import AppConfig
from app.gallery.confirm import OutfitConfirmService
from app.gallery.processor import GalleryImportService
from app.gallery.store import GalleryStore
from app.gallery.watcher import GalleryWatcher
from app.gallery.weather_recent import build_recent_weather_provider
from app.ingestion.service import ImageIngestionService
from app.location import LatLon, LocationService

if TYPE_CHECKING:  # deps imports this module in 7.1; avoid an import cycle
    import httpx

    from app.deps import AppContainer


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class GalleryRuntime:
    store: GalleryStore
    importer: GalleryImportService
    watcher: GalleryWatcher | None  # None when watching is off
    outfit_confirm: OutfitConfirmService


def build_gallery_runtime(
    config: AppConfig,
    container: AppContainer,
    *,
    clock: Callable[[], datetime] = _utc_now,
    http_client: httpx.Client | None = None,
) -> GalleryRuntime:
    """Gallery services from an ``AppContainer`` (``engine``, ``repository``,
    ``vision_provider``, ``fixture_weather``, ``location``)."""
    automation = config.automation
    tz = config.app_timezone
    store = GalleryStore(container.engine)
    location = container.location or LocationService(
        container.engine, LatLon(config.default_latitude, config.default_longitude)
    )
    weather = build_recent_weather_provider(config, container.fixture_weather, client=http_client)
    ingestion = ImageIngestionService(
        automation.user_photo_dir, container.vision_provider, weather, container.repository, tz
    )
    importer = GalleryImportService(
        automation.gallery_dir,
        automation.user_photo_dir,
        store,
        container.repository,
        ingestion,
        location,
        tz,
    )
    watcher = (
        GalleryWatcher(
            automation.gallery_dir, store, importer,
            poll_seconds=automation.gallery_poll_seconds, clock=clock,
        )
        if automation.gallery_watch_enabled
        else None
    )
    confirm = OutfitConfirmService(importer, store, container.repository, ingestion, clock=clock)
    return GalleryRuntime(store=store, importer=importer, watcher=watcher, outfit_confirm=confirm)
