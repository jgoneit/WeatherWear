"""Brief_Composer: labels, particle, place label and the brief text (Phase 2 tasks 4.4).

Req 11.2~11.12, 11.14, 11.15. ``test_compose_matches_user_example`` pins the
sentence of user decision 2 with hand-made inputs, independent of demo data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain import LOWER_TYPES, OUTER_TYPES, UPPER_TYPES, ClothingTags, WeatherContext
from app.notifications.compose import (
    INSUFFICIENT_DATA_TEXT,
    MAX_BODY_LENGTH,
    TITLE,
    BriefInput,
    compose_brief,
    format_humidity,
    format_temp,
    outfit_phrase,
)
from app.notifications.josa import object_particle
from app.notifications.labels_ko import (
    COLOR_LABELS,
    LOWER_LABELS,
    OUTER_LABELS,
    UPPER_LABELS,
    color_label,
)
from app.notifications.places import KNOWN_PLACES, UNKNOWN_PLACE, haversine_km, place_label
from app.schemas import OutfitOut, RecommendationItemOut, WeatherSnapshotOut

REPO_ROOT = Path(__file__).resolve().parents[2]
VISION_FIXTURE = REPO_ROOT / "data" / "demo" / "fixtures" / "vision.json"
USER_EXAMPLE = "오늘 서울 14.5°C, 습도 55%. 비슷했던 14.4°C 날 크림 니트를 입고 '적당했어'라고 했어요."


def tags(
    upper: str = "knit",
    outer: str = "none",
    lower: str = "jeans",
    colors: list[str] | None = None,
) -> ClothingTags:
    return ClothingTags(
        upper_type=upper,  # type: ignore[arg-type]
        outer_type=outer,  # type: ignore[arg-type]
        lower_type=lower,  # type: ignore[arg-type]
        colors=["cream"] if colors is None else colors,
        layers_visible=1,
        confidence=0.9,
    )


def today(
    temp: float | None = 14.5, humidity: float | None = 55.0, provider: str = "open_meteo"
) -> WeatherContext:
    return WeatherContext(
        actual_temperature_c=temp,
        apparent_temperature_c=None,
        relative_humidity_pct=humidity,
        wind_speed_kmh=None,
        precipitation_mm=None,
        observed_at=datetime(2026, 11, 14, 7, 30, tzinfo=UTC),
        provider=provider,  # type: ignore[arg-type]
        is_fixture=provider != "open_meteo",
        scenario_id="s1" if provider == "scenario" else None,
        scenario_name="쌀쌀한 아침" if provider == "scenario" else None,
    )


def item(
    past: float = 14.4,
    clothing: ClothingTags | None = None,
    vote: str | None = "comfortable",
    fallback: bool = False,
    outfit_id: int = 15,
    with_weather: bool = True,
) -> RecommendationItemOut:
    weather = WeatherSnapshotOut(
        observed_at=datetime(2025, 3, 2, 8, 0, tzinfo=UTC),
        actual_temperature_c=past,
        apparent_temperature_c=None,
        relative_humidity_pct=40.0,
        wind_speed_kmh=None,
        precipitation_mm=None,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )
    outfit = OutfitOut(
        id=outfit_id,
        captured_at=datetime(2025, 3, 2, 8, 0, tzinfo=UTC),
        latitude=37.5665,
        longitude=126.978,
        source="exif",
        metadata_synthetic=True,
        photo_url=f"/api/outfits/{outfit_id}/photo",
        tags=clothing or tags(lower="none"),
        vision_provider="fixture",
        weather=weather if with_weather else None,
        effective_vote=vote,  # type: ignore[arg-type]
    )
    return RecommendationItemOut(
        rank=1,
        outfit=outfit,
        score=100.0,
        temperature_diff_c=0.1,
        selected_by_fallback=fallback,
        confidence_level="high",
        reasons=["기온 차이 0.1°C"],
        explanation="설명",
    )


# --- user example ---------------------------------------------------------------


def test_compose_matches_user_example() -> None:
    # 015_loose_hair_front_cream.png: 14.4°C, knit, outer none, cream, comfortable.
    top = item(14.4, tags("knit", "none", "unknown", ["cream"]), "comfortable", outfit_id=15)
    message = compose_brief(BriefInput(place=place_label(37.5665, 126.978), today=today(14.5, 55), top=top))
    assert message.body == USER_EXAMPLE
    assert message.title == TITLE == "오늘의 착장 브리핑"
    assert message.url == "/outfits/15"
    assert message.outfit_id == 15


# --- body variants ----------------------------------------------------------------


def test_brief_without_vote() -> None:
    message = compose_brief(BriefInput("서울", today(), item(vote=None)))
    assert message.body == (
        "오늘 서울 14.5°C, 습도 55%. 비슷했던 14.4°C 날 크림 니트를 입었어요. 그날 어땠는지 알려 주세요."
    )


def test_brief_cold_tail() -> None:
    message = compose_brief(BriefInput("서울", today(), item(vote="cold")))
    assert message.body.endswith("크림 니트를 입고 '추웠어'라고 했어요. 오늘은 한 겹 더 챙겨 보세요.")


def test_brief_hot_tail() -> None:
    message = compose_brief(BriefInput("서울", today(), item(vote="hot")))
    assert message.body.endswith("크림 니트를 입고 '더웠어'라고 했어요. 오늘은 조금 가볍게 입어 보세요.")


def test_comfortable_has_no_tail() -> None:
    body = compose_brief(BriefInput("서울", today(), item(vote="comfortable"))).body
    assert body.endswith("'적당했어'라고 했어요.")
    assert "오늘은" not in body.split(". ", 1)[1]


def test_brief_fallback_wording() -> None:
    body = compose_brief(BriefInput("서울", today(), item(past=9.0, fallback=True))).body
    assert " 기온이 가장 가까웠던 9.0°C 날 " in body
    assert "비슷했던" not in body


def test_brief_insufficient_data() -> None:
    message = compose_brief(BriefInput("서울", today(14.5, 55), None))
    assert message.body == (
        "오늘 서울 14.5°C, 습도 55%. 비슷한 기온에서 입었던 착장 기록이 아직 없어요. "
        "오늘 입은 옷을 찍어 두면 다음에 알려 드릴게요."
    )
    assert message.url == "/"
    assert message.outfit_id is None


def test_top_without_weather_counts_as_no_data() -> None:
    message = compose_brief(BriefInput("서울", today(), item(with_weather=False)))
    assert message.body.endswith(INSUFFICIENT_DATA_TEXT)
    assert message.url == "/"


def test_brief_without_humidity() -> None:
    body = compose_brief(BriefInput("서울", today(14.5, None), item())).body
    assert body.startswith("오늘 서울 14.5°C. 비슷했던 14.4°C 날 ")
    assert "습도" not in body


def test_title_marks_fixture_and_scenario() -> None:
    top = item()
    assert compose_brief(BriefInput("서울", today(provider="open_meteo"), top)).title == "오늘의 착장 브리핑"
    assert (
        compose_brief(BriefInput("서울", today(provider="fixture"), top)).title
        == "오늘의 착장 브리핑 (fixture 날씨)"
    )
    scenario = compose_brief(BriefInput("서울", today(provider="scenario"), top))
    assert scenario.title == "오늘의 착장 브리핑 (시나리오 날씨)"
    assert "시나리오" not in scenario.body and "fixture" not in scenario.body


def test_current_location_label_in_body() -> None:
    body = compose_brief(BriefInput(place_label(0.0, 0.0), today(), item())).body
    assert body.startswith("오늘 현재 위치 14.5°C")


def test_missing_today_temperature_raises() -> None:
    with pytest.raises(ValueError):
        compose_brief(BriefInput("서울", today(temp=None), item()))


def test_temperatures_and_humidity_rounding() -> None:
    assert format_temp(14.45) == "14.5°C"
    assert format_temp(-0.04) == "0.0°C"
    assert format_temp(-3.25) == "-3.3°C"
    assert format_humidity(54.5) == "55%"
    assert format_humidity(54.4) == "54%"
    assert format_humidity(0.0) == "0%"
    body = compose_brief(BriefInput("서울", today(9.95, 62.5), item(past=-1.25))).body
    assert body.startswith("오늘 서울 10.0°C, 습도 63%. 비슷했던 -1.3°C 날 ")


def test_long_color_is_cut_to_keep_body_short() -> None:
    long_color = "x" * 30
    top = item(clothing=tags("long_sleeve_t_shirt", "fleece_jacket", "jeans", [long_color]), vote="cold")
    message = compose_brief(BriefInput("현재 위치", today(-12.35, 100.0), top))
    assert len(message.body) <= MAX_BODY_LENGTH
    assert long_color in message.body  # still fits, so nothing is cut


def test_body_over_limit_cuts_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.notifications import compose

    monkeypatch.setattr(compose, "MAX_BODY_LENGTH", 80)
    top = item(clothing=tags("knit", "blazer", "jeans", ["y" * 30]), vote="cold")
    body = compose.compose_brief(BriefInput("서울", today(), top)).body
    assert len(body) <= 80
    assert "y" * 13 not in body


# --- outfit phrase ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("clothing", "expected"),
    [
        (tags("knit", "none", "jeans", ["cream"]), "크림 니트"),
        (tags("knit", "unknown", "jeans", ["cream"]), "크림 니트"),
        (tags("knit", "blazer", "jeans", ["camel"]), "카멜 니트에 블레이저"),
        (tags("unknown", "coat", "jeans", ["black"]), "검정 코트"),
        (tags("unknown", "none", "jeans", ["navy"]), "네이비 청바지"),
        (tags("unknown", "unknown", "skirt", []), "치마"),
        (tags("unknown", "unknown", "unknown", ["red"]), "그날 착장"),
        (tags("unknown", "none", "none", []), "그날 착장"),
        (tags("dress", "none", "none", ["white"]), "흰색 원피스"),
        (tags("knit", "none", "jeans", []), "니트"),
        (tags("shirt", "none", "jeans", ["neon green"]), "neon green 셔츠"),
        (tags("shirt", "none", "jeans", ["Light Blue"]), "하늘색 셔츠"),
    ],
)
def test_outfit_phrase_rules(clothing: ClothingTags, expected: str) -> None:
    assert outfit_phrase(clothing) == expected


def test_camel_knit_with_blazer_particle() -> None:
    top = item(clothing=tags("knit", "blazer", "jeans", ["camel"]))
    assert "카멜 니트에 블레이저를 입고" in compose_brief(BriefInput("서울", today(), top)).body


def test_particle_after_final_consonant_in_body() -> None:
    top = item(clothing=tags("unknown", "long_puffer", "jeans", ["black"]))
    assert "검정 롱패딩을 입고" in compose_brief(BriefInput("서울", today(), top)).body


# --- labels -------------------------------------------------------------------------


def test_every_garment_type_has_a_korean_label() -> None:
    hidden = {"none", "unknown"}
    assert set(UPPER_LABELS) == set(UPPER_TYPES) - hidden
    assert set(OUTER_LABELS) == set(OUTER_TYPES) - hidden
    assert set(LOWER_LABELS) == set(LOWER_TYPES) - hidden
    for label in [*UPPER_LABELS.values(), *OUTER_LABELS.values(), *LOWER_LABELS.values()]:
        assert label and len(label) <= 12
        assert "_" not in label and not label.isascii()


def _fixture_colors() -> set[str]:
    data = json.loads(VISION_FIXTURE.read_text(encoding="utf-8"))
    return {color for entry in data.values() for color in entry["tags"]["colors"]}


def test_every_demo_fixture_color_has_a_label() -> None:
    colors = _fixture_colors()
    assert colors  # the fixture is tracked in git
    missing = sorted(c for c in colors if c.strip().lower() not in COLOR_LABELS)
    assert missing == []
    for color in colors:
        assert not color_label(color).isascii()


def test_unlabelled_color_is_kept_as_written() -> None:
    assert color_label("neon green") == "neon green"
    assert color_label(" cream ") == "크림"


# --- particle -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("니트", "를"),
        ("블레이저", "를"),
        ("롱패딩", "을"),
        ("청바지", "를"),
        ("코트", "를"),
        ("반바지", "를"),
        ("카디건", "을"),
        ("그날 착장", "을"),
        ("가", "를"),
        ("힣", "을"),
        ("jeans", "을(를)"),
        ("니트2", "을(를)"),
        ("", "을(를)"),
        ("ㄱ", "을(를)"),
    ],
)
def test_object_particle(word: str, expected: str) -> None:
    assert object_particle(word) == expected


# --- place label ---------------------------------------------------------------------


def test_default_location_is_seoul() -> None:
    assert place_label(37.5665, 126.9780) == "서울"
    assert place_label(37.57, 126.98) == "서울"


def test_known_places_are_sixteen_and_label_themselves() -> None:
    assert len(KNOWN_PLACES) == 16
    for name, lat, lon in KNOWN_PLACES:
        assert place_label(lat, lon) == name


def test_far_away_location_is_current_location() -> None:
    assert place_label(0.0, 0.0) == UNKNOWN_PLACE == "현재 위치"
    assert place_label(40.7128, -74.006) == "현재 위치"  # New York
    assert place_label(float("nan"), 126.98) == "현재 위치"


def test_thirty_km_boundary() -> None:
    _, lat, lon = KNOWN_PLACES[-1]  # 제주, no other place nearby
    # Move north until just inside / just outside 30 km.
    inside = lat + 0.269
    outside = lat + 0.271
    assert haversine_km(inside, lon, lat, lon) <= 30.0 < haversine_km(outside, lon, lat, lon)
    assert place_label(inside, lon) == "제주"
    assert place_label(outside, lon) == "현재 위치"


def test_nearest_place_wins() -> None:
    # Between Seoul and Incheon, closer to Incheon.
    assert place_label(37.47, 126.73) == "인천"
    # Ilsan area: closer to Goyang than to Seoul.
    assert place_label(37.66, 126.83) == "고양"
