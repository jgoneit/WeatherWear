"""FixtureVisionProvider (design.md §4, Req 3.3, 15.8).

Loads ``data/demo/fixtures/vision.json`` (``{sha256: VisionAnalysis}``) once
at construction and answers by hash lookup. No network access. Unknown hashes
get ``UNKNOWN_ANALYSIS`` so they are reported as uncertain instead of being
stored as outfits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.domain import UNKNOWN_ANALYSIS, VisionAnalysis

logger = logging.getLogger(__name__)

# backend/app/providers/vision/fixture.py → parents[4] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_VISION_FIXTURE_PATH = REPO_ROOT / "data" / "demo" / "fixtures" / "vision.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def load_vision_fixture(path: Path) -> dict[str, VisionAnalysis]:
    """Read and validate a SHA-256 to ``VisionAnalysis`` fixture map.

    A missing or unparsable file gives an empty map (every photo then gets
    ``UNKNOWN_ANALYSIS``). Entries whose key is not a SHA-256 hex digest or
    whose value fails ``VisionAnalysis`` validation are skipped. Problems are
    logged with the file name only.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Vision fixture 파일이 없습니다: %s. 모든 사진에 unknown 태그를 사용합니다.", path.name)
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.error("Vision fixture 파일을 읽지 못했습니다: %s. 모든 사진에 unknown 태그를 사용합니다.", path.name)
        return {}
    if not isinstance(payload, dict):
        logger.error("Vision fixture 최상위는 객체여야 합니다: %s", path.name)
        return {}

    analysis_by_hash: dict[str, VisionAnalysis] = {}
    skipped = 0
    for key, value in payload.items():
        sha = str(key).strip().lower()
        if not _SHA256_RE.match(sha):
            skipped += 1
            continue
        try:
            analysis_by_hash[sha] = VisionAnalysis.model_validate(value)
        except ValidationError:
            skipped += 1
    if skipped:
        logger.warning("Vision fixture에서 잘못된 항목 %d건을 건너뛰었습니다: %s", skipped, path.name)
    return analysis_by_hash


class FixtureVisionProvider:
    """Deterministic Vision_Provider backed by a hash → analysis map."""

    name: Literal["fixture"] = "fixture"

    def __init__(self, analysis_by_hash: Mapping[str, VisionAnalysis]) -> None:
        self._analyses = {k.lower(): v.model_copy(deep=True) for k, v in analysis_by_hash.items()}

    @classmethod
    def from_file(cls, path: Path = DEFAULT_VISION_FIXTURE_PATH) -> FixtureVisionProvider:
        return cls(load_vision_fixture(path))

    def __len__(self) -> int:
        return len(self._analyses)

    def analyze(self, image: bytes, mime_type: str, sha256: str | None = None) -> VisionAnalysis:
        """Return fixture analysis for the image hash, else ``UNKNOWN_ANALYSIS``."""
        key = (sha256 or hashlib.sha256(image).hexdigest()).lower()
        analysis = self._analyses.get(key, UNKNOWN_ANALYSIS)
        return analysis.model_copy(deep=True)
