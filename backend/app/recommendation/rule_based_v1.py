"""Rule-based recommendation strategy ``rule_based_v1`` (design.md §9).

Selection (Req 6.1, 6.3, 6.5, 6.7, 6.9):
  diff = round_half_up(|past - today|, 1). Candidates with ``diff <= tolerance``
  are used; if there are none, the tolerance is expanded once to 5.0 and the
  fallback flag is set. Outfits without weather or temperature never qualify.

Non-outfit photos (Req 6.10, 15.13; behaviour change after Phase 1):
  A photo whose upper/outer/lower types are all ``"unknown"`` with confidence
  0.0 (``is_non_outfit``, e.g. food or landscape shots) is not a candidate. It
  is excluded before the tolerance search, so it never appears in the results
  and never affects the "no candidates" or fallback decisions. The algorithm
  version string stays ``rule_based_v1`` because the requirements fix it.

Scoring and ranking (Req 7.1~7.8):
  score = 100 - 10 * diff + adj(vote), adj = +15 comfortable, -15 cold/hot, 0 none.
  Sort key (-score, tb(apparent), tb(humidity), tb(wind), tb(precip), outfit_id)
  where tb(x) = (0, |past_x - today_x|) when both values exist, else (1, 0).

Only ``app.domain`` and the sibling ``base``/``explain`` modules are imported (Req 7.13).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.domain import (
    ALGORITHM_VERSION,
    ClothingTags,
    ConfidenceLevel,
    OutfitWithWeatherAndFeedback,
    RecommendationResult,
    ThermalVote,
    WeatherContext,
)
from app.recommendation.base import MissingTodayTemperature, RecommendationOutcome
from app.recommendation.explain import build_explanation, display_tags

# Configuration constants (Req 7.9).
FALLBACK_TOLERANCE_C = Decimal("5.0")
TEMP_SCORE_BASE = Decimal("100.0")
TEMP_WEIGHT_PER_C = Decimal("10.0")  # 10 points less per 1°C of difference
COMFORTABLE_BONUS = Decimal("15.0")  # 과거에 적당했음
MISMATCH_PENALTY = Decimal("15.0")  # 과거에 추웠음 / 더웠음
MAX_RESULTS = 5

ONE_DECIMAL = Decimal("0.1")


def to_decimal(value: float | int | str | Decimal) -> Decimal:
    """Decimal from the shortest float repr, so 3.05 stays exactly 3.05."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_half_up(value: Decimal) -> Decimal:
    """Round to one decimal place, halves away from zero (Req 6.7)."""
    return value.quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)


def temperature_diff(past_c: float, today_c: float) -> Decimal:
    """|past - today| rounded to one decimal place (Req 6.7, 7.1)."""
    return round_half_up(abs(to_decimal(past_c) - to_decimal(today_c)))


def vote_adjustment(vote: ThermalVote | None) -> Decimal:
    """+15 comfortable, -15 cold/hot, 0 without a vote (Req 7.3~7.5)."""
    if vote == "comfortable":
        return COMFORTABLE_BONUS
    if vote in ("cold", "hot"):
        return -MISMATCH_PENALTY
    return Decimal("0")


def score_for(diff: Decimal, vote: ThermalVote | None) -> Decimal:
    """100 - 10 * diff + adj(vote) (Req 7.1, 7.2)."""
    return TEMP_SCORE_BASE - TEMP_WEIGHT_PER_C * diff + vote_adjustment(vote)


def confidence_level(has_vote: bool, within_default_tolerance: bool) -> ConfidenceLevel:
    """high: both true; medium: exactly one false; low: both false (Req 8.5, 7.5)."""
    if has_vote and within_default_tolerance:
        return "high"
    if has_vote or within_default_tolerance:
        return "medium"
    return "low"


def is_non_outfit(tags: ClothingTags) -> bool:
    """True when all three clothing types are ``unknown`` and confidence is 0.0 (Req 6.10)."""
    return (
        tags.upper_type == "unknown"
        and tags.outer_type == "unknown"
        and tags.lower_type == "unknown"
        and tags.confidence == 0.0
    )


def _tie_break(past: float | None, today: float | None) -> tuple[int, Decimal]:
    """Smaller difference first; a missing value on either side sorts last (Req 7.7, 7.8)."""
    if past is None or today is None:
        return (1, Decimal("0"))
    return (0, abs(to_decimal(past) - to_decimal(today)))


class RuleBasedTemperatureStrategy:
    """``rule_based_v1``: tolerance search + feedback-weighted ranking."""

    algorithm_version: str = ALGORITHM_VERSION

    def __init__(self, tolerance_c: Decimal | float | str = Decimal("3.0")) -> None:
        self.tolerance_c = to_decimal(tolerance_c)

    def recommend(
        self, today: WeatherContext, candidates: list[OutfitWithWeatherAndFeedback]
    ) -> list[RecommendationResult]:
        return self.recommend_with_outcome(today, candidates).results

    def recommend_with_outcome(
        self, today: WeatherContext, candidates: list[OutfitWithWeatherAndFeedback]
    ) -> RecommendationOutcome:
        if today.actual_temperature_c is None:
            raise MissingTodayTemperature("today's actual_temperature_c is missing")
        today_c = today.actual_temperature_c

        # Req 6.9: no weather, or weather without a temperature, never qualifies.
        # Req 6.10: non-outfit photos never qualify either.
        scored: list[tuple[OutfitWithWeatherAndFeedback, Decimal]] = [
            (c, temperature_diff(c.weather.actual_temperature_c, today_c))
            for c in candidates
            if c.weather is not None
            and c.weather.actual_temperature_c is not None
            and not is_non_outfit(c.tags)
        ]

        fallback = False
        applied = self.tolerance_c
        selected = [(c, d) for c, d in scored if d <= applied]  # boundary included (Req 6.7)
        if not selected:
            # Req 6.3, 6.5: expand exactly once, never further.
            fallback = True
            applied = FALLBACK_TOLERANCE_C
            selected = [(c, d) for c, d in scored if d <= applied]

        ranked = sorted(
            ((c, d, score_for(d, c.effective_vote)) for c, d in selected),
            key=lambda item: self._sort_key(today, *item),
        )

        results: list[RecommendationResult] = []
        seen: set[int] = set()
        for candidate, diff, score in ranked:
            if candidate.outfit_id in seen:  # Req 7.12: never return an Outfit twice
                continue
            seen.add(candidate.outfit_id)
            assert candidate.weather is not None
            vote = candidate.effective_vote
            explanation, reasons = build_explanation(
                round_half_up(to_decimal(today_c)),
                round_half_up(to_decimal(candidate.weather.actual_temperature_c)),
                diff,
                display_tags(candidate.tags),
                vote,
                fallback,
                adjustment=vote_adjustment(vote) if vote is not None else None,
            )
            results.append(
                RecommendationResult(
                    outfit_id=candidate.outfit_id,
                    rank=len(results) + 1,
                    score=float(round_half_up(score)),
                    temperature_diff_c=float(diff),
                    selected_by_fallback=fallback,
                    confidence_level=confidence_level(
                        candidate.effective_vote is not None, not fallback
                    ),
                    reasons=reasons,
                    explanation=explanation,
                    algorithm_version=ALGORITHM_VERSION,
                )
            )
            if len(results) == MAX_RESULTS:
                break

        return RecommendationOutcome(results=results, applied_tolerance_c=applied, fallback=fallback)

    @staticmethod
    def _sort_key(
        today: WeatherContext,
        candidate: OutfitWithWeatherAndFeedback,
        diff: Decimal,
        score: Decimal,
    ) -> tuple:
        w = candidate.weather
        assert w is not None
        return (
            -score,
            _tie_break(w.apparent_temperature_c, today.apparent_temperature_c),
            _tie_break(w.relative_humidity_pct, today.relative_humidity_pct),
            _tie_break(w.wind_speed_kmh, today.wind_speed_kmh),
            _tie_break(w.precipitation_mm, today.precipitation_mm),
            candidate.outfit_id,
        )
