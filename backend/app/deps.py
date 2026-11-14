"""Dependency injection for the API (design.md §11, Req 7.14).

``create_app()`` builds one ``AppContainer`` per app and stores it on
``app.state.container``. Routers never build providers or services
themselves; they ask for them with ``Depends(get_...)`` so tests can swap any
of them with ``app.dependency_overrides``.

The recommendation strategy is chosen by ``get_strategy`` (which calls
``build_strategy``). ``get_recommendation_service`` depends on
``get_strategy``, so overriding ``get_strategy`` changes the strategy used by
``GET /api/recommendations/today`` (Req 7.14).

``ApiError`` is the exception routers raise for the common error format
``{"error": {"code", "message"}}``; ``main.py`` registers its handler.

``scan_lock`` is the process-wide lock for ``POST /api/demo/scan``. The scan
router takes it with ``scan_lock.acquire(blocking=False)`` and returns 409
``SCAN_IN_PROGRESS`` when that fails (Req 1.13).

Phase 2 (design.md §20): ``AppContainer`` has one optional field per Phase 2
service, all ``None`` by default. ``build_container`` fills ``location`` and
``place_label`` now; the gallery and notification fields are filled in tasks
7.1 from the runtime builders of flows A (``app/gallery/runtime.py``, 3.13)
and B (``app/notifications/runtime.py``, 4.11). Routers get them through the
``get_*`` functions below; a missing service answers 501 ``NOT_IMPLEMENTED``
until its task wires it (``require``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from fastapi import Depends, Request
from sqlalchemy.engine import Engine

from app.config import AppConfig
from app.db import create_db_engine
from app.feedback import FeedbackService
from app.ingestion.service import ImageIngestionService
from app.location import LatLon, LocationService
from app.providers.vision.base import VisionProvider
from app.providers.vision.factory import build_vision_provider
from app.providers.weather.base import WeatherProvider
from app.providers.weather.factory import build_weather_provider
from app.providers.weather.fixture import FixtureWeatherProvider
from app.recommendation.base import RecommendationStrategy
from app.recommendation.factory import build_strategy
from app.recommendation.service import RecommendationService
from app.repository import OutfitRepository
from app.today_weather import (
    DEFAULT_SCENARIOS_PATH,
    ScenarioStore,
    TodayWeatherService,
    load_scenarios,
)

logger = logging.getLogger(__name__)

# Process-wide: one scan at a time per server process (Req 1.13).
scan_lock = threading.Lock()


class ApiError(Exception):
    """Raise from a router to answer with the common error format."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class AppContainer:
    """Long-lived objects shared by every request of one app instance."""

    config: AppConfig
    engine: Engine
    repository: OutfitRepository
    vision_provider: VisionProvider
    fixture_weather: FixtureWeatherProvider
    weather_provider: WeatherProvider
    scenario_store: ScenarioStore
    today_weather: TodayWeatherService
    feedback_service: FeedbackService
    strategy: RecommendationStrategy
    scenario_errors: list[str]

    # --- Phase 2 (design.md §20). ``None`` until the owning task wires it. -------
    # Lead (1.8): last browser location. Place labels come from flow B's
    # ``app.notifications.places.place_label`` once 7.1 connects it; until then
    # ``build_container`` uses ``fallback_place_label``.
    location: LocationService | None = None
    place_label: Callable[[float, float], str] | None = None
    # Flow A (3.3, 3.11, 3.13, 3.20), wired in 7.1 from ``build_gallery_runtime()``.
    gallery_store: Any = None  # app.gallery.store.GalleryStore
    gallery_importer: Any = None  # app.gallery.processor.GalleryImportService
    gallery_watcher: Any = None  # app.gallery.watcher.GalleryWatcher (None when watching is off)
    outfit_confirm: Any = None  # app.gallery.confirm.OutfitConfirmService
    # Flow B (4.3, 4.6, 4.7, 4.9, 4.11), wired in 7.1 from ``build_brief_runtime()``.
    current_weather: Any = None  # app.notifications.current_weather.CurrentWeatherService
    notification_log: Any = None  # app.notifications.records.NotificationLog
    push_store: Any = None  # app.notifications.push_store.PushSubscriptionStore
    vapid_keys: Any = None  # app.notifications.vapid.VapidKeyStore
    notifier: Any = None  # app.notifications.notifier.Notifier
    daily_brief: Any = None  # app.notifications.daily_brief.DailyBriefService
    brief_scheduler: Any = None  # app.notifications.scheduler.BriefScheduler (None when off)


FALLBACK_PLACE_LABEL = "현재 위치"


def fallback_place_label(latitude: float, longitude: float) -> str:
    """Place label used until flow B's ``place_label`` is connected in 7.1."""
    return FALLBACK_PLACE_LABEL


def build_container(
    config: AppConfig, *, scenarios_path: Path = DEFAULT_SCENARIOS_PATH
) -> AppContainer:
    """Build the engine (``create_all`` runs inside), providers and services."""
    engine = create_db_engine(config.database_url)
    repository = OutfitRepository(engine)
    vision_provider = build_vision_provider(config)
    fixture_weather = FixtureWeatherProvider(tz=config.app_timezone)
    weather_provider = build_weather_provider(config, fixture_weather)
    scenarios, scenario_errors = load_scenarios(scenarios_path)
    scenario_store = ScenarioStore(scenarios)
    today_weather = TodayWeatherService(
        config, weather_provider, scenario_store, fallback=fixture_weather
    )
    location = LocationService(
        engine, LatLon(config.default_latitude, config.default_longitude)
    )
    return AppContainer(
        config=config,
        engine=engine,
        repository=repository,
        vision_provider=vision_provider,
        fixture_weather=fixture_weather,
        weather_provider=weather_provider,
        scenario_store=scenario_store,
        today_weather=today_weather,
        feedback_service=FeedbackService(repository),
        strategy=build_strategy(tolerance_c=config.temperature_tolerance_c),
        scenario_errors=scenario_errors,
        location=location,
        place_label=fallback_place_label,
    )


# --- Dependency functions ---------------------------------------------------


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def get_config(container: AppContainer = Depends(get_container)) -> AppConfig:
    return container.config


def get_repository(container: AppContainer = Depends(get_container)) -> OutfitRepository:
    return container.repository


def get_vision_provider(container: AppContainer = Depends(get_container)) -> VisionProvider:
    return container.vision_provider


def get_weather_provider(container: AppContainer = Depends(get_container)) -> WeatherProvider:
    return container.weather_provider


def get_scenario_store(container: AppContainer = Depends(get_container)) -> ScenarioStore:
    return container.scenario_store


def get_today_weather_service(
    container: AppContainer = Depends(get_container),
) -> TodayWeatherService:
    return container.today_weather


def get_feedback_service(container: AppContainer = Depends(get_container)) -> FeedbackService:
    return container.feedback_service


def get_strategy(container: AppContainer = Depends(get_container)) -> RecommendationStrategy:
    """The recommendation strategy (Req 7.14). Override this in tests."""
    return container.strategy


def get_recommendation_service(
    today_weather: TodayWeatherService = Depends(get_today_weather_service),
    repository: OutfitRepository = Depends(get_repository),
    strategy: RecommendationStrategy = Depends(get_strategy),
) -> RecommendationService:
    return RecommendationService(today_weather, repository, strategy)


def get_ingestion_service(
    config: AppConfig = Depends(get_config),
    vision: VisionProvider = Depends(get_vision_provider),
    weather: WeatherProvider = Depends(get_weather_provider),
    repository: OutfitRepository = Depends(get_repository),
) -> ImageIngestionService:
    return ImageIngestionService(
        config.photo_dir, vision, weather, repository, config.app_timezone
    )


def get_scan_lock() -> threading.Lock:
    return scan_lock


# --- Phase 2 dependency functions ---------------------------------------------

T = TypeVar("T")

NOT_IMPLEMENTED_MESSAGE = "아직 준비되지 않은 기능입니다."


def not_implemented(task: str) -> NoReturn:
    """501 ``NOT_IMPLEMENTED`` for a Phase 2 route whose task has not filled it yet.

    ``task`` is the tasks.md id that fills the route; it is logged, not returned.
    """
    logger.info("Phase 2 route not implemented yet (tasks %s)", task)
    raise ApiError(501, "NOT_IMPLEMENTED", NOT_IMPLEMENTED_MESSAGE)


def require(service: T | None, task: str) -> T:
    """Return ``service`` or answer 501 until ``task`` wires it into the container."""
    if service is None:
        not_implemented(task)
    return service


def get_location_service(container: AppContainer = Depends(get_container)) -> LocationService:
    return require(container.location, "1.8")


def get_place_label(
    container: AppContainer = Depends(get_container),
) -> Callable[[float, float], str]:
    return container.place_label or fallback_place_label


def get_gallery_store(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.gallery_store, "3.3/7.1")


def get_gallery_importer(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.gallery_importer, "3.11/7.1")


def get_outfit_confirm_service(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.outfit_confirm, "3.20/7.1")


def get_current_weather_service(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.current_weather, "4.3/7.1")


def get_notification_log(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.notification_log, "4.6/7.1")


def get_push_store(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.push_store, "4.9/7.1")


def get_vapid_keys(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.vapid_keys, "4.7/7.1")


def get_notifier(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.notifier, "4.6/7.1")


def get_daily_brief_service(container: AppContainer = Depends(get_container)) -> Any:
    return require(container.daily_brief, "4.11/7.1")
