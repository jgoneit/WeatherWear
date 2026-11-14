"""Vision_Provider interface (design.md §4).

Implementations take raw image bytes and return a validated ``VisionAnalysis``
containing the photo type and visible ``ClothingTags``. They are called once
per new image in a scan and never retry.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from app.domain import VisionAnalysis, VisionProviderName

VisionErrorKind = Literal["schema_invalid", "api_error", "timeout"]
VISION_ERROR_KINDS: tuple[VisionErrorKind, ...] = ("schema_invalid", "api_error", "timeout")


class VisionError(Exception):
    """Analysis failed. ``kind`` maps to the scan error code (Req 3.5):

    ``schema_invalid`` → ``VISION_SCHEMA_INVALID``, ``api_error`` →
    ``VISION_API_ERROR``, ``timeout`` → ``VISION_TIMEOUT``.
    The message must never contain the API key or file paths.
    """

    def __init__(self, kind: VisionErrorKind, message: str = "") -> None:
        if kind not in VISION_ERROR_KINDS:
            raise ValueError(f"unknown VisionError kind: {kind!r}")
        self.kind: VisionErrorKind = kind
        super().__init__(message or kind)

    @property
    def code(self) -> str:
        """Scan error code, e.g. ``VISION_TIMEOUT``."""
        return f"VISION_{self.kind.upper()}"


@runtime_checkable
class VisionProvider(Protocol):
    """Classifies one photo and analyzes visible garments in a single call.

    ``sha256`` is the digest the ingestion service already computed. Fixture
    providers use it as a lookup key while network providers may ignore it.
    Raises ``VisionError`` on failure.
    """

    name: VisionProviderName

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis: ...
