"""Korean labels for clothing tags (design.md §14, Req 11.7).

- Garment types are closed lists (``app.domain.UPPER_TYPES`` and friends), so
  every value except ``none`` and ``unknown`` has a label here. A coverage test
  walks the closed lists.
- Colors are free strings. Every color that appears in the demo fixture
  (``data/demo/fixtures/vision.json``) has a label, plus a few common ones from
  the design table. A color without a label is shown as written (Req 11.7).

Pure data and lookups; no I/O.
"""

from __future__ import annotations

UPPER_LABELS: dict[str, str] = {
    "t_shirt": "티셔츠",
    "long_sleeve_t_shirt": "긴소매 티셔츠",
    "shirt": "셔츠",
    "blouse": "블라우스",
    "knit": "니트",
    "turtleneck": "터틀넥",
    "sweatshirt": "맨투맨",
    "hoodie": "후드티",
    "sleeveless_top": "민소매 톱",
    "cardigan": "카디건",
    "fleece": "플리스",
    "jersey": "저지",
    "dress": "원피스",
    "jumpsuit": "점프슈트",
    "coveralls": "커버올",
}

OUTER_LABELS: dict[str, str] = {
    "long_puffer": "롱패딩",
    "short_puffer": "숏패딩",
    "puffer_jacket": "패딩 재킷",
    "parka": "파카",
    "coat": "코트",
    "trench_coat": "트렌치코트",
    "jacket": "재킷",
    "blazer": "블레이저",
    "cardigan": "카디건",
    "denim_jacket": "청재킷",
    "leather_jacket": "가죽 재킷",
    "bomber_jacket": "항공 점퍼",
    "fleece_jacket": "플리스 재킷",
    "rain_jacket": "레인 재킷",
    "windbreaker": "바람막이",
    "track_jacket": "트랙 재킷",
    "utility_jacket": "워크 재킷",
    "shirt_jacket": "셔츠 재킷",
    "varsity_jacket": "야구 점퍼",
    "vest": "조끼",
    "jersey": "저지",
}

LOWER_LABELS: dict[str, str] = {
    "jeans": "청바지",
    "trousers": "바지",
    "slacks": "슬랙스",
    "chinos": "치노 팬츠",
    "cargo_pants": "카고 팬츠",
    "corduroy_pants": "코듀로이 팬츠",
    "leggings": "레깅스",
    "shorts": "반바지",
    "skirt": "치마",
    "track_pants": "트랙 팬츠",
    "jogger_pants": "조거 팬츠",
    "parachute_pants": "파라슈트 팬츠",
}

# Keys are lower-case color names as written by the vision fixture / Gemini.
COLOR_LABELS: dict[str, str] = {
    "beige": "베이지",
    "black": "검정",
    "blue": "파란색",
    "brown": "갈색",
    "burgundy": "버건디",
    "camel": "카멜",
    "champagne": "샴페인",
    "charcoal": "차콜",
    "chartreuse": "연두",
    "cobalt blue": "코발트 블루",
    "coral": "코랄",
    "cream": "크림",
    "denim": "데님",
    "dusty pink": "더스티 핑크",
    "forest green": "포레스트 그린",
    "graphite": "그래파이트",
    "gray": "회색",
    "green": "초록",
    "grey": "회색",
    "indigo": "인디고",
    "ivory": "아이보리",
    "khaki": "카키",
    "lavender": "라벤더",
    "light blue": "하늘색",
    "lilac": "라일락",
    "lime": "라임",
    "magenta": "마젠타",
    "mint": "민트",
    "mustard": "머스터드",
    "navy": "네이비",
    "ochre": "황토색",
    "olive": "올리브",
    "orange": "주황",
    "pink": "분홍",
    "plum": "플럼",
    "purple": "보라",
    "red": "빨강",
    "rust": "러스트",
    "sage": "세이지",
    "sand": "샌드",
    "silver": "은색",
    "slate blue": "슬레이트 블루",
    "tan": "탠",
    "taupe": "토프",
    "teal": "틸",
    "turquoise": "터키석색",
    "violet": "바이올렛",
    "white": "흰색",
    "yellow": "노랑",
}

HIDDEN_VALUES = frozenset({"none", "unknown"})


def upper_label(value: str) -> str | None:
    """Korean label of a known upper type; None for ``unknown`` or an unlisted value."""
    return UPPER_LABELS.get(value)


def outer_label(value: str) -> str | None:
    """Korean label of a known outer type; None for ``none``/``unknown``."""
    return OUTER_LABELS.get(value)


def lower_label(value: str) -> str | None:
    """Korean label of a known lower type; None for ``none``/``unknown``."""
    return LOWER_LABELS.get(value)


def color_label(color: str) -> str:
    """Korean label of a color, or the color as written when it has none (Req 11.7)."""
    text = color.strip()
    return COLOR_LABELS.get(text.lower(), text)
