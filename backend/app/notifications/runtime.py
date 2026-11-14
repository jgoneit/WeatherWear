"""Builds the flow B runtime from an ``AppContainer`` (design.md §20, tasks 4.11).

``deps.py`` is not changed by flow B: tasks 7.1 calls ``build_brief_runtime``
from ``build_container`` and copies the fields into the container
(``current_weather``, ``notification_log``, ``push_store``, ``vapid_keys``,
``notifier``, ``daily_brief``, ``brief_scheduler``). The CLI
``scripts/daily_brief.py`` uses the same builder, so it shares the server's
settings and database.

- The Forecast client exists only with ``WEATHER_PROVIDER=open_meteo``.
- The scheduler is ``None`` unless ``DAILY_BRIEF_ENABLED`` (Req 12.2); the
  service exists either way, because ``POST /api/daily-brief/run`` works
  regardless of that setting (Req 13.3).
- VAPID key rotation deletes subscriptions made with another public key (Req 15.3).
- ``live_current`` is what tasks 7.1 passes to ``TodayWeatherService`` in Live
  mode (decision A8, tasks 4.16): the same Current_Weather_Service call at the
  same brief location as the daily brief (Req 10.8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import AppConfig
from app.domain import WeatherContext
from app.notifications.current_weather import CurrentWeatherService
from app.notifications.daily_brief import DailyBriefRuns, DailyBriefService
from app.notifications.notifier import Notifier, build_notifier
from app.notifications.places import place_label
from app.notifications.push_store import PushSubscriptionStore
from app.notifications.records import NotificationLog
from app.notifications.scheduler import DailyBriefScheduler
from app.notifications.vapid import VapidKeyStore
from app.notifications.webpush import PushSender, PyWebPushSender
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.forecast import ForecastClient
from app.recommendation.service import RecommendationService


@dataclass
class BriefRuntime:
    current_weather: CurrentWeatherService
    notification_log: NotificationLog
    push_store: PushSubscriptionStore
    vapid_keys: VapidKeyStore
    notifier: Notifier
    runs: DailyBriefRuns
    daily_brief: DailyBriefService
    brief_scheduler: DailyBriefScheduler | None
    live_current: Callable[[], WeatherContext]
    place_label: Callable[[float, float], str]
    forecast: ForecastClient | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_brief_runtime(
    config: AppConfig,
    container: Any,
    *,
    clock: Callable[[], datetime] | None = None,
    sender: PushSender | None = None,
    forecast: ForecastClient | None = None,
    on_vapid_secrets: Callable[[list[str]], object] | None = None,
) -> BriefRuntime:
    """Flow B services on top of ``container`` (an ``AppContainer``).

    ``container`` must provide ``engine``, ``scenario_store``,
    ``fixture_weather``, ``location``, ``repository``, ``strategy`` and
    ``today_weather``. ``clock``, ``sender`` and ``forecast`` are for tests;
    with a ``clock`` the fixture weather gets the same clock, so "today" is
    the same date for the fixture and the brief. ``on_vapid_secrets`` receives
    the private key strings when the key is loaded or created (RedactingFilter, 7.1).
    """
    now = clock or _utc_now
    tz = config.app_timezone
    engine = container.engine

    fixture = (
        FixtureWeatherProvider(tz=tz, now=clock) if clock is not None else container.fixture_weather
    )
    if forecast is None and config.weather_provider == "open_meteo":
        forecast = ForecastClient()
    current_weather = CurrentWeatherService(config, forecast, container.scenario_store, fixture, now)

    log = NotificationLog(engine, now)
    push_store = PushSubscriptionStore(engine, now)
    vapid_keys = VapidKeyStore(
        config.automation.vapid_key_dir,
        now,
        on_rotated=push_store.delete_not_matching_key,
        on_secrets=on_vapid_secrets,
    )
    notifier = build_notifier(
        config, push_store=push_store, keys=vapid_keys, sender=sender or PyWebPushSender()
    )
    runs = DailyBriefRuns(engine, now)

    def recommendations() -> RecommendationService:
        return RecommendationService(container.today_weather, container.repository, container.strategy)

    location = container.location
    daily_brief = DailyBriefService(
        location,
        current_weather,
        recommendations,
        log,
        notifier,
        runs,
        tz,
        now,
        place_label=place_label,
    )
    scheduler = (
        DailyBriefScheduler(daily_brief, runs, config.automation.daily_brief_time, tz, now)
        if config.automation.daily_brief_enabled
        else None
    )

    def live_current() -> WeatherContext:
        return current_weather.get_current(location.brief_location()[0])

    return BriefRuntime(
        current_weather=current_weather,
        notification_log=log,
        push_store=push_store,
        vapid_keys=vapid_keys,
        notifier=notifier,
        runs=runs,
        daily_brief=daily_brief,
        brief_scheduler=scheduler,
        live_current=live_current,
        place_label=place_label,
        forecast=forecast,
    )
