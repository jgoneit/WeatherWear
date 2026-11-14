"""Unit tests for the sidecar parser (Req 2.1, 2.3, 2.4, 2.5, 2.8, 2.9, 2.11)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.domain import SidecarMetadata
from app.ingestion.sidecar import (
    SidecarError,
    parse_sidecar_text,
    read_sidecar,
    sidecar_path_for,
)

VALID = {
    "captured_at": "2025-11-20T08:30:00+09:00",
    "latitude": 37.5665,
    "longitude": 126.9780,
    "thermal_vote": "comfortable",
}


def parse(obj: object) -> SidecarMetadata | SidecarError:
    return parse_sidecar_text(json.dumps(obj))


def with_(**changes: object) -> dict[str, object]:
    return {**VALID, **changes}


def without(name: str) -> dict[str, object]:
    return {k: v for k, v in VALID.items() if k != name}


def assert_error(result: object, code: str, field: str | None = None) -> None:
    assert isinstance(result, SidecarError), result
    assert result.code == code
    assert result.field == field
    assert result.message


def test_sidecar_path_for_replaces_extension() -> None:
    assert sidecar_path_for(Path("photos/001_front.png")) == Path("photos/001_front.metadata.json")
    assert sidecar_path_for(Path("/x/2025-01-08_081500_KST.jpg")) == Path(
        "/x/2025-01-08_081500_KST.metadata.json"
    )
    assert sidecar_path_for(Path("a.b.JPEG")) == Path("a.b.metadata.json")


def test_valid_sidecar_keeps_offset_and_values() -> None:
    result = parse(VALID)
    assert isinstance(result, SidecarMetadata)
    assert result.captured_at.utcoffset() == timedelta(hours=9)
    assert result.captured_at.isoformat() == "2025-11-20T08:30:00+09:00"
    assert result.latitude == 37.5665
    assert result.longitude == 126.9780
    assert result.thermal_vote == "comfortable"


def test_thermal_vote_optional_and_null_is_absent() -> None:
    assert parse(without("thermal_vote")).thermal_vote is None  # type: ignore[union-attr]
    assert parse(with_(thermal_vote=None)).thermal_vote is None  # type: ignore[union-attr]


def test_extra_fields_ignored() -> None:
    result = parse(with_(note="카페 출근길", extra={"nested": [1, 2]}))
    assert result == parse(VALID)


def test_integer_and_boundary_coordinates_accepted() -> None:
    result = parse(with_(latitude=-90, longitude=180))
    assert isinstance(result, SidecarMetadata)
    assert (result.latitude, result.longitude) == (-90.0, 180.0)


def test_utc_z_suffix_accepted() -> None:
    result = parse(with_(captured_at="2025-01-15T00:00:00Z"))
    assert isinstance(result, SidecarMetadata)
    assert result.captured_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("text", ["", "{", "not json", "{'a': 1}"])
def test_invalid_json_is_parse_error(text: str) -> None:
    assert_error(parse_sidecar_text(text), "SIDECAR_PARSE_ERROR")


@pytest.mark.parametrize("obj", [[], [VALID], "text", 1, None])
def test_non_object_top_level_is_parse_error(obj: object) -> None:
    assert_error(parse(obj), "SIDECAR_PARSE_ERROR")


@pytest.mark.parametrize("name", ["captured_at", "latitude", "longitude"])
def test_missing_or_null_required_field(name: str) -> None:
    assert_error(parse(without(name)), "SIDECAR_MISSING_FIELD", name)
    assert_error(parse(with_(**{name: None})), "SIDECAR_MISSING_FIELD", name)


@pytest.mark.parametrize(
    "value",
    ["2025-11-20T08:30:00", "2025-11-20", "yesterday", "", "2025-13-01T00:00:00+09:00", 1700000000, True, []],
)
def test_invalid_captured_at(value: object) -> None:
    assert_error(parse(with_(captured_at=value)), "SIDECAR_CAPTURED_AT_INVALID", "captured_at")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("latitude", 90.0001),
        ("latitude", -91),
        ("latitude", "37.5"),
        ("latitude", True),
        ("latitude", [37.5]),
        ("longitude", 180.5),
        ("longitude", -1e400),  # json.dumps writes -Infinity
        ("longitude", "126.9"),
        ("longitude", False),
        ("longitude", 10**400),
    ],
)
def test_out_of_range_or_non_numeric_coordinates(name: str, value: object) -> None:
    assert_error(parse(with_(**{name: value})), "SIDECAR_OUT_OF_RANGE", name)


def test_nan_coordinate_rejected() -> None:
    text = json.dumps(VALID).replace("37.5665", "NaN")
    assert_error(parse_sidecar_text(text), "SIDECAR_OUT_OF_RANGE", "latitude")


@pytest.mark.parametrize("vote", ["COLD", " cold", "warm", "", 1, True, ["hot"]])
def test_invalid_thermal_vote(vote: object) -> None:
    assert_error(parse(with_(thermal_vote=vote)), "SIDECAR_THERMAL_VOTE_INVALID", "thermal_vote")


def test_vote_only_sidecar_allowed_without_location_requirement() -> None:
    result = parse_sidecar_text(
        json.dumps({"thermal_vote": "hot", "note": "x"}), require_location=False
    )
    assert result == SidecarMetadata(thermal_vote="hot")
    assert_error(
        parse_sidecar_text(json.dumps({"thermal_vote": "hot"})),
        "SIDECAR_MISSING_FIELD",
        "captured_at",
    )


def test_present_location_still_validated_without_requirement() -> None:
    text = json.dumps({"thermal_vote": "cold", "latitude": 200})
    assert_error(parse_sidecar_text(text, require_location=False), "SIDECAR_OUT_OF_RANGE", "latitude")


def test_read_sidecar_missing_is_none_and_ok(tmp_path: Path) -> None:
    image = tmp_path / "001_front.png"
    assert read_sidecar(image) is None

    (tmp_path / "001_front.metadata.json").write_text(json.dumps(VALID), encoding="utf-8-sig")
    assert isinstance(read_sidecar(image), SidecarMetadata)


def test_read_sidecar_directory_is_none(tmp_path: Path) -> None:
    (tmp_path / "x.metadata.json").mkdir()
    assert read_sidecar(tmp_path / "x.png") is None


def test_read_sidecar_non_utf8_is_parse_error(tmp_path: Path) -> None:
    (tmp_path / "x.metadata.json").write_bytes(b"\xff\xfe\x00bad")
    assert_error(read_sidecar(tmp_path / "x.png"), "SIDECAR_PARSE_ERROR")
