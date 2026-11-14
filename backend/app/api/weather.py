"""``GET /api/weather/today`` (design.md §11, Req 5.1, 5.5, 6.8).

Selected scenario > Weather_Provider > fixture fallback, as decided by
``TodayWeatherService``. A missing actual temperature raises
``MissingTodayTemperature``, which ``main.py`` maps to 503
``TODAY_TEMPERATURE_MISSING``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_today_weather_service
from app.recommendation.service import weather_context_out
from app.schemas import ErrorResponse, WeatherContextOut
from app.today_weather import TodayWeatherService

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get(
    "/today",
    response_model=WeatherContextOut,
    responses={503: {"model": ErrorResponse}},
)
def weather_today(
    service: TodayWeatherService = Depends(get_today_weather_service),
) -> WeatherContextOut:
    return weather_context_out(service.get_today())
