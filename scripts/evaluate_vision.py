#!/usr/bin/env python3
"""Evaluate photo-type and garment-category accuracy of a Vision provider (issue #3).

One command writes ``results.json``, ``summary.md``, ``misclassified.json`` and
``unknown.json`` to ``eval-results/<timestamp>/``:

    # Offline pipeline check (fixture provider, no network, no key).
    backend/.venv/bin/python scripts/evaluate_vision.py

    # Opt-in live run: needs GEMINI_API_KEY (env or .env) and the demo photos.
    backend/.venv/bin/python scripts/evaluate_vision.py --provider gemini --check-thresholds

The fixture provider returns the same labels used as ground truth, so its
numbers only prove the pipeline works. The Gemini provider is never built unless
``--provider gemini`` is given and a key is present; normal tests never call it.

Exit codes: 0 ok, 1 thresholds/regression failed (--check-thresholds), 2 usage or
environment problem (no key, no photos, bad labels).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_LABELS = REPO_ROOT / "data" / "eval" / "labels.json"
DEFAULT_THRESHOLDS = REPO_ROOT / "data" / "eval" / "thresholds.json"
DEFAULT_PHOTOS = REPO_ROOT / "data" / "demo" / "photos"
DEFAULT_OUT_ROOT = REPO_ROOT / "eval-results"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", choices=["fixture", "gemini"], default="fixture")
    p.add_argument("--model", help="Gemini model ID (default: GEMINI_MODEL from env/.env)")
    p.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    p.add_argument("--photos", type=Path, default=DEFAULT_PHOTOS)
    p.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    p.add_argument("--out-dir", type=Path, help="default: eval-results/<provider>-<timestamp>")
    p.add_argument("--limit", type=int, help="evaluate only the first N labeled photos (cheap smoke run)")
    p.add_argument("--baseline", type=Path, help="earlier results.json; fail if a metric dropped too much")
    p.add_argument("--check-thresholds", action="store_true", help="exit 1 if a regression floor is missed")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import load_config  # noqa: PLC0415
    from app.evaluation.runner import (  # noqa: PLC0415
        LabelsError,
        check_thresholds,
        evaluate,
        load_labels,
        write_reports,
    )
    from app.providers.vision.fixture import FixtureVisionProvider  # noqa: PLC0415

    try:
        labels_header, truths = load_labels(args.labels)
    except LabelsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.photos.is_dir():
        print(f"error: photo folder not found: {args.photos.name}. Run scripts/prepare_demo_photos.py first.", file=sys.stderr)
        return 2

    if not any((args.photos / t.file).is_file() for t in truths):
        print(
            f"error: none of the {len(truths)} labeled photos exist in {args.photos.name}. "
            "The photos are git-ignored; run scripts/prepare_demo_photos.py or pass --photos.",
            file=sys.stderr,
        )
        return 2

    if args.provider == "gemini":
        config, _issues = load_config(REPO_ROOT / ".env")
        if not config.gemini_api_key_configured:
            print("error: --provider gemini needs GEMINI_API_KEY (environment or .env). Nothing was called.", file=sys.stderr)
            return 2
        from app.providers.vision.gemini import GeminiVisionProvider  # noqa: PLC0415

        provider = GeminiVisionProvider(model=args.model or config.gemini_model, api_key=config.gemini_api_key)
        selected = truths[: args.limit] if args.limit else truths
        planned = sum((args.photos / t.file).is_file() for t in selected)  # missing files make no call
        print(f"live run: {planned} Gemini calls with model {provider.model}", file=sys.stderr)
    else:
        provider = FixtureVisionProvider.from_file()

    def progress(i: int, n: int, name: str) -> None:
        if i == 1 or i % 20 == 0 or i == n:
            print(f"[{i}/{n}] {name}", file=sys.stderr)

    result = evaluate(truths, args.photos, provider, limit=args.limit, on_progress=progress)
    result["labels"] = labels_header

    thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    verdict = check_thresholds(result, thresholds, baseline)

    out_dir = args.out_dir or DEFAULT_OUT_ROOT / f"{args.provider}-{datetime.now():%Y%m%d-%H%M%S}"
    write_reports(result, out_dir, verdict)
    print(f"wrote {out_dir}")
    print(f"evaluated={result['counts']['evaluated']} missing={result['counts']['missing_files']} "
          f"errors={result['counts']['provider_errors']} thresholds={'PASS' if verdict['passed'] else 'FAIL'}")

    if args.check_thresholds and not verdict["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
