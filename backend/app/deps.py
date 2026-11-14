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
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, Request
from sqlalchemy.engine import Engine

from app.config import AppConfig
from app.db import create_db_engine
from app.feedback import FeedbackService
from app.ingestion.service import ImageIngestionService
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
