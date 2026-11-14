"""FixtureVisionProvider (design.md §4, Req 3.3, 15.8; Phase 2 design.md §8, Req 6.3, 6.4).

Loads ``data/demo/fixtures/vision.json`` (``{sha256: VisionAnalysis}``) once
at construction and answers by hash lookup. No network access. Unknown hashes
get ``UNKNOWN_ANALYSIS`` so they are reported as uncertain instead of being
stored as outfits.

Phase 2: an optional Vision_Fixture_Overlay (``data/user/fixtures/
vision.overlay.json``, same format) is looked up *before* the demo fixture.
The capture simulator writes the overlay entry before the photo lands in
Gallery_Dir, so a watched photo can be stored in fixture mode. The overlay is
re-read whenever its size or mtime_ns changes, so no restart is needed. A
missing overlay is silent; an unreadable one is ignored with one WARNING per
file signature. Overlay keys are hashes of new photos, so demo scan results do
not change (Req 4.6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
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


_OverlaySignature = tuple[int, int]  # (size, mtime_ns)


class _Overlay:
    """The overlay file, re-read only when its (size, mtime_ns) changes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._signature: _OverlaySignature | None = None
        self._analyses: dict[str, VisionAnalysis] = {}
        self._warned: set[_OverlaySignature] = set()

    def lookup(self, sha256: str) -> VisionAnalysis | None:
        with self._lock:
            self._refresh()
            return self._analyses.get(sha256)

    def _refresh(self) -> None:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            self._signature, self._analyses = None, {}
            return
        except OSError:
            self._signature, self._analyses = None, {}
            self._warn_once((-1, -1))
            return
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature == self._signature:
            return
        self._signature = signature
        self._analyses = self._read(signature)

    def _read(self, signature: _OverlaySignature) -> dict[str, VisionAnalysis]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._warn_once(signature)
            return {}
        if not isinstance(payload, dict):
            self._warn_once(signature)
            return {}
        analyses: dict[str, VisionAnalysis] = {}
        for key, value in payload.items():
            sha = str(key).strip().lower()
            if not _SHA256_RE.match(sha):
                continue
            try:
                analyses[sha] = VisionAnalysis.model_validate(value)
            except ValidationError:
                continue
        return analyses

    def _warn_once(self, signature: _OverlaySignature) -> None:
        if signature in self._warned:
            return
        self._warned.add(signature)
        logger.warning(
            "Vision fixture overlay를 읽지 못해 데모 fixture만 사용합니다: %s", self._path.name
        )


class FixtureVisionProvider:
    """Deterministic Vision_Provider backed by a hash → analysis map (+ optional overlay)."""

    name: Literal["fixture"] = "fixture"

    def __init__(
        self,
        analysis_by_hash: Mapping[str, VisionAnalysis],
        *,
        overlay_path: Path | None = None,
    ) -> None:
        self._analyses = {k.lower(): v.model_copy(deep=True) for k, v in analysis_by_hash.items()}
        self._overlay = _Overlay(Path(overlay_path)) if overlay_path is not None else None

    @classmethod
    def from_file(
        cls, path: Path = DEFAULT_VISION_FIXTURE_PATH, *, overlay_path: Path | None = None
    ) -> FixtureVisionProvider:
        return cls(load_vision_fixture(path), overlay_path=overlay_path)

    def __len__(self) -> int:
        return len(self._analyses)

    def analyze(self, image: bytes, mime_type: str, sha256: str | None = None) -> VisionAnalysis:
        """Overlay → demo fixture → ``UNKNOWN_ANALYSIS`` (Req 6.3)."""
        key = (sha256 or hashlib.sha256(image).hexdigest()).lower()
        analysis = self._overlay.lookup(key) if self._overlay is not None else None
        if analysis is None:
            analysis = self._analyses.get(key, UNKNOWN_ANALYSIS)
        return analysis.model_copy(deep=True)
