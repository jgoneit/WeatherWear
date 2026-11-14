"""Sidecar metadata parsing (design.md §3, Req 2.1, 2.3~2.5, 2.8, 2.9, 2.11).

``parse_sidecar_text`` is a pure function: it takes the sidecar file text and
returns either a validated ``SidecarMetadata`` or a ``SidecarError``. It never
raises for bad input. ``read_sidecar`` is the thin I/O wrapper that adds the
"file not found" case.

Sidecar format (``<image stem>.metadata.json``)::

    {
      "captured_at": "2025-11-20T08:30:00+09:00",   # ISO 8601, offset required
      "latitude": 37.5665,                          # number in [-90, 90]
      "longitude": 126.9780,                        # number in [-180, 180]
      "thermal_vote": "comfortable",                # optional: cold | comfortable | hot
      "note": "..."                                 # any other key is ignored
    }
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.domain import SidecarMetadata

SIDECAR_SUFFIX = ".metadata.json"

SidecarErrorCode = Literal[
    "SIDECAR_NOT_FOUND",
    "SIDECAR_PARSE_ERROR",
    "SIDECAR_MISSING_FIELD",
    "SIDECAR_CAPTURED_AT_INVALID",
    "SIDECAR_OUT_OF_RANGE",
    "SIDECAR_THERMAL_VOTE_INVALID",
]

REQUIRED_FIELDS: tuple[str, ...] = ("captured_at", "latitude", "longitude")
COORDINATE_RANGES: dict[str, tuple[int, int]] = {
    "latitude": (-90, 90),
    "longitude": (-180, 180),
}
THERMAL_VOTES: frozenset[str] = frozenset({"cold", "comfortable", "hot"})


@dataclass(frozen=True)
class SidecarError:
    """Why a sidecar was rejected. ``field`` is set for field-specific codes."""

    code: SidecarErrorCode
    message: str  # Korean, never contains the input value or a server path
    field: str | None = None


def sidecar_path_for(image: Path) -> Path:
    """``001_front.png`` → ``001_front.metadata.json`` in the same folder (Req 2.1)."""
    return image.with_name(image.stem + SIDECAR_SUFFIX)


def _is_number(value: object) -> bool:
    # bool is a subclass of int in Python; JSON true/false must be rejected.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_captured_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_coordinate(value: object, low: int, high: int) -> float | None:
    if not _is_number(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    # Compare before float() so huge JSON integers cannot overflow.
    if not (low <= value <= high):  # type: ignore[operator]
        return None
    return float(value)  # type: ignore[arg-type]


def parse_sidecar_text(text: str | bytes) -> SidecarMetadata | SidecarError:
    """Parse and validate sidecar JSON text. Pure; never raises for bad input."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return SidecarError("SIDECAR_PARSE_ERROR", "사이드카 JSON을 해석할 수 없습니다.")
    if not isinstance(data, dict):
        return SidecarError("SIDECAR_PARSE_ERROR", "사이드카 최상위 값은 JSON 객체여야 합니다.")

    # Req 2.3: missing or null required field.
    for name in REQUIRED_FIELDS:
        if data.get(name) is None:
            return SidecarError(
                "SIDECAR_MISSING_FIELD", f"사이드카에 필수 필드 {name}이(가) 없습니다.", name
            )

    # Req 2.4: ISO 8601 with an explicit offset.
    captured_at = _parse_captured_at(data["captured_at"])
    if captured_at is None:
        return SidecarError(
            "SIDECAR_CAPTURED_AT_INVALID",
            "captured_at은 타임존 오프셋이 있는 ISO 8601 시각이어야 합니다.",
            "captured_at",
        )

    # Req 2.5: numbers only (no strings or booleans), within range.
    coords: dict[str, float] = {}
    for name, (low, high) in COORDINATE_RANGES.items():
        value = _parse_coordinate(data[name], low, high)
        if value is None:
            return SidecarError(
                "SIDECAR_OUT_OF_RANGE",
                f"{name}은(는) {low} 이상 {high} 이하의 숫자여야 합니다.",
                name,
            )
        coords[name] = value

    # Req 2.9: optional thermal_vote; JSON null is treated as absent.
    vote = data.get("thermal_vote")
    if vote is not None and (not isinstance(vote, str) or vote not in THERMAL_VOTES):
        return SidecarError(
            "SIDECAR_THERMAL_VOTE_INVALID",
            "thermal_vote는 cold, comfortable, hot 중 하나여야 합니다.",
            "thermal_vote",
        )

    # Req 2.11: every other key is ignored.
    return SidecarMetadata(
        captured_at=captured_at,
        latitude=coords["latitude"],
        longitude=coords["longitude"],
        thermal_vote=vote,
    )


def read_sidecar(image: Path) -> SidecarMetadata | SidecarError:
    """Read the sidecar next to ``image``. Missing or non-file → SIDECAR_NOT_FOUND (Req 2.3)."""
    path = sidecar_path_for(image)
    if not path.is_file():
        return SidecarError("SIDECAR_NOT_FOUND", "사이드카 메타데이터 파일이 없습니다.")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return SidecarError("SIDECAR_NOT_FOUND", "사이드카 메타데이터 파일이 없습니다.")
    except OSError:
        return SidecarError("SIDECAR_PARSE_ERROR", "사이드카 파일을 읽을 수 없습니다.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return SidecarError("SIDECAR_PARSE_ERROR", "사이드카 파일이 UTF-8 텍스트가 아닙니다.")
    return parse_sidecar_text(text)
