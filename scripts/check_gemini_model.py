#!/usr/bin/env python3
"""Check that GEMINI_MODEL can be called in Agent Platform express mode (task 19.12, Req 3.2, 16.5, 17.10).

Run this once before the first live Vision call:

    backend/.venv/bin/python scripts/check_gemini_model.py

Settings are read through ``app.config`` the same way the backend reads them
(process environment > repository-root ``.env`` > defaults).

- ``GEMINI_API_KEY`` empty: no API call is made; the script prints a hint and
  exits with code 0.
- Key set: builds the same express-mode client as the Vision provider and
  sends one short text ``generate_content`` call ("Reply with OK"). An
  express-mode key cannot call ``models.get``/``models.list`` (401), so a
  real generation call is the only reliable check. Exit code 0 on success,
  1 on failure.

Model ID forms:

- Short IDs such as ``gemini-3-flash-preview`` work with the default express
  client.
- ``gemini-4-hydrogen`` works only through the ``global`` location, as a full
  path ``projects/<PROJECT_NUMBER>/locations/global/publishers/google/models/gemini-4-hydrogen``
  with ``base_url=https://aiplatform.googleapis.com/`` and ``api_version=v1``.

Output is the model ID and OK/fail only. On failure the error type and HTTP
status code are printed. The API key and the response body are never printed.
This script is not a test and pytest does not collect it.

Docs:
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/overview
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/vertex-ai-express-mode-api-quickstart
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
ENV_FILE = REPO_ROOT / ".env"

FALLBACK_MODEL = "gemini-3-flash-preview"
GLOBAL_HYDROGEN_PATH = "projects/<PROJECT_NUMBER>/locations/global/publishers/google/models/gemini-4-hydrogen"


def main() -> int:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import RawSettings, resolve_config  # noqa: PLC0415
    # Same client as GeminiVisionProvider: full ``projects/...`` paths get the
    # global v1 endpoint, short IDs keep the SDK defaults.
    from app.providers.vision.gemini import build_client  # noqa: PLC0415

    raw = RawSettings(_env_file=ENV_FILE if ENV_FILE.is_file() else None)  # type: ignore[call-arg]
    config, _issues = resolve_config(raw.model_dump())
    model = config.gemini_model

    if not config.gemini_api_key_configured:
        print(
            f"SKIP model={model}: GEMINI_API_KEY가 비어 있어 호출하지 않았습니다. "
            "Agent Platform express mode 키를 .env 또는 환경 변수에 넣고 다시 실행하세요."
        )
        return 0

    try:
        client = build_client(config.gemini_api_key, model)
        client.models.generate_content(model=model, contents="Reply with OK")
    except Exception as exc:  # report type and status only, never the message
        status = getattr(exc, "code", None)
        has_status = isinstance(status, int) and not isinstance(status, bool)
        status_text = f", status={status}" if has_status else ""
        print(f"FAIL model={model}: error={type(exc).__name__}{status_text}")
        if has_status and status == 404:
            print(
                f"이 모델을 express mode에서 찾지 못했습니다. GEMINI_MODEL을 {FALLBACK_MODEL} "
                f"또는 global 전체 경로 {GLOBAL_HYDROGEN_PATH} 로 바꿔 보세요."
            )
        return 1

    print(f"OK model={model}: Agent Platform express mode 호출 성공")
    return 0


if __name__ == "__main__":
    sys.exit(main())
