"""Demo weather scenario selection, checks and seed votes (design.md "시나리오 규칙과
선택 알고리즘", "시드 투표 규칙", Req 2.6, 15.9, 15.10, 15.11).

Pure functions only: no file or network access. ``scripts/select_scenarios.py``
reads the recorded data, calls these functions and writes the demo files.

Input ``T`` is the list of recorded temperatures of the photos a scan stores as
Outfits: fixture ``photo_type`` ``outfit`` after the ingestion confidence gate
(``apply_confidence_gate``). ``non_outfit`` and ``uncertain`` photos are never
stored, so they are not in ``T``. ``d(x, t) = round_half_up(|x - t|, 1)`` is the same
Decimal calculation the recommendation strategy uses (Req 6.7), and
``N(t, r) = #{x in T : d(x, t) <= r}``. The rules assume the default
``TEMPERATURE_TOLERANCE_C = 3.0`` and temperatures in 0.1°C steps.

Scenario rules:

- S1 ``chilly-morning``: ``N(t, 3.0) >= 5`` (5 normal results).
- S2 ``cold-snap``: exactly one x with ``d(x, t) = 3.0`` and ``N(t, 3.0) = 1``.
- S3 ``early-heat``: ``N(t, 3.0) = 0`` and ``N(t, 5.0) >= 1`` (fallback).
- S4 ``heatwave``: ``N(t, 5.0) = 0`` (insufficient data).

Seed vote rules (earlier rule wins when a photo matches two):

- V1: a photo the scan does not store as an Outfit (Non_Outfit_Photo or
  uncertain) gets no vote; the scan stops before it would read the sidecar.
- V2: outfit photos within 3.0°C of S1, sorted by ``(diff, file)``: the 1st
  gets ``cold``, the 2nd gets ``comfortable``.
- V3: the S2 boundary photo (diff 3.0) gets ``comfortable``.
- V4: every other outfit photo gets a vote only when
  ``int(sha256[0:8], 16) % 3 == 0``; the value is
  ``("comfortable", "cold", "hot")[int(sha256[8:16], 16) % 3]``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.domain import ClothingTags, ThermalVote, VisionAnalysis
from app.ingestion.service import apply_confidence_gate
from app.recommendation.rule_based_v1 import score_for, temperature_diff, to_decimal

DEFAULT_TOLERANCE_C = Decimal("3.0")
FALLBACK_TOLERANCE_C = Decimal("5.0")
MAX_RESULTS = 5

S1_ID = "chilly-morning"
S2_ID = "cold-snap"
S3_ID = "early-heat"
S4_ID = "heatwave"
SCENARIO_IDS: tuple[str, ...] = (S1_ID, S2_ID, S3_ID, S4_ID)

# S1 candidates: 5.0 ~ 15.0 in 0.5°C steps; ties prefer 9.0, then the lower value.
S1_CANDIDATES: tuple[Decimal, ...] = tuple(Decimal("5.0") + Decimal("0.5") * i for i in range(21))
S1_PREFERRED_C = Decimal("9.0")
S1_MIN_COUNT = MAX_RESULTS
S2_OFFSET_C = Decimal("3.0")
S3_OFFSET_C = Decimal("4.0")
S4_OFFSET_C = Decimal("8.0")
ONE_DECIMAL = Decimal("0.1")

V4_VALUES: tuple[ThermalVote, ...] = ("comfortable", "cold", "hot")

Side = Literal["cold", "hot"]

# Fixed parts of data/demo/scenarios.json; only actual_temperature_c is chosen.
# apparent_temperature_c is null because the recorded weather has no apparent
# temperature, so tie-breaking stays consistent with the data (Req 7.8).
SCENARIO_NAMES: dict[str, str] = {
    S1_ID: "쌀쌀한 아침",
    S2_ID: "기록적 한파 (경계 3.0°C)",
    S3_ID: "무더위 (범위 확장)",
    S4_ID: "기록적 폭염 (데이터 부족)",
}
S2_HOT_SIDE_NAME = "기록적 고온 (경계 3.0°C)"
SCENARIO_AUX: dict[str, dict[str, float | None]] = {
    S1_ID: {"apparent_temperature_c": None, "relative_humidity_pct": 55, "wind_speed_kmh": 12, "precipitation_mm": 0},
    S2_ID: {"apparent_temperature_c": None, "relative_humidity_pct": 35, "wind_speed_kmh": 20, "precipitation_mm": 0},
    S3_ID: {"apparent_temperature_c": None, "relative_humidity_pct": 65, "wind_speed_kmh": 6, "precipitation_mm": 0},
    S4_ID: {"apparent_temperature_c": None, "relative_humidity_pct": 70, "wind_speed_kmh": 5, "precipitation_mm": 0},
}

Number = Decimal | float | int | str


@dataclass(frozen=True)
class ScenarioTemps:
    """Chosen ``actual_temperature_c`` of each scenario (one decimal place)."""

    chilly_morning: Decimal
    cold_snap: Decimal
    early_heat: Decimal
    heatwave: Decimal
    cold_snap_side: Side  # "cold": below the minimum, "hot": above the maximum

    def as_mapping(self) -> dict[str, Decimal]:
        return {
            S1_ID: self.chilly_morning,
            S2_ID: self.cold_snap,
            S3_ID: self.early_heat,
            S4_ID: self.heatwave,
        }


@dataclass(frozen=True)
class Infeasible:
    """``choose_scenarios`` could not satisfy a rule with the given data."""

    rule: str  # "S1" or "S2"
    reason: str


@dataclass(frozen=True)
class Violation:
    """A rule that does not hold. ``scenario_id`` is None for vote-only rules (V1)."""

    rule: str
    scenario_id: str | None
    detail: str = ""


@dataclass(frozen=True)
class OutfitPhoto:
    """One demo photo as seen by the seed vote rules."""

    file: str
    sha256: str
    temperature_c: Number
    analysis: VisionAnalysis  # the fixture entry for ``sha256``

    @property
    def tags(self) -> ClothingTags:
        return self.analysis.tags

    @property
    def stored_as_outfit(self) -> bool:
        """True when a scan stores the photo as an Outfit (same gate as ingestion)."""
        return apply_confidence_gate(self.analysis).photo_type == "outfit"


@dataclass(frozen=True)
class SeedVote:
    vote: ThermalVote
    rule: Literal["V2", "V3", "V4"]


# -- helpers ---------------------------------------------------------------------


def _q(value: Number) -> Decimal:
    return to_decimal(value).quantize(ONE_DECIMAL)


def diff(x: Number, t: Number) -> Decimal:
    """``d(x, t) = round_half_up(|x - t|, 1)`` (Req 6.7)."""
    return temperature_diff(to_decimal(x), to_decimal(t))  # type: ignore[arg-type]


def count_within(temps: Iterable[Number], t: Number, radius: Number) -> int:
    """``N(t, r)``."""
    r = to_decimal(radius)
    return sum(1 for x in temps if diff(x, t) <= r)


def outfit_temperatures(photos: Iterable[OutfitPhoto]) -> list[Decimal]:
    """``T``: recorded temperatures of the photos stored as Outfits only."""
    return [to_decimal(p.temperature_c) for p in photos if p.stored_as_outfit]


def _as_mapping(scenarios: ScenarioTemps | Mapping[str, Number]) -> dict[str, Decimal]:
    if isinstance(scenarios, ScenarioTemps):
        return scenarios.as_mapping()
    return {k: to_decimal(v) for k, v in scenarios.items()}


# -- selection -------------------------------------------------------------------


def choose_scenarios(temps: Sequence[Number]) -> ScenarioTemps | Infeasible:
    """Pick the four scenario temperatures from ``T`` (design "선택 방법")."""
    values = [to_decimal(x) for x in temps]
    if not values:
        return Infeasible("S1", "no outfit temperatures")

    # S1: the candidate with the most temperatures within 3.0°C.
    def s1_key(t: Decimal) -> tuple[int, Decimal, Decimal]:
        return (-count_within(values, t, DEFAULT_TOLERANCE_C), abs(t - S1_PREFERRED_C), t)

    s1 = min(S1_CANDIDATES, key=s1_key)
    best = count_within(values, s1, DEFAULT_TOLERANCE_C)
    if best < S1_MIN_COUNT:
        return Infeasible("S1", f"at most {best} temperatures within 3.0°C of any S1 candidate")

    low, high = min(values), max(values)
    side: Side
    if values.count(low) == 1:
        s2, side = low - S2_OFFSET_C, "cold"
    elif values.count(high) == 1:
        s2, side = high + S2_OFFSET_C, "hot"
    else:
        return Infeasible("S2", "both the minimum and the maximum temperature occur more than once")

    return ScenarioTemps(
        chilly_morning=_q(s1),
        cold_snap=_q(s2),
        early_heat=_q(high + S3_OFFSET_C),
        heatwave=_q(high + S4_OFFSET_C),
        cold_snap_side=side,
    )


def scenario_name(scenario_id: str, side: Side = "cold") -> str:
    """Display name; S2 is renamed when it sits on the hot side of the data."""
    if scenario_id == S2_ID and side == "hot":
        return S2_HOT_SIDE_NAME
    return SCENARIO_NAMES[scenario_id]


def build_scenarios_document(temps: ScenarioTemps) -> dict:
    """Content of ``data/demo/scenarios.json`` (ids, names and aux values fixed)."""
    chosen = temps.as_mapping()
    return {
        "scenarios": [
            {
                "id": sid,
                "name": scenario_name(sid, temps.cold_snap_side),
                "actual_temperature_c": float(chosen[sid]),
                **SCENARIO_AUX[sid],
            }
            for sid in SCENARIO_IDS
        ]
    }


# -- seed votes ------------------------------------------------------------------


def v2_photos(photos: Sequence[OutfitPhoto], s1: Number) -> list[OutfitPhoto]:
    """Outfit photos within 3.0°C of S1, sorted by ``(diff, file)``."""
    within = [p for p in photos if p.stored_as_outfit and diff(p.temperature_c, s1) <= DEFAULT_TOLERANCE_C]
    return sorted(within, key=lambda p: (diff(p.temperature_c, s1), p.file))


def v3_photos(photos: Sequence[OutfitPhoto], s2: Number) -> list[OutfitPhoto]:
    """Outfit photos exactly 3.0°C from S2 (one photo when S2 holds)."""
    return sorted(
        (p for p in photos if p.stored_as_outfit and diff(p.temperature_c, s2) == DEFAULT_TOLERANCE_C),
        key=lambda p: p.file,
    )


def v4_vote(sha256: str) -> ThermalVote | None:
    """About one in three photos get a vote, chosen from the file hash."""
    if int(sha256[0:8], 16) % 3 != 0:
        return None
    return V4_VALUES[int(sha256[8:16], 16) % 3]


def assign_seed_votes(
    photos: Sequence[OutfitPhoto], scenarios: ScenarioTemps | Mapping[str, Number]
) -> dict[str, SeedVote]:
    """Seed votes by file name (V1~V4). Photos without a vote are absent."""
    chosen = _as_mapping(scenarios)
    votes: dict[str, SeedVote] = {}

    ranked = v2_photos(photos, chosen[S1_ID])
    for photo, vote in zip(ranked[:2], ("cold", "comfortable"), strict=False):
        votes[photo.file] = SeedVote(vote, "V2")  # type: ignore[arg-type]

    for photo in v3_photos(photos, chosen[S2_ID]):
        votes.setdefault(photo.file, SeedVote("comfortable", "V3"))

    for photo in sorted(photos, key=lambda p: p.file):
        if not photo.stored_as_outfit or photo.file in votes:  # V1, earlier rules
            continue
        vote = v4_vote(photo.sha256)
        if vote is not None:
            votes[photo.file] = SeedVote(vote, "V4")
    return dict(sorted(votes.items()))


# -- checks ----------------------------------------------------------------------


def check_scenarios(
    scenarios: ScenarioTemps | Mapping[str, Number],
    temps: Sequence[Number],
    *,
    photos: Sequence[OutfitPhoto] | None = None,
    votes: Mapping[str, ThermalVote | SeedVote] | None = None,
) -> list[Violation]:
    """Re-check S1~S4 against ``T``. With ``photos`` and ``votes`` also check V1~V3.

    The vote checks: no photo that is not stored as an Outfit has a vote (V1); the two V2 photos
    hold ``cold``/``comfortable`` and the ``comfortable`` one scores higher
    (V2); the S2 boundary photo has a vote, so its confidence is ``high`` (V3).
    """
    chosen = _as_mapping(scenarios)
    values = [to_decimal(x) for x in temps]
    violations: list[Violation] = []

    missing = [sid for sid in SCENARIO_IDS if sid not in chosen]
    violations.extend(Violation("MISSING", sid, "scenario is missing") for sid in missing)

    def n(sid: str, radius: Decimal) -> int:
        return count_within(values, chosen[sid], radius)

    if S1_ID in chosen and n(S1_ID, DEFAULT_TOLERANCE_C) < S1_MIN_COUNT:
        violations.append(Violation("S1", S1_ID, f"N(t, 3.0) = {n(S1_ID, DEFAULT_TOLERANCE_C)} < 5"))
    if S2_ID in chosen:
        boundary = sum(1 for x in values if diff(x, chosen[S2_ID]) == DEFAULT_TOLERANCE_C)
        within = n(S2_ID, DEFAULT_TOLERANCE_C)
        if boundary != 1 or within != 1:
            violations.append(
                Violation("S2", S2_ID, f"{boundary} at exactly 3.0, N(t, 3.0) = {within}")
            )
    if S3_ID in chosen:
        if n(S3_ID, DEFAULT_TOLERANCE_C) != 0 or n(S3_ID, FALLBACK_TOLERANCE_C) < 1:
            violations.append(
                Violation(
                    "S3",
                    S3_ID,
                    f"N(t, 3.0) = {n(S3_ID, DEFAULT_TOLERANCE_C)}, N(t, 5.0) = {n(S3_ID, FALLBACK_TOLERANCE_C)}",
                )
            )
    if S4_ID in chosen and n(S4_ID, FALLBACK_TOLERANCE_C) != 0:
        violations.append(Violation("S4", S4_ID, f"N(t, 5.0) = {n(S4_ID, FALLBACK_TOLERANCE_C)}"))

    if photos is None or votes is None:
        return violations

    def vote_of(file: str) -> ThermalVote | None:
        value = votes.get(file)
        return value.vote if isinstance(value, SeedVote) else value

    for photo in photos:
        if not photo.stored_as_outfit and vote_of(photo.file) is not None:
            violations.append(Violation("V1", None, f"{photo.file} is not stored as an outfit but has a vote"))

    if S1_ID in chosen:
        ranked = v2_photos(photos, chosen[S1_ID])
        if len(ranked) < 2:
            violations.append(Violation("V2", S1_ID, "fewer than two outfit photos within 3.0°C"))
        else:
            first, second = ranked[0], ranked[1]
            if vote_of(first.file) != "cold" or vote_of(second.file) != "comfortable":
                violations.append(Violation("V2", S1_ID, "V2 photos do not hold cold/comfortable"))
            else:
                cold_score = score_for(diff(first.temperature_c, chosen[S1_ID]), "cold")
                comfy_score = score_for(diff(second.temperature_c, chosen[S1_ID]), "comfortable")
                if comfy_score <= cold_score:
                    violations.append(
                        Violation("V2", S1_ID, f"comfortable {comfy_score} is not above cold {cold_score}")
                    )

    if S2_ID in chosen:
        for photo in v3_photos(photos, chosen[S2_ID]):
            if vote_of(photo.file) is None:
                violations.append(Violation("V3", S2_ID, f"{photo.file} has no vote"))
    return violations
