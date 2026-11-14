"""Unit tests for the Vision interface, FixtureVisionProvider and factory (task 4.1).

Configs are built with ``resolve_config`` from explicit mappings, never from
the real ``.env``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import resolve_config
from app.domain import (
    LOWER_TYPES,
    OUTER_TYPES,
    UPPER_TYPES,
    UNKNOWN_ANALYSIS,
    ClothingTags,
    VisionAnalysis,
)
from app.providers.vision.base import VisionError, VisionProvider
from app.providers.vision.factory import build_vision_provider
from app.providers.vision.fixture import (
    DEFAULT_VISION_FIXTURE_PATH,
    REPO_ROOT,
    FixtureVisionProvider,
    load_vision_fixture,
)

DEMO_PHOTO = REPO_ROOT / "data" / "demo" / "photos" / "081_hiphop_varsity_baggy_denim.png"

TAGS = ClothingTags(
    upper_type="knit",
    outer_type="coat",
    lower_type="slacks",
    colors=["navy", "grey"],
    layers_visible=2,
    confidence=0.8,
)
ANALYSIS = VisionAnalysis(
    photo_type="outfit",
    photo_type_confidence=0.95,
    reason="A full outfit is visible.",
    tags=TAGS,
)


def _write_fixture(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "vision.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_vision_error_kinds_and_codes() -> None:
    assert VisionError("schema_invalid").code == "VISION_SCHEMA_INVALID"
    assert VisionError("api_error").code == "VISION_API_ERROR"
    assert VisionError("timeout").code == "VISION_TIMEOUT"
    with pytest.raises(ValueError):
        VisionError("other")  # type: ignore[arg-type]


def test_fixture_returns_registered_tags_and_unknown_default(tmp_path: Path) -> None:
    known = b"known image bytes"
    sha = hashlib.sha256(known).hexdigest()
    provider = FixtureVisionProvider.from_file(_write_fixture(tmp_path, {sha: ANALYSIS.model_dump()}))

    assert isinstance(provider, VisionProvider)
    assert provider.name == "fixture"
    assert provider.analyze(known, "image/png", sha) == ANALYSIS
    assert provider.analyze(known, "image/png") == ANALYSIS  # hash computed from bytes
    assert provider.analyze(b"other", "image/png") == UNKNOWN_ANALYSIS


def test_fixture_result_is_a_copy(tmp_path: Path) -> None:
    sha = hashlib.sha256(b"x").hexdigest()
    provider = FixtureVisionProvider({sha: ANALYSIS})
    provider.analyze(b"x", "image/png", sha).tags.colors.append("red")
    provider.analyze(b"y", "image/png").tags.colors.append("red")
    assert provider.analyze(b"x", "image/png", sha) == ANALYSIS
    assert UNKNOWN_ANALYSIS.tags.colors == []


def test_load_skips_invalid_entries(tmp_path: Path) -> None:
    good = "a" * 64
    path = _write_fixture(
        tmp_path,
        {
            good.upper(): ANALYSIS.model_dump(),
            "not-a-hash": ANALYSIS.model_dump(),
            "b" * 64: {
                **ANALYSIS.model_dump(),
                "tags": {**TAGS.model_dump(), "layers_visible": 9},
            },
        },
    )
    assert load_vision_fixture(path) == {good: ANALYSIS}


def test_real_fixture_uses_only_canonical_categories() -> None:
    fixture = load_vision_fixture(DEFAULT_VISION_FIXTURE_PATH)
    assert len(fixture) == 174
    for analysis in fixture.values():
        tags = analysis.tags
        assert tags.upper_type in UPPER_TYPES
        assert tags.outer_type in OUTER_TYPES
        assert tags.lower_type in LOWER_TYPES
        assert " " not in tags.upper_type + tags.outer_type + tags.lower_type
        assert "-" not in tags.upper_type + tags.outer_type + tags.lower_type


@pytest.mark.parametrize("content", [None, "{broken", "[]"])
def test_load_missing_or_broken_file_gives_empty_map(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "vision.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert load_vision_fixture(path) == {}
    assert FixtureVisionProvider.from_file(path).analyze(b"img", "image/png") == UNKNOWN_ANALYSIS


def test_real_demo_fixture_matches_demo_photo() -> None:
    if not DEMO_PHOTO.exists():
        pytest.skip("demo photos not prepared")
    provider = FixtureVisionProvider.from_file(DEFAULT_VISION_FIXTURE_PATH)
    assert len(provider) == 174

    data = DEMO_PHOTO.read_bytes()
    analysis = provider.analyze(data, "image/png", hashlib.sha256(data).hexdigest())
    assert analysis.photo_type == "outfit"
    assert analysis.tags == ClothingTags(
        upper_type="t_shirt",
        outer_type="varsity_jacket",
        lower_type="jeans",
        colors=["burgundy", "cream", "white", "black"],
        layers_visible=2,
        confidence=0.9,
    )


def test_factory_default_config_builds_fixture(tmp_path: Path) -> None:
    sha = "c" * 64
    cfg, _ = resolve_config({})
    provider = build_vision_provider(cfg, fixture_path=_write_fixture(tmp_path, {sha: ANALYSIS.model_dump()}))
    assert isinstance(provider, FixtureVisionProvider)
    assert provider.analyze(b"", "image/png", sha) == ANALYSIS


def test_factory_gemini_without_key_falls_back_to_fixture(tmp_path: Path) -> None:
    cfg, _ = resolve_config({})
    cfg = replace(cfg, vision_provider="gemini")  # hand-built config bypassing resolve_config
    provider = build_vision_provider(cfg, fixture_path=_write_fixture(tmp_path, {}))
    assert isinstance(provider, FixtureVisionProvider)
