#!/usr/bin/env python3
"""Check that the configured Gemini model ID exists (task 4.3, Req 3.2, 16.2, 16.5).

Run this once before the first live Vision call:

    backend/.venv/bin/python scripts/check_gemini_model.py

Settings are read the same way the backend reads them (process environment >
repository-root ``.env`` > defaults), so ``GEMINI_MODEL`` falls back to
``gemini-4-hydrogen``.

- ``GEMINI_API_KEY`` empty: no API call is made; the script prints a hint and
  exits with code 0.
- Key set: calls ``client.models.get(model=GEMINI_MODEL)`` once and prints the
  model ID only. Exit code 0 when the model exists, 1 otherwise.

The API key is never printed. On failure only the error type, the HTTP status
code (when there is one) and the model ID are printed. This script is not a
test and pytest does not collect it.

Official model list: https://ai.google.dev/gemini-api/docs/models
(redirects to https://aistudio.google.com/docs/models).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
ENV_FILE = REPO_ROOT / ".env"


def main() -> int:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import DEFAULT_GEMINI_MODEL, RawSettings  # noqa: PLC0415

    settings = RawSettings(_env_file=ENV_FILE if ENV_FILE.is_file() else None)  # type: ignore[call-arg]
    model_id = settings.GEMINI_MODEL.strip() or DEFAULT_GEMINI_MODEL
    api_key = settings.GEMINI_API_KEY.strip()

    if not api_key:
        print(
            "GEMINI_API_KEY가 설정되지 않아 확인을 건너뜁니다. "
            f"키를 .env 또는 환경 변수에 넣고 다시 실행하세요. (GEMINI_MODEL={model_id})"
        )
        return 0

    from google import genai  # noqa: PLC0415

    try:
        client = genai.Client(api_key=api_key)
        model = client.models.get(model=model_id)
    except Exception as exc:  # report the type and status only, never the message
        status = getattr(exc, "code", None)
        status_text = f", status={status}" if isinstance(status, int) else ""
        print(f"모델 확인 실패: model={model_id}, error={type(exc).__name__}{status_text}")
        return 1

    found = (model.name or "").removeprefix("models/") or model_id
    print(f"모델 확인 성공: model={found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
