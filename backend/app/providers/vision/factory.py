"""Chooses the Vision_Provider from config (design.md §4, Req 3.2, 3.3, 15.5).

``resolve_config`` already downgrades ``gemini`` to ``fixture`` when the key or
model is missing (Req 15.6). The factory repeats that check so a hand-built
``AppConfig`` cannot start a Gemini provider without a key.

The Gemini module (task 4.4) is imported lazily so fixture mode never imports
the Gemini SDK. Contract for ``GeminiVisionProvider``:
``GeminiVisionProvider(model: str, api_key: SecretStr, client: object | None = None)``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import AppConfig
from app.providers.vision.base import VisionProvider
from app.providers.vision.fixture import DEFAULT_VISION_FIXTURE_PATH, FixtureVisionProvider

logger = logging.getLogger(__name__)


def build_vision_provider(
    cfg: AppConfig,
    *,
    fixture_path: Path = DEFAULT_VISION_FIXTURE_PATH,
    gemini_client: object | None = None,
) -> VisionProvider:
    """Return ``GeminiVisionProvider`` for ``vision_provider=gemini``, else ``FixtureVisionProvider``.

    The fixture provider gets ``cfg.automation.vision_overlay_path`` (Phase 2,
    design.md §8); a missing overlay file changes nothing.
    ``gemini_client`` lets tests inject a fake ``genai.Client``.
    """
    if cfg.vision_provider == "gemini":
        if cfg.gemini_api_key_configured and cfg.gemini_model:
            try:
                from app.providers.vision.gemini import GeminiVisionProvider
            except ModuleNotFoundError as exc:
                if exc.name == "app.providers.vision.gemini":
                    raise NotImplementedError("GeminiVisionProvider is not implemented yet (task 4.4)") from exc
                raise
            return GeminiVisionProvider(
                model=cfg.gemini_model,
                api_key=cfg.gemini_api_key,
                client=gemini_client,
            )
        logger.error("VISION_PROVIDER=gemini이지만 GEMINI_API_KEY 또는 GEMINI_MODEL이 없어 fixture를 사용합니다.")
    # Phase 2 (Req 6.3): Vision_Fixture_Overlay is looked up before the demo fixture.
    return FixtureVisionProvider.from_file(
        fixture_path, overlay_path=cfg.automation.vision_overlay_path
    )
