#!/usr/bin/env python3
"""Build the WeatherWear demo fixtures from the photos in ``data/demo/photos/``.

What it writes (design.md "데모 데이터 파일"):

- ``<stem>.metadata.json`` sidecars for every photo. JPG ``captured_at`` values
  (taken from the ``YYYY-MM-DD_HHMMSS_KST`` file name by
  ``prepare_demo_photos.py``) and any non-null PNG value already in a sidecar are
  kept. A null PNG ``captured_at`` gets a unique, deterministic ``+09:00`` time
  between 07:00 and 20:00 in a season that matches the photo's fixture
  temperature. ``thermal_vote`` follows rule T6.
- ``fixtures/weather.json``: one entry per photo with the temperature from the
  design table ``TEMPERATURES`` plus derived apparent temperature, humidity,
  wind and precipitation, and ``today_default`` (9.0°C).
- ``fixtures/vision.json``: ``{sha256: VisionAnalysis}`` for every photo. Values
  were written by looking at each photo (``TAGS``). The 20 non-outfit JPGs are
  labeled ``non_outfit`` and carry ``UNKNOWN_TAGS``.

``--check`` validates the written files instead of building them: sidecars
against ``SidecarMetadata``, vision entries against ``VisionAnalysis``, the
temperature rules T1-T6, and the expected outcome of each demo scenario.

The script is deterministic: running it twice produces identical files.

Usage: python scripts/build_fixtures.py [--check] [--photos DIR] [--fixtures DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "data" / "demo"
DEFAULT_PHOTOS = DEMO_DIR / "photos"
DEFAULT_FIXTURES = DEMO_DIR / "fixtures"
SCENARIOS_PATH = DEMO_DIR / "scenarios.json"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SIDECAR_SUFFIX = ".metadata.json"
KST = timezone(timedelta(hours=9))
SEOUL = (37.5665, 126.9780)

# Seoul spots used for PNG sidecars (all inside the city).
SEOUL_SPOTS = [
    (37.5665, 126.9780),  # 시청
    (37.5563, 126.9236),  # 홍대입구
    (37.4979, 127.0276),  # 강남역
    (37.5446, 127.0559),  # 성수
    (37.5219, 126.9245),  # 여의도
    (37.5796, 126.9770),  # 경복궁
    (37.5340, 126.9948),  # 이태원
]

# design.md "사진별 fixture 기온" (°C → photo stems without extension).
_TEMPERATURE_TABLE = """
-9.0: 076_winter_navy_long_parka
-8.5: 071_winter_cream_long_puffer
-6.5: 2025-01-08_081500_KST
-5.0: 070_winter_puffer_denim
-4.5: 2025-12-17_081000_KST
-4.0: 072_winter_forest_puffer
-3.5: 074_winter_burgundy_puffer
-3.0: 073_winter_blue_puffer_skirt, 2025-01-08_121800_KST
-2.5: 2025-02-12_084000_KST
-2.0: 2025-01-24_200600_KST
-1.5: 075_winter_cropped_puffer_corduroy, 2025-12-17_175400_KST
-1.0: 2025-01-24_191000_KST
1.0: 069_winter_wool_coat_skirt
1.5: 2025-02-12_124600_KST
2.5: 2025-11-18_082000_KST
3.5: 2025-03-05_090500_KST
5.5: 117_hiking_fleece_excited, 2025-03-05_121200_KST, 2025-11-18_124300_KST
6.0: 096_outdoor_hiking_fleece
6.5: 059_charcoal_turtleneck_waist
7.0: 043_black_turtleneck_jeans_full
7.5: 083_techwear_cargo_layers
8.0: 021_cafe_cream_sweater, 082_grunge_leather_plaid, 116_techwear_alert_focus
8.5: 052_burgundy_sweater_skirt_full, 119_grunge_leather_confident
9.0: 033_home_sage_sweater, 081_hiphop_varsity_baggy_denim
9.5: 026_gallery_black_knit, 104_punk_tartan_trousers
10.0: 014_warm_indoor_black, 079_autumn_denim_corduroy, 123_gorpcore_color_shell
10.5: 045_navy_knit_dress_full, 099_evening_velvet_satin
11.0: 049_sage_running_jacket_full, 066_autumn_suede_denim, 094_minimalist_knit_dress, 125_techwear_red_hoodie
11.5: 037_cafe_rust_cardigan, 057_black_sport_jacket_knee, 2025-04-14_082500_KST
12.0: 015_loose_hair_front_cream, 032_street_navy_cardigan, 084_skater_flannel_board, 124_thrift_patchwork_grunge
12.5: 030_park_gray_hoodie, 067_autumn_burgundy_skirt, 098_canvas_workwear_utility, 113_rain_trench_relief, 2025-10-29_184500_KST, 2025-10-29_193400_KST
13.0: 010_front_subtle_surprise_rust, 040_rainy_street_ochre_jacket, 060_navy_cardigan_waist, 088_preppy_cardigan_pleated, 107_rainy_day_sage_trench, 121_hongdae_colorblock_denim
13.5: 022_library_blue_cardigan, 044_olive_cardigan_pleated_midi_full, 080_rainy_spring_trench, 118_preppy_cardigan_curious
14.0: 012_slight_upward_gaze_grey, 036_park_beige_jacket, 061_spring_trench_navy_skirt, 087_tailored_office_suit, 112_office_suit_focused, 2025-10-08_083000_KST
14.5: 019_professional_grey_blazer, 047_beige_trench_trousers_full, 068_autumn_utility_casual, 102_balletcore_wrap_cardigan, 2025-03-25_122000_KST
15.0: 008_right_smile_cardigan, 023_park_denim_jacket, 053_camel_blazer_seated_knee, 089_quiet_luxury_camel_blazer
15.5: 016_loose_hair_threequarter_black, 041_taupe_blazer_full, 086_retro_athleisure_track, 105_smart_casual_long_vest, 122_rugby_skater_store, 2025-03-25_150400_KST
16.0: 011_front_composed_navy, 050_denim_overshirt_full, 090_monochrome_creative_office, 101_nineties_colorblock_track
16.5: 020_over_shoulder_navy, 062_spring_cardigan_denim, 2025-04-14_123200_KST
17.0: 013_slight_downward_gaze_denim, 063_spring_light_jacket_skirt, 095_denim_on_denim_coffee, 2025-04-29_175200_KST
17.5: 028_cafe_loose_hair, 077_spring_denim_cream_trousers, 111_colorblock_street_amused, 2025-04-29_171500_KST
18.0: 009_threequarter_neutral_green, 038_street_loose_hair, 055_blue_knit_linen_knee, 106_eclectic_colorblock_city
18.5: 029_home_blue_oxford, 046_black_evening_midi_full, 092_vintage_seventies_pleats, 114_cafe_denim_laugh, 2025-05-16_091000_KST
19.0: 031_library_tan_shirt, 048_charcoal_activewear_full, 109_tailored_jumpsuit_evening, 2025-10-08_150700_KST
19.5: 018_soft_backlight_sage, 085_y2k_parachute_pants
20.0: 039_home_striped_top, 056_forest_green_dress_knee, 110_denim_maxi_street, 2025-05-29_191100_KST
20.5: 034_gallery_white_blouse, 097_running_athleisure_tie_shoe, 2025-05-16_174100_KST
21.0: 027_seaside_striped_shirt, 058_ivory_blouse_waist, 115_romantic_skirt_shy_smile, 2025-09-16_091500_KST
21.5: 042_white_shirt_navy_skirt_full, 108_retro_citypop_casual, 2025-05-29_182000_KST
22.0: 017_ponytail_outdoor_white, 091_romantic_pastel_date_dress
22.5: 035_seaside_loose_hair, 103_cottagecore_gingham, 2025-06-12_083500_KST
23.0: 2025-09-16_171800_KST
23.5: 025_street_linen_blouse, 2025-07-14_182600_KST
24.0: 007_front_natural_laugh_linen, 093_bohemian_floral_maxi, 2025-06-12_121400_KST, 2025-08-28_185200_KST
24.5: 054_white_tee_denim_skirt_knee, 2025-06-28_144300_KST, 2025-07-30_200300_KST, 2025-08-13_152400_KST, 2025-08-28_084500_KST
25.0: 024_home_white_tee, 065_summer_stripe_midi, 2025-07-14_081000_KST
25.5: 051_ivory_linen_trousers_full, 120_resort_linen_serene
26.0: 064_summer_linen_trousers, 100_resort_linen_color, 2025-06-28_130500_KST
26.5: 078_summer_sage_linen_dress, 2025-07-30_190500_KST, 2025-08-13_121500_KST
"""


def _parse_temperature_table(text: str) -> dict[str, float]:
    table: dict[str, float] = {}
    for line in text.strip().splitlines():
        temp, names = line.split(":", 1)
        for name in names.split(","):
            stem = name.strip()
            if stem in table:
                raise ValueError(f"duplicate stem in temperature table: {stem}")
            table[stem] = float(temp)
    return table


TEMPERATURES = _parse_temperature_table(_TEMPERATURE_TABLE)

# JPGs without a person (food, cafe, street, landscape, grocery bag photos).
NON_OUTFIT = {
    "2025-01-08_121800_KST", "2025-01-24_200600_KST", "2025-02-12_124600_KST",
    "2025-03-05_121200_KST", "2025-03-25_150400_KST", "2025-04-14_123200_KST",
    "2025-04-29_175200_KST", "2025-05-16_174100_KST", "2025-05-29_191100_KST",
    "2025-06-12_121400_KST", "2025-06-28_144300_KST", "2025-07-14_182600_KST",
    "2025-07-30_200300_KST", "2025-08-13_152400_KST", "2025-08-28_185200_KST",
    "2025-09-16_171800_KST", "2025-10-08_150700_KST", "2025-10-29_193400_KST",
    "2025-11-18_124300_KST", "2025-12-17_175400_KST",
}

# Rule T6 fixed votes.
FIXED_VOTES = {
    "081_hiphop_varsity_baggy_denim": "comfortable",
    "033_home_sage_sweater": "cold",
    "021_cafe_cream_sweater": "comfortable",
    "076_winter_navy_long_parka": "comfortable",
}
# Cycle used for the ~1/3 of remaining outfit photos that get a seed vote.
_VOTE_CYCLE = ("comfortable", "cold", "comfortable", "hot")

# Photos with rain (precipitation_mm > 0).
RAIN_MM = {
    "040_rainy_street_ochre_jacket": 3.5,
    "080_rainy_spring_trench": 2.0,
    "107_rainy_day_sage_trench": 4.5,
    "113_rain_trench_relief": 1.5,
    "2025-07-14_182600_KST": 6.0,
}

# Hand-checked clothing tags: key | upper | outer | lower | colors | layers | confidence.
# PNG keys are the 3-digit prefix, JPG keys the full stem. Face-only portraits
# use lower_type "unknown"; dresses/jumpsuits use lower_type "none".
_TAGS_TABLE = """
007|shirt|none|unknown|white|1|0.6
008|t_shirt|cardigan|unknown|beige,white|2|0.7
009|knit|none|unknown|green|1|0.6
010|knit|none|unknown|rust|1|0.6
011|knit|none|unknown|navy|1|0.6
012|knit|none|unknown|grey|1|0.6
013|unknown|shirt_jacket|unknown|blue,white|2|0.6
014|knit|none|unknown|black|1|0.6
015|knit|none|unknown|cream|1|0.6
016|shirt|none|unknown|black|1|0.6
017|shirt|none|unknown|white|1|0.6
018|blouse|none|unknown|sage|1|0.6
019|t_shirt|blazer|unknown|grey,white|2|0.7
020|shirt|none|unknown|navy|1|0.6
021|knit|none|unknown|cream|1|0.7
022|t_shirt|cardigan|jeans|light blue,white,blue|2|0.65
023|t_shirt|denim_jacket|chinos|blue,white,beige|2|0.85
024|t_shirt|none|jeans|white,blue|1|0.8
025|shirt|none|trousers|olive,cream|1|0.85
026|knit|none|skirt|black,charcoal|1|0.75
027|long_sleeve_t_shirt|none|jeans|navy,white,light blue|1|0.85
028|unknown|cardigan|unknown|beige,black|2|0.65
029|shirt|none|trousers|light blue,black|1|0.75
030|hoodie|none|track_pants|grey,black|1|0.85
031|sleeveless_top|shirt_jacket|jeans|tan,white,navy|2|0.8
032|t_shirt|cardigan|trousers|navy,white,beige|2|0.85
033|knit|none|trousers|sage,cream|1|0.65
034|blouse|none|jeans|white,blue|1|0.75
035|sleeveless_top|shirt_jacket|trousers|light blue,white,cream|2|0.75
036|sleeveless_top|jacket|cargo_pants|beige,white,olive|2|0.85
037|t_shirt|cardigan|unknown|rust,cream|2|0.7
038|t_shirt|none|jeans|black,blue|1|0.7
039|long_sleeve_t_shirt|none|trousers|cream,navy,brown|1|0.75
040|t_shirt|rain_jacket|jeans|ochre,white,blue|2|0.9
041|t_shirt|blazer|trousers|taupe,white,charcoal|2|0.9
042|blouse|none|skirt|white,navy|1|0.9
043|turtleneck|none|jeans|black,blue|1|0.9
044|t_shirt|cardigan|skirt|olive,white,cream|2|0.9
045|dress|none|none|navy|1|0.9
046|dress|none|none|black|1|0.9
047|sleeveless_top|trench_coat|trousers|beige,cream,brown|2|0.9
048|t_shirt|none|leggings|charcoal,navy|1|0.9
049|unknown|track_jacket|leggings|sage,black|1|0.85
050|t_shirt|shirt_jacket|jeans|blue,white|2|0.9
051|shirt|none|trousers|ivory,beige|1|0.9
052|knit|none|skirt|burgundy,charcoal|1|0.9
053|knit|blazer|trousers|camel,light blue,charcoal|2|0.85
054|t_shirt|none|skirt|white,blue|1|0.9
055|knit|none|trousers|light blue,ivory|1|0.85
056|dress|none|none|forest green|1|0.85
057|unknown|track_jacket|trousers|black,charcoal|1|0.75
058|blouse|none|trousers|ivory,charcoal|1|0.7
059|turtleneck|none|unknown|charcoal|1|0.65
060|t_shirt|cardigan|unknown|navy,grey|2|0.7
061|shirt|trench_coat|skirt|beige,light blue,navy|2|0.85
062|t_shirt|cardigan|trousers|olive,white,cream|2|0.85
063|t_shirt|jacket|jeans|lavender,white,navy|2|0.85
064|blouse|none|trousers|cream,beige|1|0.85
065|t_shirt|none|skirt|navy,white,blue|1|0.85
066|knit|jacket|jeans|brown,cream,navy|2|0.85
067|blouse|cardigan|skirt|burgundy,cream,camel|2|0.85
068|hoodie|utility_jacket|cargo_pants|olive,grey,black|2|0.85
069|knit|coat|skirt|charcoal,grey,cream|2|0.8
070|knit|puffer_jacket|jeans|grey,light blue,charcoal|2|0.85
071|unknown|long_puffer|skirt|cream,brown,charcoal|1|0.75
072|turtleneck|puffer_jacket|trousers|forest green,beige,black|2|0.85
073|hoodie|puffer_jacket|skirt|light blue,grey,charcoal|2|0.85
074|turtleneck|puffer_jacket|skirt|burgundy,black|2|0.85
075|knit|short_puffer|corduroy_pants|beige,cream,brown|2|0.85
076|unknown|parka|trousers|navy,lavender,charcoal|1|0.8
077|t_shirt|denim_jacket|trousers|blue,yellow,cream|2|0.9
078|dress|none|none|sage|1|0.9
079|t_shirt|shirt_jacket|corduroy_pants|blue,white,rust|2|0.9
080|long_sleeve_t_shirt|trench_coat|jeans|olive,navy,white,black|2|0.85
081|t_shirt|varsity_jacket|jeans|burgundy,cream,white,black|2|0.9
082|unknown|leather_jacket|skirt|black,burgundy,charcoal|2|0.85
083|hoodie|rain_jacket|cargo_pants|black,grey|3|0.8
084|t_shirt|shirt_jacket|jeans|green,white,light blue|2|0.9
085|sleeveless_top|denim_jacket|parachute_pants|navy,white,silver|2|0.9
086|t_shirt|track_jacket|track_pants|teal,cream,black|2|0.85
087|shirt|blazer|trousers|navy,white|2|0.9
088|shirt|cardigan|skirt|cream,light blue,navy|2|0.9
089|knit|blazer|trousers|camel,cream,brown|2|0.9
090|unknown|blazer|trousers|charcoal,black|2|0.85
091|dress|cardigan|none|dusty pink,beige|2|0.85
092|turtleneck|none|skirt|rust,brown|1|0.9
093|blouse|none|skirt|beige,olive,cream|1|0.85
094|dress|coat|none|forest green,charcoal|2|0.85
095|t_shirt|shirt_jacket|jeans|blue,white|2|0.9
096|t_shirt|fleece_jacket|cargo_pants|rust,black,olive|2|0.85
097|t_shirt|windbreaker|leggings|cobalt blue,black|2|0.85
098|t_shirt|utility_jacket|chinos|olive,khaki,navy,white|2|0.85
099|dress|blazer|none|champagne,black|2|0.85
100|sleeveless_top|shirt_jacket|trousers|light blue,white,rust|2|0.9
101|t_shirt|track_jacket|track_pants|navy,red,cream,white|2|0.9
102|cardigan|none|skirt|dusty pink,taupe|2|0.8
103|dress|cardigan|none|grey,white,beige|2|0.85
104|t_shirt|blazer|trousers|charcoal,black,burgundy|2|0.85
105|blouse|vest|trousers|taupe,cream,teal|2|0.85
106|knit|blazer|trousers|magenta,cobalt blue,black|2|0.9
107|long_sleeve_t_shirt|trench_coat|jeans|sage,navy,white,black|2|0.85
108|t_shirt|cardigan|trousers|mustard,burgundy,navy,white|2|0.9
109|jumpsuit|none|none|forest green|1|0.85
110|sweatshirt|none|skirt|charcoal,navy|1|0.85
111|t_shirt|bomber_jacket|cargo_pants|green,rust,white,black|2|0.85
112|blouse|blazer|trousers|navy,cream|2|0.9
113|long_sleeve_t_shirt|trench_coat|jeans|sage,navy,white|2|0.85
114|t_shirt|shirt_jacket|trousers|blue,white,cream|2|0.9
115|blouse|cardigan|skirt|yellow,cream,lavender|2|0.85
116|unknown|rain_jacket|cargo_pants|black,charcoal|2|0.8
117|unknown|fleece_jacket|cargo_pants|rust,olive|1|0.8
118|shirt|cardigan|skirt|cream,light blue,navy|2|0.9
119|unknown|leather_jacket|skirt|black,burgundy|2|0.85
120|sleeveless_top|shirt_jacket|trousers|mint,white,cream|2|0.9
121|hoodie|denim_jacket|cargo_pants|blue,lime,burgundy|2|0.9
122|jersey|none|jeans|purple,cream,light blue|1|0.85
123|fleece|rain_jacket|cargo_pants|orange,teal,olive|2|0.85
124|t_shirt|cardigan|jeans|mustard,green,burgundy,black,blue|2|0.85
125|hoodie|vest|cargo_pants|red,black,charcoal|2|0.85
126|hoodie|jersey|track_pants|navy,grey,red,white|2|0.85
127|t_shirt|windbreaker|shorts|turquoise,charcoal,white,black|2|0.85
128|coveralls|none|none|indigo,white,red,tan|2|0.85
129|hoodie|vest|cargo_pants|black,chartreuse,violet,orange|2|0.9
130|sweatshirt|none|trousers|cream,grey,blue,black|2|0.85
131|hoodie|windbreaker|parachute_pants|violet,yellow,black|2|0.9
132|shirt|vest|corduroy_pants|green,plum,rust,cream,red|2|0.85
133|jersey|utility_jacket|jeans|orange,navy,indigo,white|2|0.9
134|long_sleeve_t_shirt|vest|track_pants|slate blue,white,graphite,mint|2|0.85
135|t_shirt|shirt_jacket|jeans|black,burgundy,red|2|0.85
136|t_shirt|none|shorts|cobalt blue,yellow,red,black,white|2|0.85
137|jersey|none|track_pants|black,white,red|2|0.9
138|t_shirt|cardigan|cargo_pants|lilac,white,forest green,silver|2|0.85
139|long_sleeve_t_shirt|vest|trousers|charcoal,white,cream,black|2|0.85
140|hoodie|utility_jacket|parachute_pants|mustard,teal,black,orange|2|0.9
2025-01-08_081500_KST|unknown|long_puffer|jeans|black,beige,navy|1|0.8
2025-01-24_191000_KST|unknown|puffer_jacket|jeans|cream,grey,charcoal|1|0.8
2025-02-12_084000_KST|turtleneck|long_puffer|jeans|black,grey,navy|2|0.85
2025-03-05_090500_KST|knit|coat|jeans|charcoal,cream,navy|2|0.85
2025-03-25_122000_KST|knit|trench_coat|jeans|beige,navy,blue|2|0.85
2025-04-14_082500_KST|t_shirt|denim_jacket|trousers|blue,white,black|2|0.9
2025-04-29_171500_KST|t_shirt|cardigan|jeans|grey,white,navy|2|0.85
2025-05-16_091000_KST|shirt|none|chinos|light blue,cream|1|0.9
2025-05-29_182000_KST|t_shirt|none|jeans|white,blue|1|0.9
2025-06-12_083500_KST|t_shirt|none|trousers|navy,beige|1|0.9
2025-06-28_130500_KST|t_shirt|none|shorts|white,black|1|0.9
2025-07-14_081000_KST|t_shirt|none|shorts|grey,blue|1|0.9
2025-07-30_190500_KST|t_shirt|none|trousers|navy,beige|1|0.9
2025-08-13_121500_KST|t_shirt|none|shorts|white,beige|1|0.9
2025-08-28_084500_KST|shirt|none|trousers|light blue,black|1|0.9
2025-09-16_091500_KST|t_shirt|cardigan|jeans|grey,white,navy|2|0.9
2025-10-08_083000_KST|knit|none|jeans|cream,navy|1|0.9
2025-10-29_184500_KST|knit|trench_coat|trousers|beige,navy,black|2|0.85
2025-11-18_082000_KST|knit|coat|jeans|charcoal,beige,grey,navy|2|0.85
2025-12-17_081000_KST|unknown|long_puffer|trousers|black,beige,charcoal|1|0.85
"""

UNKNOWN_TAGS = {
    "upper_type": "unknown",
    "outer_type": "unknown",
    "lower_type": "unknown",
    "colors": [],
    "layers_visible": 0,
    "confidence": 0.0,
}


def _parse_tags_table(text: str) -> dict[str, dict]:
    tags: dict[str, dict] = {}
    for line in text.strip().splitlines():
        key, upper, outer, lower, colors, layers, confidence = line.split("|")
        if key in tags:
            raise ValueError(f"duplicate key in tags table: {key}")
        tags[key] = {
            "upper_type": upper,
            "outer_type": outer,
            "lower_type": lower,
            "colors": [c.strip() for c in colors.split(",") if c.strip()],
            "layers_visible": int(layers),
            "confidence": float(confidence),
        }
    return tags


TAGS = _parse_tags_table(_TAGS_TABLE)

TODAY_DEFAULT = {
    "actual_temperature_c": 9.0,
    "apparent_temperature_c": 6.5,
    "relative_humidity_pct": 55,
    "wind_speed_kmh": 12,
    "precipitation_mm": 0.0,
}

SCENARIOS = [
    {"id": "chilly-morning", "name": "쌀쌀한 아침", "actual_temperature_c": 9.0,
     "apparent_temperature_c": 6.5, "relative_humidity_pct": 55, "wind_speed_kmh": 12,
     "precipitation_mm": 0},
    {"id": "cold-snap", "name": "기록적 한파", "actual_temperature_c": -12.0,
     "apparent_temperature_c": -18.0, "relative_humidity_pct": 35, "wind_speed_kmh": 20,
     "precipitation_mm": 0},
    {"id": "early-heat", "name": "때 이른 무더위", "actual_temperature_c": 30.0,
     "apparent_temperature_c": 33.0, "relative_humidity_pct": 65, "wind_speed_kmh": 6,
     "precipitation_mm": 0},
    {"id": "heatwave", "name": "기록적 폭염", "actual_temperature_c": 38.0,
     "apparent_temperature_c": 41.0, "relative_humidity_pct": 70, "wind_speed_kmh": 5,
     "precipitation_mm": 0},
]

MONTHLY_HUMIDITY = {1: 45, 2: 48, 3: 52, 4: 55, 5: 60, 6: 68, 7: 78, 8: 76,
                    9: 66, 10: 60, 11: 55, 12: 50}


# ---------------------------------------------------------------- helpers

def list_photos(photo_dir: Path) -> list[Path]:
    return sorted(
        p for p in photo_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def sidecar_path_for(image: Path) -> Path:
    return image.with_name(image.stem + SIDECAR_SUFFIX)


def tags_for(stem: str) -> dict:
    if stem in NON_OUTFIT:
        return dict(UNKNOWN_TAGS, colors=[])
    if stem in TAGS:
        return TAGS[stem]
    prefix = stem.split("_", 1)[0]
    if prefix in TAGS and not stem.startswith("20"):
        return TAGS[prefix]
    raise KeyError(f"no hand-checked tags for {stem}")


def vision_analysis_for(stem: str) -> dict:
    tags = tags_for(stem)
    is_non_outfit = stem in NON_OUTFIT
    return {
        "photo_type": "non_outfit" if is_non_outfit else "outfit",
        "photo_type_confidence": 1.0,
        "reason": (
            "No person with a visible outfit."
            if is_non_outfit
            else "A person with visible clothing is the main subject."
        ),
        "tags": tags,
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def season_months(temp: float, rank_fraction: float, i: int) -> tuple[int, int]:
    """(year, month) for a PNG photo, by design "사이드카 작성 규칙"."""
    if temp < 5:
        if rank_fraction < 0.5:
            return 2025, 1
        return (2024, 12) if i % 2 == 0 else (2025, 2)
    if temp < 17:
        if rank_fraction < 0.5:
            return (2025, 3) if i % 2 == 0 else (2025, 11)
        return (2025, 4) if i % 2 == 0 else (2025, 10)
    if temp < 22:
        return (2025, 5) if i % 2 == 0 else (2025, 9)
    if rank_fraction < 0.34:
        return 2025, 6
    return (2025, 7) if i % 2 == 0 else (2025, 8)


def _bucket(temp: float) -> int:
    return 0 if temp < 5 else 1 if temp < 17 else 2 if temp < 22 else 3


def generate_png_times(stems: list[str], taken: set[datetime]) -> dict[str, datetime]:
    """Unique +09:00 times between 07:00 and 19:55 for each PNG stem."""
    result: dict[str, datetime] = {}
    by_bucket: dict[int, list[str]] = {}
    for stem in stems:
        by_bucket.setdefault(_bucket(TEMPERATURES[stem]), []).append(stem)
    for bucket in sorted(by_bucket):
        members = sorted(by_bucket[bucket], key=lambda s: (TEMPERATURES[s], s))
        n = len(members)
        for i, stem in enumerate(members):
            fraction = i / (n - 1) if n > 1 else 0.0
            year, month = season_months(TEMPERATURES[stem], fraction, i)
            day = 1 + (i * 11) % 28
            hour = 7 + (i * 5) % 13
            minute = ((i * 7) % 12) * 5
            candidate = datetime(year, month, day, hour, minute, tzinfo=KST)
            while candidate in taken:
                candidate += timedelta(days=1)
                if candidate.month != month:
                    candidate = candidate.replace(day=1) + timedelta(minutes=5)
            taken.add(candidate)
            result[stem] = candidate
    return result


def seed_votes(stems: list[str]) -> dict[str, str | None]:
    """Rule T6: fixed votes, then about 1/3 of other outfit photos."""
    votes: dict[str, str | None] = {}
    others = sorted(s for s in stems if s not in FIXED_VOTES and s not in NON_OUTFIT)
    k = 0
    for idx, stem in enumerate(others):
        if idx % 3 == 1:
            votes[stem] = _VOTE_CYCLE[k % len(_VOTE_CYCLE)]
            k += 1
        else:
            votes[stem] = None
    for stem in stems:
        if stem in NON_OUTFIT:
            votes[stem] = None
    votes.update({s: v for s, v in FIXED_VOTES.items() if s in stems})
    return votes


def _round1(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def derived_weather(stem: str, idx: int, captured_at: datetime) -> dict:
    temp = TEMPERATURES[stem]
    month = captured_at.month
    humidity = MONTHLY_HUMIDITY[month] + (idx % 5) * 2
    if stem in RAIN_MM:
        humidity = min(95, humidity + 25)
    wind = 6 + (idx * 3) % 10 + (4 if temp < 5 else 0)
    if temp <= 10:
        apparent = temp - wind * 0.2
    elif temp >= 22:
        apparent = temp + (humidity - 60) * 0.05 + 0.5
    else:
        apparent = temp - wind * 0.05
    return {
        "actual_temperature_c": temp,
        "apparent_temperature_c": _round1(apparent),
        "relative_humidity_pct": float(humidity),
        "wind_speed_kmh": float(wind),
        "precipitation_mm": RAIN_MM.get(stem, 0.0),
    }


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- build

def build(photo_dir: Path, fixtures_dir: Path) -> None:
    photos = list_photos(photo_dir)
    stems = [p.stem for p in photos]
    missing_temp = [s for s in stems if s not in TEMPERATURES]
    if missing_temp:
        raise SystemExit(f"photos without a fixture temperature: {missing_temp}")
    extra_temp = sorted(set(TEMPERATURES) - set(stems))
    if extra_temp:
        print(f"warning: temperature table entries without a photo: {extra_temp}")

    sidecars: dict[str, dict] = {}
    for photo in photos:
        path = sidecar_path_for(photo)
        sidecars[photo.stem] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    taken = {
        datetime.fromisoformat(sc["captured_at"])
        for sc in sidecars.values() if sc.get("captured_at")
    }
    need_time = [p.stem for p in photos
                 if not sidecars[p.stem].get("captured_at") and p.suffix.lower() == ".png"]
    generated = generate_png_times(need_time, taken)
    votes = seed_votes(stems)

    png_index = 0
    for photo in photos:
        stem = photo.stem
        old = sidecars[stem]
        captured = old.get("captured_at") or (
            generated[stem].isoformat() if stem in generated else None
        )
        if captured is None:
            raise SystemExit(f"{photo.name}: no captured_at (JPG name not parseable?)")
        if photo.suffix.lower() == ".png" and "latitude" not in old:
            lat, lon = SEOUL_SPOTS[png_index % len(SEOUL_SPOTS)]
        elif photo.suffix.lower() == ".png" and stem in generated:
            lat, lon = SEOUL_SPOTS[png_index % len(SEOUL_SPOTS)]
        else:
            lat = old.get("latitude", SEOUL[0])
            lon = old.get("longitude", SEOUL[1])
        if photo.suffix.lower() == ".png":
            png_index += 1
        new = {"captured_at": captured, "latitude": lat, "longitude": lon}
        if votes.get(stem):
            new["thermal_vote"] = votes[stem]
        for key, value in old.items():  # keep unrelated extra fields (Req 2.11)
            if key not in {"captured_at", "latitude", "longitude", "thermal_vote"}:
                new[key] = value
        sidecars[stem] = new
        _write_json(sidecar_path_for(photo), new)

    entries = []
    for idx, photo in enumerate(photos):
        sc = sidecars[photo.stem]
        captured_at = datetime.fromisoformat(sc["captured_at"])
        entries.append({
            "file": photo.name,
            "captured_at": sc["captured_at"],
            "latitude": sc["latitude"],
            "longitude": sc["longitude"],
            **derived_weather(photo.stem, idx, captured_at),
        })
    _write_json(fixtures_dir / "weather.json",
                {"entries": entries, "today_default": TODAY_DEFAULT})

    vision = {}
    for photo in photos:
        digest = sha256_of(photo)
        if digest in vision:
            raise SystemExit(f"duplicate SHA-256 for {photo.name}")
        vision[digest] = vision_analysis_for(photo.stem)
    _write_json(fixtures_dir / "vision.json", vision)

    _write_json(SCENARIOS_PATH, {"scenarios": SCENARIOS})
    print(f"wrote {len(photos)} sidecars, {len(entries)} weather entries, "
          f"{len(vision)} vision entries, {len(SCENARIOS)} scenarios")


# ---------------------------------------------------------------- check

def _reference_recommend(today: float, rows: list[dict], tolerance: Decimal = Decimal("3.0")):
    """Minimal rule_based_v1 model (design §9) used only to check the demo data."""
    t = Decimal(str(today))
    scored = []
    for r in rows:
        diff = abs(Decimal(str(r["temp"])) - t).quantize(Decimal("0.1"), ROUND_HALF_UP)
        scored.append((diff, r))
    picked = [(d, r) for d, r in scored if d <= tolerance]
    fallback = False
    if not picked:
        fallback = True
        picked = [(d, r) for d, r in scored if d <= Decimal("5.0")]
    adj = {"comfortable": Decimal(15), "cold": Decimal(-15), "hot": Decimal(-15), None: Decimal(0)}
    results = [(Decimal(100) - Decimal(10) * d + adj[r["vote"]], d, r) for d, r in picked]
    results.sort(key=lambda x: (-x[0], x[2]["id"]))
    return fallback, results[:5]


def check(photo_dir: Path, fixtures_dir: Path) -> int:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.domain import SidecarMetadata, VisionAnalysis  # noqa: PLC0415

    problems: list[str] = []
    photos = list_photos(photo_dir)
    stems = [p.stem for p in photos]

    # sidecars
    sidecars = {}
    times = set()
    for photo in photos:
        path = sidecar_path_for(photo)
        if not path.exists():
            problems.append(f"missing sidecar: {path.name}")
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            meta = SidecarMetadata.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"invalid sidecar {path.name}: {exc}")
            continue
        if meta.captured_at.utcoffset() != timedelta(hours=9):
            problems.append(f"{path.name}: offset is not +09:00")
        if meta.captured_at in times:
            problems.append(f"{path.name}: duplicate captured_at")
        times.add(meta.captured_at)
        if photo.suffix.lower() == ".png" and not 7 <= meta.captured_at.hour < 20:
            problems.append(f"{path.name}: PNG time outside 07:00-20:00")
        if photo.suffix.lower() == ".jpg":
            expected = datetime.strptime(photo.stem, "%Y-%m-%d_%H%M%S_KST").replace(tzinfo=KST)
            if meta.captured_at != expected:
                problems.append(f"{path.name}: JPG time differs from file name")
        if photo.suffix.lower() == ".png":
            temp, month = TEMPERATURES[photo.stem], meta.captured_at.month
            allowed = ({12, 1, 2} if temp < 5 else {3, 4, 10, 11} if temp < 17
                       else {5, 9} if temp < 22 else {6, 7, 8})
            if month not in allowed:
                problems.append(f"{path.name}: month {month} does not fit {temp}°C")
        sidecars[photo.stem] = meta

    # vision
    vision = json.loads((fixtures_dir / "vision.json").read_text(encoding="utf-8"))
    for photo in photos:
        digest = sha256_of(photo)
        if digest not in vision:
            problems.append(f"vision.json missing {photo.name}")
            continue
        try:
            analysis = VisionAnalysis.model_validate(vision[digest])
        except Exception as exc:  # noqa: BLE001
            problems.append(f"invalid vision analysis for {photo.name}: {exc}")
            continue
        if photo.stem in NON_OUTFIT:
            if analysis.photo_type != "non_outfit" or analysis.tags.model_dump() != UNKNOWN_TAGS:
                problems.append(f"non-outfit {photo.name} has invalid analysis")
        elif analysis.photo_type != "outfit":
            problems.append(f"outfit {photo.name} is not labeled outfit")
    if len(vision) != len(photos):
        problems.append(f"vision.json has {len(vision)} entries for {len(photos)} photos")

    # weather + T1-T6
    weather = json.loads((fixtures_dir / "weather.json").read_text(encoding="utf-8"))
    by_file = {e["file"]: e for e in weather["entries"]}
    temps: dict[str, float] = {}
    for photo in photos:
        entry = by_file.get(photo.name)
        if entry is None:
            problems.append(f"weather.json missing {photo.name}")
            continue
        temps[photo.stem] = entry["actual_temperature_c"]
        if entry["actual_temperature_c"] != TEMPERATURES[photo.stem]:
            problems.append(f"{photo.name}: temperature differs from design table")
        meta = sidecars.get(photo.stem)
        if meta and (datetime.fromisoformat(entry["captured_at"]) != meta.captured_at
                     or entry["latitude"] != meta.latitude
                     or entry["longitude"] != meta.longitude):
            problems.append(f"{photo.name}: weather key differs from sidecar")
        if not 0 <= entry["relative_humidity_pct"] <= 100:
            problems.append(f"{photo.name}: humidity out of range")
        if (entry["precipitation_mm"] > 0) != (photo.stem in RAIN_MM):
            problems.append(f"{photo.name}: precipitation rule broken")
    if weather.get("today_default", {}).get("actual_temperature_c") != 9.0:
        problems.append("today_default is not 9.0°C")

    values = list(temps.values())
    if any((v * 2) != int(v * 2) or not -9.0 <= v <= 26.5 for v in values):
        problems.append("T1 broken: grid or range")
    if any(-15.0 <= v < -9.0 for v in values) or [s for s, v in temps.items() if v == -9.0] != [
            "076_winter_navy_long_parka"]:
        problems.append("T2 broken: cold boundary")
    if sorted(v for v in values if v > -9.0)[0] != -8.5:
        problems.append("T2 broken: next coldest is not -8.5")
    if any(27.0 <= v <= 33.0 for v in values) or not any(25.0 <= v < 27.0 for v in values):
        problems.append("T3 broken")
    if any(33.0 <= v <= 43.0 for v in values):
        problems.append("T4 broken")
    if sum(1 for v in values if 6.0 <= v <= 12.0) < 5:
        problems.append("T5 broken")
    if sorted(s for s, v in temps.items() if v == 9.0) != [
            "033_home_sage_sweater", "081_hiphop_varsity_baggy_denim"]:
        problems.append("T6 broken: 9.0°C photos")
    for stem, vote in FIXED_VOTES.items():
        meta = sidecars.get(stem)
        if meta is None or meta.thermal_vote != vote:
            problems.append(f"T6 broken: {stem} vote")
    for stem in NON_OUTFIT:
        meta = sidecars.get(stem)
        if meta is not None and meta.thermal_vote is not None:
            problems.append(f"non-outfit {stem} has a vote")
        if 6.0 <= TEMPERATURES[stem] <= 12.0 or TEMPERATURES[stem] >= 25.0:
            problems.append(f"non-outfit {stem} in a scenario band")
    voted = [s for s, m in sidecars.items() if m.thermal_vote and s not in FIXED_VOTES]
    outfit_pool = len([s for s in stems if s not in NON_OUTFIT and s not in FIXED_VOTES])

    # scenario outcomes
    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
    ids = [s["id"] for s in scenarios]
    if ids != ["chilly-morning", "cold-snap", "early-heat", "heatwave"]:
        problems.append(f"unexpected scenarios: {ids}")
    rows = [{"id": i, "stem": s, "temp": temps[s], "vote": sidecars[s].thermal_vote}
            for i, s in enumerate(stems, start=1) if s in temps and s in sidecars]
    outcome = {}
    for sc in scenarios:
        fallback, res = _reference_recommend(sc["actual_temperature_c"], rows)
        outcome[sc["id"]] = (fallback, res)
        print(f"  {sc['id']:15} {sc['actual_temperature_c']:6.1f}°C fallback={fallback} "
              f"items={len(res)} "
              + ", ".join(f"{r['stem'][:24]}({float(sc_):.1f}/d{float(d):.1f})"
                          for sc_, d, r in res))
    fb, res = outcome["chilly-morning"]
    if fb or len(res) != 5 or res[0][2]["stem"] != "081_hiphop_varsity_baggy_denim" \
            or res[0][0] != Decimal("115.0") or res[1][0] >= res[0][0] \
            or any(d > 3 for _, d, _ in res) or any(r["stem"] in NON_OUTFIT for *_, r in res):
        problems.append("chilly-morning outcome differs from design")
    fb, res = outcome["cold-snap"]
    if fb or len(res) != 1 or res[0][2]["stem"] != "076_winter_navy_long_parka" \
            or res[0][1] != Decimal("3.0"):
        problems.append("cold-snap outcome differs from design")
    fb, res = outcome["early-heat"]
    if not fb or len(res) != 5 or any(not Decimal("3.5") <= d <= Decimal("5.0") for _, d, _ in res) \
            or any(r["stem"] in NON_OUTFIT for *_, r in res):
        problems.append("early-heat outcome differs from design")
    pool_30 = [r for r in rows if abs(r["temp"] - 30.0) <= 5.0]
    if len(pool_30) != 11 or any(r["stem"] in NON_OUTFIT for r in pool_30):
        problems.append(f"early-heat fallback pool is {len(pool_30)}, expected 11 outfit photos")
    fb, res = outcome["heatwave"]
    if not fb or res:
        problems.append("heatwave outcome differs from design")

    print(f"photos={len(photos)} sidecars_valid={len(sidecars)} vision={len(vision)} "
          f"weather={len(by_file)} extra_votes={len(voted)}/{outfit_pool}")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("all checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate instead of build")
    parser.add_argument("--photos", type=Path, default=DEFAULT_PHOTOS)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    args = parser.parse_args(argv)
    if args.check:
        return check(args.photos, args.fixtures)
    build(args.photos, args.fixtures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
