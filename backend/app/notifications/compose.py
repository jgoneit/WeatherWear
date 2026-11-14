"""Brief_Composer: title and body of the daily brief (design.md §14, Req 11.2~11.14).

Pure functions. Example (Req 11.2): today 14.5°C / 55 %, top recommendation
taken at 14.4°C in a cream knit with no outerwear, voted ``comfortable``:

    오늘 서울 14.5°C, 습도 55%. 비슷했던 14.4°C 날 크림 니트를 입고 '적당했어'라고 했어요.

Body rules:

- ``p1 = "오늘 {place} {T(today)}" + (", 습도 {h}%" if humidity) + "."``
- top with a vote: ``" {lead} {T(past)} 날 {phrase}{을/를} 입고 '{spoken}'라고 했어요."``
  plus the cold/hot tail sentence;
- top without a vote: ``" {lead} {T(past)} 날 {phrase}{을/를} 입었어요. 그날 어땠는지 알려 주세요."``;
- no top: the insufficient-data sentence (Req 11.9).

``lead`` is "기온이 가장 가까웠던" when the item was selected by the fallback
tolerance, otherwise "비슷했던" (Req 11.5). Temperatures use Phase 1's
``format_celsius`` (one decimal, ROUND_HALF_UP); humidity is a whole number
rounded half up, the same as the frontend's ``formatHumidity`` (Req 11.2).
The title gets " (fixture 날씨)" or " (시나리오 날씨)" when today's weather is
not live (Req 11.12). A body over 200 characters is rebuilt with every label
of the outfit phrase cut to 12 characters (closed-list labels are shorter, so
only free-text colors are ever cut), then hard-cut as a last resort.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain import ClothingTags, ThermalVote, WeatherContext
from app.notifications.josa import object_particle
from app.notifications.labels_ko import color_label, lower_label, outer_label, upper_label
from app.recommendation.explain import format_celsius
from app.schemas import RecommendationItemOut

VOTE_SPOKEN: dict[ThermalVote, str] = {"cold": "추웠어", "comfortable": "적당했어", "hot": "더웠어"}
VOTE_TAIL: dict[ThermalVote, str] = {
    "cold": " 오늘은 한 겹 더 챙겨 보세요.",
    "hot": " 오늘은 조금 가볍게 입어 보세요.",
}
TITLE = "오늘의 착장 브리핑"
TITLE_SUFFIX = {"fixture": " (fixture 날씨)", "scenario": " (시나리오 날씨)"}

LEAD_SIMILAR = "비슷했던"
LEAD_FALLBACK = "기온이 가장 가까웠던"
NO_VOTE_TEXT = "입었어요. 그날 어땠는지 알려 주세요."
INSUFFICIENT_DATA_TEXT = (
    " 비슷한 기온에서 입었던 착장 기록이 아직 없어요. 오늘 입은 옷을 찍어 두면 다음에 알려 드릴게요."
)
NO_TAGS_PHRASE = "그날 착장"
HOME_URL = "/"

MAX_BODY_LENGTH = 200
TRUNCATED_LABEL_LENGTH = 12


@dataclass(frozen=True)
class BriefInput:
    place: str
    today: WeatherContext  # actual_temperature_c is required
    top: RecommendationItemOut | None  # rank 1, or None when there is no recommendation


@dataclass(frozen=True)
class BriefMessage:
    title: str
    body: str
    url: str
    outfit_id: int | None


def format_temp(celsius: float) -> str:
    """One decimal place + "°C", halves away from zero (Phase 1 explain)."""
    return format_celsius(celsius)


def format_humidity(percent: float) -> str:
    """Whole percent, halves up (non-negative humidity: same as JS ``Math.round``)."""
    rounded = Decimal(str(percent)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = Decimal("0")
    return f"{rounded}%"


def _cut(text: str, limit: int | None) -> str:
    return text if limit is None else text[:limit]


def outfit_phrase(tags: ClothingTags, *, max_label: int | None = None) -> str:
    """Outfit words of the top recommendation (Req 11.6).

    | upper | outer | result |
    | known | none/unknown | "{color} {upper}" |
    | known | known | "{color} {upper}에 {outer}" |
    | unknown | known | "{color} {outer}" |
    | unknown | none/unknown | "{color} {lower}" if lower is known, else "그날 착장" |

    ``color`` is the label of ``colors[0]``; without colors the "{color} " part
    is left out. ``max_label`` cuts every label to that many characters.
    """
    upper = upper_label(tags.upper_type)
    outer = outer_label(tags.outer_type)
    lower = lower_label(tags.lower_type)
    if upper is not None:
        core = _cut(upper, max_label)
        if outer is not None:
            core = f"{core}에 {_cut(outer, max_label)}"
    elif outer is not None:
        core = _cut(outer, max_label)
    elif lower is not None:
        core = _cut(lower, max_label)
    else:
        return NO_TAGS_PHRASE

    color = _cut(color_label(tags.colors[0]), max_label) if tags.colors else ""
    return f"{color} {core}" if color else core


def _title(today: WeatherContext) -> str:
    return TITLE + TITLE_SUFFIX.get(today.provider, "")


def _first_sentence(place: str, today: WeatherContext) -> str:
    if today.actual_temperature_c is None:
        raise ValueError("today's actual_temperature_c is required")
    text = f"오늘 {place} {format_temp(today.actual_temperature_c)}"
    if today.relative_humidity_pct is not None:
        text += f", 습도 {format_humidity(today.relative_humidity_pct)}"
    return text + "."


def _usable_top(top: RecommendationItemOut | None) -> RecommendationItemOut | None:
    # rule_based_v1 only ranks outfits with a weather snapshot; a top item
    # without one cannot say "the day it was X°C", so it counts as no data.
    if top is None or top.outfit.weather is None:
        return None
    return top


def _second_sentence(top: RecommendationItemOut | None, max_label: int | None) -> str:
    if top is None:
        return INSUFFICIENT_DATA_TEXT
    assert top.outfit.weather is not None
    lead = LEAD_FALLBACK if top.selected_by_fallback else LEAD_SIMILAR
    past = format_temp(top.outfit.weather.actual_temperature_c)
    phrase = outfit_phrase(top.outfit.tags, max_label=max_label)
    particle = object_particle(phrase)
    vote = top.outfit.effective_vote
    if vote is None:
        return f" {lead} {past} 날 {phrase}{particle} {NO_VOTE_TEXT}"
    return f" {lead} {past} 날 {phrase}{particle} 입고 '{VOTE_SPOKEN[vote]}'라고 했어요." + VOTE_TAIL.get(
        vote, ""
    )


def compose_brief(inp: BriefInput) -> BriefMessage:
    """Title, body (≤ 200 characters) and link of one daily brief (Req 11.2~11.14).

    The link is ``/outfits/{id}`` of the top Outfit, or ``/`` without one (Req 11.13).
    Raises ``ValueError`` when today's temperature is missing.
    """
    top = _usable_top(inp.top)
    first = _first_sentence(inp.place, inp.today)
    body = first + _second_sentence(top, None)
    if len(body) > MAX_BODY_LENGTH:
        body = first + _second_sentence(top, TRUNCATED_LABEL_LENGTH)
    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH]
    outfit_id = top.outfit.id if top is not None else None
    return BriefMessage(
        title=_title(inp.today),
        body=body,
        url=f"/outfits/{outfit_id}" if outfit_id is not None else HOME_URL,
        outfit_id=outfit_id,
    )
