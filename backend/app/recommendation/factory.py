"""Recommendation strategy factory (design.md §9, Req 7.14).

The API selects the implementation through ``get_strategy`` dependency
injection, which calls this factory.
"""

from __future__ import annotations

from decimal import Decimal

from app.recommendation.base import RecommendationStrategy
from app.recommendation.rule_based_v1 import RuleBasedTemperatureStrategy

_STRATEGIES = {"rule_based_v1": RuleBasedTemperatureStrategy}


def build_strategy(name: str = "rule_based_v1", *, tolerance_c: Decimal) -> RecommendationStrategy:
    """Return the strategy registered under ``name``. Unknown names raise ValueError."""
    try:
        cls = _STRATEGIES[name]
    except KeyError:
        raise ValueError(f"unknown recommendation strategy: {name!r}") from None
    return cls(tolerance_c=tolerance_c)
