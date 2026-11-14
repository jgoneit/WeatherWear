"""Korean explanation text for recommendation results (design.md §10).

Template (every temperature with one decimal place and °C):

    오늘 9.0°C, 지난 8.0°C 기록과 기온 차이 1.0°C입니다. 당시 착장: 코트+니트+슬랙스.
    체감: 적당함 (과거에 적당했음, +15.0점).

- no displayable tags  -> ``당시 착장: 착장 태그 정보 없음.`` (Req 8.6)
- no Effective_Vote     -> ``체감 피드백 기록 없음.`` (Req 8.4)
- cold / hot            -> ``체감: 추웠음 (과거에 추웠음, −15.0점).`` (Req 7.4)
- fallback selection    -> ``기온 허용 범위를 5.0°C로 확장하여 선택되었습니다.`` appended (Req 8.3)

Pure functions only: imports nothing but ``app.domain`` (Req 7.13).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.domain import ClothingTags, ThermalVote

VOTE_KO: dict[ThermalVote, str] = {"cold": "추웠음", "comfortable": "적당함", "hot": "더웠음"}

VOTE_REASON: dict[ThermalVote, str] = {
    "comfortable": "과거에 적당했음",
    "cold": "과거에 추웠음",
    "hot": "과거에 더웠음",
}

NO_TAGS_TEXT = "착장 태그 정보 없음"
NO_VOTE_TEXT = "체감 피드백 기록 없음"
FALLBACK_TEXT = "기온 허용 범위를 5.0°C로 확장하여 선택되었습니다."

MAX_EXPLANATION_LENGTH = 300  # Req 8.1
TRUNCATED_TAG_LENGTH = 20

_HIDDEN_TAG_VALUES = {"none", "unknown"}
_ONE_DECIMAL = Decimal("0.1")


def display_tags(tags: ClothingTags, limit: int = 3) -> list[str]:
    """Clothing types in outer -> upper -> lower order, without ``none``/``unknown``.

    Colors are never used to fill the list; only garment types are shown.
    """
    values = (tags.outer_type, tags.upper_type, tags.lower_type)
    shown = [v for v in values if v.strip().lower() not in _HIDDEN_TAG_VALUES]
    return shown[:limit]


def format_celsius(value: Decimal | float | int) -> str:
    """One decimal place, halves away from zero, ``°C`` suffix. Never prints ``-0.0``."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    q = d.quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)
    if q == 0:
        q = Decimal("0.0")
    return f"{q}°C"


def format_points(points: Decimal) -> str:
    """``+15.0점`` / ``−15.0점`` (U+2212 minus sign, as in design.md)."""
    q = abs(points).quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)
    sign = "−" if points < 0 else "+"
    return f"{sign}{q}점"


def _vote_reason(vote: ThermalVote, adjustment: Decimal | None) -> str:
    if adjustment is None:
        return VOTE_REASON[vote]
    return f"{VOTE_REASON[vote]}, {format_points(adjustment)}"


def _compose(
    today_c: Decimal,
    past_c: Decimal,
    diff: Decimal,
    tags: list[str],
    vote: ThermalVote | None,
    fallback: bool,
    adjustment: Decimal | None,
) -> str:
    parts = [
        f"오늘 {format_celsius(today_c)}, 지난 {format_celsius(past_c)} 기록과 "
        f"기온 차이 {format_celsius(diff)}입니다.",
        f"당시 착장: {'+'.join(tags) if tags else NO_TAGS_TEXT}.",
    ]
    if vote is None:
        parts.append(f"{NO_VOTE_TEXT}.")
    else:
        parts.append(f"체감: {VOTE_KO[vote]} ({_vote_reason(vote, adjustment)}).")
    if fallback:
        parts.append(FALLBACK_TEXT)
    return " ".join(parts)


def build_explanation(
    today_c: Decimal,
    past_c: Decimal,
    diff: Decimal,
    tags: list[str],
    vote: ThermalVote | None,
    fallback: bool,
    *,
    adjustment: Decimal | None = None,
) -> tuple[str, list[str]]:
    """Return ``(explanation, reasons)`` for one recommended candidate.

    ``tags`` is the output of :func:`display_tags`. ``adjustment`` is the
    Effective_Vote score adjustment (e.g. +15 / −15); when given, it is shown
    next to the vote reason in both the explanation and ``reasons``.
    If the explanation exceeds 300 characters, every tag is cut to 20
    characters and the text is rebuilt (Req 8.1).
    """
    explanation = _compose(today_c, past_c, diff, tags, vote, fallback, adjustment)
    if len(explanation) > MAX_EXPLANATION_LENGTH:
        short_tags = [t[:TRUNCATED_TAG_LENGTH] for t in tags]
        explanation = _compose(today_c, past_c, diff, short_tags, vote, fallback, adjustment)
    # Last-resort guard for out-of-domain inputs (e.g. absurd temperatures).
    explanation = explanation[:MAX_EXPLANATION_LENGTH]

    reasons: list[str] = []
    if vote is None:
        reasons.append(NO_VOTE_TEXT)
    elif adjustment is None:
        reasons.append(VOTE_REASON[vote])
    else:
        reasons.append(f"{VOTE_REASON[vote]} ({format_points(adjustment)})")
    if fallback:
        reasons.append(FALLBACK_TEXT)
    return explanation, reasons
