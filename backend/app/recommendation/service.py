"""RecommendationService: today's weather + stored outfits + strategy -> API response
(design.md §9, Req 6.3, 6.5, 6.8, 8.1, 8.7, 9.6).

- Recommendations are recomputed on every call and never cached, so feedback
  saved a moment ago changes the next result (Req 9.6).
- A missing today temperature raises ``MissingTodayTemperature`` from the
  strategy; the API turns it into 503 ``TODAY_TEMPERATURE_MISSING`` (Req 6.8).
- No result is not an error: the response is 200 with
  ``status="insufficient_data"``, ``fallback=True`` and ``items=[]`` (Req 6.5, 8.7).

The service reads all Outfits once with ``OutfitRepository.list_outfits()``
and builds the strategy input from those records (the same mapping as
``list_candidates()``). One read keeps the candidates and the ``OutfitOut``
payload of each item consistent with each other.
"""

from __future__ import annotations

from app.domain import OutfitWithWeatherAndFeedback, WeatherContext, WeatherReading
from app.recommendation.base import MissingTodayTemperature, RecommendationStrategy
from app.repository import OutfitRecord, OutfitRepository
from app.schemas import (
    OutfitOut,
    RecommendationItemOut,
    RecommendationResponse,
    WeatherContextOut,
    WeatherSnapshotOut,
)
from app.today_weather import TodayWeatherService


def photo_url(outfit_id: int) -> str:
    """API path of an Outfit's photo (design.md §11). Never a filesystem path."""
    return f"/api/outfits/{outfit_id}/photo"


def weather_snapshot_out(reading: WeatherReading | None) -> WeatherSnapshotOut | None:
    if reading is None:
        return None
    return WeatherSnapshotOut(**reading.model_dump())


def outfit_out(record: OutfitRecord) -> OutfitOut:
    """API view of one stored Outfit; ``weather``/``effective_vote`` stay ``null`` when absent."""
    return OutfitOut(
        id=record.id,
        captured_at=record.captured_at,
        latitude=record.latitude,
        longitude=record.longitude,
        source=record.source,
        photo_url=photo_url(record.id),
        tags=record.tags,
        vision_provider=record.vision_provider,
        weather=weather_snapshot_out(record.weather),
        effective_vote=record.effective_vote,
    )


def weather_context_out(today: WeatherContext) -> WeatherContextOut:
    """API view of today's weather. Raises ``MissingTodayTemperature`` (Req 6.8)."""
    if today.actual_temperature_c is None:
        raise MissingTodayTemperature("today's actual_temperature_c is missing")
    return WeatherContextOut(**today.model_dump())


def _candidate(record: OutfitRecord) -> OutfitWithWeatherAndFeedback:
    return OutfitWithWeatherAndFeedback(
        outfit_id=record.id,
        captured_at=record.captured_at,
        tags=record.tags,
        weather=record.weather,
        effective_vote=record.effective_vote,
    )


class RecommendationService:
    def __init__(
        self,
        today_weather: TodayWeatherService,
        repo: OutfitRepository,
        strategy: RecommendationStrategy,
    ) -> None:
        self._today_weather = today_weather
        self._repo = repo
        self._strategy = strategy

    def today_recommendations(self) -> RecommendationResponse:
        """Compute today's recommendations from current data (no cache).

        Raises ``MissingTodayTemperature`` when today's temperature is unknown.
        """
        today = self._today_weather.get_today()
        records = self._repo.list_outfits()
        by_id = {r.id: r for r in records}

        outcome = self._strategy.recommend_with_outcome(today, [_candidate(r) for r in records])
        today_out = weather_context_out(today)

        items = [
            RecommendationItemOut(
                rank=result.rank,
                outfit=outfit_out(by_id[result.outfit_id]),
                score=result.score,
                temperature_diff_c=result.temperature_diff_c,
                selected_by_fallback=result.selected_by_fallback,
                confidence_level=result.confidence_level,
                reasons=list(result.reasons),
                explanation=result.explanation,
            )
            for result in outcome.results
        ]

        return RecommendationResponse(
            status="ok" if items else "insufficient_data",
            algorithm_version=self._strategy.algorithm_version,  # type: ignore[arg-type]
            # Req 8.7: an empty result always reports fallback=True.
            fallback=outcome.fallback or not items,
            applied_tolerance_c=float(outcome.applied_tolerance_c),
            today=today_out,
            items=items,
        )
