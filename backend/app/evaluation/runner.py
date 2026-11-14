"""Evaluation runner (issue #3): labels + provider -> results, reports, verdict.

The provider is injected, so tests use a fake one and never touch the network.
The Gemini provider is only built by ``scripts/evaluate_vision.py`` when the
user explicitly asks for it and a key is present.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain import LOWER_TYPES, OUTER_TYPES, UPPER_TYPES
from app.evaluation.metrics import (
    CATEGORY_FIELDS,
    PHOTO_TYPES,
    Prediction,
    Scored,
    Truth,
    compute_metrics,
    find_misclassified,
    find_unknown,
    headline,
)
from app.ingestion.image_check import mime_type_for
from app.providers.vision.base import VisionError, VisionProvider

_VOCAB = {
    "upper_type": set(UPPER_TYPES),
    "outer_type": set(OUTER_TYPES),
    "lower_type": set(LOWER_TYPES),
}


class LabelsError(ValueError):
    """The labels manifest is missing, malformed or uses unknown vocabulary."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_labels(path: Path) -> tuple[dict[str, Any], list[Truth]]:
    """Read and validate the ground-truth manifest. Returns (header, truths)."""
    try:
        raw = path.read_bytes()
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise LabelsError(f"cannot read labels {path.name}: {type(exc).__name__}") from None
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list) or not items:
        raise LabelsError("labels must be an object with a non-empty 'items' list")
    truths: list[Truth] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        try:
            truth = Truth(
                file=str(item["file"]),
                photo_type=str(item["photo_type"]),
                upper_type=str(item["upper_type"]),
                outer_type=str(item["outer_type"]),
                lower_type=str(item["lower_type"]),
            )
        except (KeyError, TypeError):
            raise LabelsError(f"item {i}: missing a required field") from None
        if truth.file in seen:
            raise LabelsError(f"duplicate file in labels: {truth.file}")
        seen.add(truth.file)
        if truth.photo_type not in ("outfit", "non_outfit"):
            raise LabelsError(f"{truth.file}: photo_type must be outfit or non_outfit")
        for field in CATEGORY_FIELDS:
            if getattr(truth, field) not in _VOCAB[field]:
                raise LabelsError(f"{truth.file}: {field}={getattr(truth, field)!r} is not allowed")
        truths.append(truth)
    header = {
        "version": doc.get("version", "unversioned"),
        "sha256": sha256_hex(raw),
        "count": len(truths),
    }
    return header, truths


def provider_info(provider: VisionProvider) -> dict[str, Any]:
    """Model ID and prompt version to record next to every result."""
    from app.providers.vision.gemini import PROMPT  # noqa: PLC0415  (pure constant)

    prompt_sha = sha256_hex(PROMPT.encode("utf-8"))
    return {
        "provider": provider.name,
        "model": getattr(provider, "model", None),
        "prompt_sha256": prompt_sha,
        "prompt_version": prompt_sha[:12],
        "prompt_used_by_provider": provider.name == "gemini",
    }


def evaluate(
    truths: list[Truth],
    photo_dir: Path,
    provider: VisionProvider,
    *,
    limit: int | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Run ``provider`` over every labeled photo and compute all metrics."""
    selected = truths[:limit] if limit else truths
    scored: list[Scored] = []
    predictions: list[dict[str, Any]] = []
    missing: list[str] = []
    failures: list[dict[str, str]] = []

    for index, truth in enumerate(selected, start=1):
        if on_progress:
            on_progress(index, len(selected), truth.file)
        path = photo_dir / truth.file
        try:
            data = path.read_bytes()
        except OSError:
            missing.append(truth.file)
            continue
        try:
            analysis = provider.analyze(data, mime_type_for(path.suffix), sha256_hex(data))
        except VisionError as exc:
            failures.append({"file": truth.file, "kind": exc.kind})
            continue
        tags = analysis.tags
        prediction = Prediction(
            photo_type=analysis.photo_type,
            photo_type_confidence=analysis.photo_type_confidence,
            upper_type=tags.upper_type,
            outer_type=tags.outer_type,
            lower_type=tags.lower_type,
        )
        scored.append(Scored(truth, prediction))
        predictions.append(
            {
                "file": truth.file,
                "truth": {"photo_type": truth.photo_type, **{f: getattr(truth, f) for f in CATEGORY_FIELDS}},
                "predicted": {
                    "photo_type": prediction.photo_type,
                    "photo_type_confidence": prediction.photo_type_confidence,
                    **{f: getattr(prediction, f) for f in CATEGORY_FIELDS},
                },
            }
        )

    attempted = len(scored) + len(failures)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider_info(provider),
        "counts": {
            "labeled": len(selected),
            "evaluated": len(scored),
            "missing_files": len(missing),
            "provider_errors": len(failures),
            "error_rate": (len(failures) / attempted) if attempted else None,
        },
        "metrics": compute_metrics(scored),
        "missing_files": missing,
        "provider_error_files": failures,
        "misclassified": find_misclassified(scored),
        "unknown": find_unknown(scored),
        "predictions": predictions,
    }


# -- thresholds and regression ------------------------------------------------


def check_thresholds(
    result: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare a result with the documented thresholds (and an optional baseline).

    ``below_target`` entries are warnings. ``failures`` (below the regression
    floor, error rate too high, or a drop versus the baseline) make the CLI exit 1.
    """
    flat = headline(result["metrics"])
    targets: Mapping[str, float] = thresholds.get("targets", {})
    floors: Mapping[str, float] = thresholds.get("regression_floor", {})
    below_target: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for key, floor in floors.items():
        value = flat.get(key)
        # A metric that cannot be computed (no samples) is a failure, not a pass.
        if value is None or value < floor:
            failures.append({"metric": key, "value": value, "floor": floor})
    for key, target in targets.items():
        value = flat.get(key)
        if value is None or value < target:
            below_target.append({"metric": key, "value": value, "target": target})

    max_error_rate = thresholds.get("max_error_rate")
    error_rate = result["counts"]["error_rate"]
    if max_error_rate is not None and error_rate is not None and error_rate > max_error_rate:
        failures.append({"metric": "error_rate", "value": error_rate, "max": max_error_rate})
    if result["counts"]["evaluated"] == 0:
        failures.append({"metric": "evaluated", "value": 0, "min": 1})

    regressions: list[dict[str, Any]] = []
    if baseline is not None:
        allowed_drop = float(thresholds.get("max_drop_vs_baseline", 0.03))
        base_flat = headline(baseline["metrics"])
        for key, base_value in base_flat.items():
            value = flat.get(key)
            if base_value is None or value is None:
                continue
            if base_value - value > allowed_drop:
                regressions.append({"metric": key, "baseline": base_value, "value": value})
        failures.extend({"metric": r["metric"], **r, "reason": "regression_vs_baseline"} for r in regressions)

    return {
        "passed": not failures,
        "failures": failures,
        "below_target": below_target,
        "headline": flat,
    }


# -- reports ------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_summary(result: Mapping[str, Any], verdict: Mapping[str, Any] | None = None) -> str:
    info = result["provider"]
    counts = result["counts"]
    metrics = result["metrics"]
    lines = [
        "# Vision evaluation summary",
        "",
        f"- Created: {result['created_at']}",
        f"- Provider: `{info['provider']}`  Model: `{info['model'] or 'n/a'}`",
        f"- Prompt version: `{info['prompt_version']}` (sha256 of the Gemini prompt"
        + ("" if info["prompt_used_by_provider"] else "; not used by this provider")
        + ")",
        f"- Labels: `{result.get('labels', {}).get('version', '?')}` ({counts['labeled']} photos)",
        f"- Evaluated: {counts['evaluated']}  Missing files: {counts['missing_files']}  "
        f"Provider errors: {counts['provider_errors']}",
    ]
    if info["provider"] == "fixture":
        lines += [
            "",
            "> **Fixture mode checks the pipeline only.** The fixture holds the same labels "
            "used as ground truth, so ~100% here says nothing about model accuracy. "
            "Use `--provider gemini` for a real measurement.",
        ]
    evaluated = counts["evaluated"]
    uncertain = sum(1 for p in result.get("predictions", []) if p["predicted"]["photo_type"] == "uncertain")
    if info["provider"] == "fixture" and evaluated and uncertain / evaluated > 0.5:
        lines += [
            "",
            f"> **{uncertain}/{evaluated} photos came back `uncertain`.** The fixture is keyed by "
            "SHA-256, so these photo bytes differ from the ones the fixture was built from. "
            "Re-run `scripts/build_fixtures.py` or use the original photos.",
        ]
    lines += ["", "## Headline", "", "| Metric | Value |", "|---|---|"]
    lines.append(f"| photo_type accuracy | {_pct(metrics['photo_type']['accuracy'])} |")
    lines.append(f"| exact match (outfit photos) | {_pct(metrics['exact_match']['accuracy'])} |")
    for field in CATEGORY_FIELDS:
        block = metrics[field]
        lines.append(f"| {field} accuracy / macro-F1 | {_pct(block['accuracy'])} / {_pct(block['macro_f1'])} |")

    lines += ["", "## photo_type per class", "", "| Class | Support | Precision | Recall | F1 |", "|---|---|---|---|---|"]
    for label in PHOTO_TYPES:
        c = metrics["photo_type"]["classes"].get(label)
        if c:
            lines.append(
                f"| {label} | {c['support']} | {_pct(c['precision'])} | {_pct(c['recall'])} | {_pct(c['f1'])} |"
            )
    for field in CATEGORY_FIELDS:
        lines += ["", f"## {field} per class", "", "| Class | Support | Precision | Recall | F1 |", "|---|---|---|---|---|"]
        for label, c in metrics[field]["classes"].items():
            lines.append(
                f"| {label} | {c['support']} | {_pct(c['precision'])} | {_pct(c['recall'])} | {_pct(c['f1'])} |"
            )

    lines += [
        "",
        f"Misclassified photos: {len(result['misclassified'])} (see `misclassified.json`). "
        f"Missed-garment/unknown photos: {len(result['unknown'])} (see `unknown.json`).",
    ]
    if verdict is not None:
        lines += ["", "## Thresholds", "", f"Result: **{'PASS' if verdict['passed'] else 'FAIL'}**"]
        for f in verdict["failures"]:
            lines.append(f"- FAIL `{f['metric']}`: {json.dumps(f, ensure_ascii=False)}")
        for w in verdict["below_target"]:
            lines.append(f"- below target `{w['metric']}`: {_pct(w['value'])} < {_pct(w['target'])}")
    return "\n".join(lines) + "\n"


def write_reports(
    result: dict[str, Any], out_dir: Path, verdict: Mapping[str, Any] | None = None
) -> list[Path]:
    """Write results.json, summary.md, misclassified.json, unknown.json."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: Any) -> Path:
        path = out_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    full = dict(result)
    if verdict is not None:
        full["verdict"] = verdict
    paths = [
        dump("results.json", full),
        dump("misclassified.json", result["misclassified"]),
        dump("unknown.json", result["unknown"]),
    ]
    summary = out_dir / "summary.md"
    summary.write_text(render_summary(result, verdict), encoding="utf-8")
    paths.append(summary)
    return paths
