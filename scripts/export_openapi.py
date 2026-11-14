#!/usr/bin/env python3
"""Dump the backend OpenAPI document (task 10.4, Req 10.6, 10.7).

    backend/.venv/bin/python scripts/export_openapi.py -o /tmp/openapi.json
    cd frontend && npx openapi-typescript /tmp/openapi.json -o src/api/schema.d.ts

The app is built from ``app.main.create_app`` with a fixture-only config
(``resolve_config({})``), so the real ``.env`` is never read and no API key
is used. ``DATABASE_URL`` and ``PHOTO_DIR`` point into a temporary directory
that is deleted afterwards, so ``data/weatherwear.db`` is never created.
Without ``-o`` the JSON goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"


def build_openapi() -> dict:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import resolve_config  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="ww-openapi-") as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "photos").mkdir()
        config, _ = resolve_config({})
        config = replace(
            config,
            database_url=f"sqlite:///{tmp_path / 'openapi.db'}",
            photo_dir=tmp_path / "photos",
        )
        app = create_app(config)
        try:
            return app.openapi()
        finally:
            app.state.container.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out", type=Path, help="output file (default: stdout)")
    args = parser.parse_args(argv)

    text = json.dumps(build_openapi(), ensure_ascii=False, indent=2) + "\n"
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
