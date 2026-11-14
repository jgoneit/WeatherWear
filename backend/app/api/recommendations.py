"""``GET /api/recommendations/today`` (design.md §9, §11, Req 6.8, 8.1, 8.7).

Recomputed on every request (no cache). No candidates is 200 with
``status="insufficient_data"``; a missing today temperature is 503
``TODAY_TEMPERATURE_MISSING`` via the ``MissingTodayTemperature`` handler.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_recommendation_service
from app.recommendation.service import RecommendationService
from app.schemas import ErrorResponse, RecommendationResponse

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get(
    "/today",
    response_model=RecommendationResponse,
    responses={503: {"model": ErrorResponse}},
)
def recommendations_today(
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    return service.today_recommendations()
