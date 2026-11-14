"""Recommendation strategy interface (design.md §9, Req 7.13, 7.14).

A strategy computes its result from its arguments only. This module and the
strategy implementations must not import providers, the repository or the
database layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain import OutfitWithWeatherAndFeedback, RecommendationResult, WeatherContext


class MissingTodayTemperature(Exception):
    """Today's WeatherContext has no ``actual_temperature_c`` (Req 6.8).

    The API turns this into 503 ``TODAY_TEMPERATURE_MISSING``.
    """


@dataclass(frozen=True)
class RecommendationOutcome:
    """Strategy result plus the selection metadata the API response needs.

    - ``results``: ranked results, at most ``MAX_RESULTS`` (Req 8.1).
    - ``applied_tolerance_c``: the default tolerance, or 5.0 after the
      fallback expansion (Req 6.3).
    - ``fallback``: True when the default tolerance found no candidate,
      including the "insufficient data" case with no results (Req 6.3, 6.5).
    """

    results: list[RecommendationResult]
    applied_tolerance_c: Decimal
    fallback: bool


class RecommendationStrategy(Protocol):
    algorithm_version: str
    tolerance_c: Decimal

    def recommend(
        self, today: WeatherContext, candidates: list[OutfitWithWeatherAndFeedback]
    ) -> list[RecommendationResult]:
        """Ranked results. Raises ``MissingTodayTemperature``."""
        ...

    def recommend_with_outcome(
        self, today: WeatherContext, candidates: list[OutfitWithWeatherAndFeedback]
    ) -> RecommendationOutcome:
        """Same results as ``recommend`` plus applied tolerance and fallback flag."""
        ...
