"""``GET /api/meta`` (design.md §11, Req 11.4): timezone, algorithm and provider names."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import AppConfig
from app.deps import get_config, get_strategy
from app.recommendation.base import RecommendationStrategy
from app.schemas import AppMeta

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta", response_model=AppMeta)
def app_meta(
    config: AppConfig = Depends(get_config),
    strategy: RecommendationStrategy = Depends(get_strategy),
) -> AppMeta:
    return AppMeta(
        app_timezone=config.app_timezone.key,
        algorithm_version=strategy.algorithm_version,  # type: ignore[arg-type]
        vision_provider=config.vision_provider,
        weather_provider=config.weather_provider,
    )
