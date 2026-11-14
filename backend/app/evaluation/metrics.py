"""Pure metric computation for the vision evaluation (issue #3).

No I/O and no provider calls here, so everything is unit-testable offline.

Definitions
-----------
- ``photo_type``: evaluated on every successfully analyzed photo. Per-class
  precision / recall / F1 over ``outfit``, ``non_outfit`` and ``uncertain``
  (``uncertain`` is never a true label, so its recall is undefined; its
  precision shows how often the model refuses to decide).
- ``upper_type`` / ``outer_type`` / ``lower_type``: evaluated only on photos
  whose *true* ``photo_type`` is ``outfit``. A model that calls such a photo
  ``non_outfit`` returns ``unknown`` tags, which then counts as an error.
- ``exact_match``: fraction of true-outfit photos where photo_type and all three
  categories are right.
- Photos the provider failed on (``VisionError``) or that are missing on disk
  are reported separately and excluded from every metric.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

CATEGORY_FIELDS = ("upper_type", "outer_type", "lower_type")
PHOTO_TYPES = ("outfit", "non_outfit", "uncertain")


@dataclass(frozen=True)
class Truth:
    file: str
    photo_type: str
    upper_type: str
    outer_type: str
    lower_type: str


@dataclass(frozen=True)
class Prediction:
    photo_type: str
    photo_type_confidence: float
    upper_type: str
    outer_type: str
    lower_type: str


@dataclass(frozen=True)
class Scored:
    truth: Truth
    prediction: Prediction


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def per_class_metrics(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """``pairs`` are ``(truth, predicted)``. Returns accuracy, per-class P/R/F1, macro-F1."""
    total = len(pairs)
    correct = sum(1 for t, p in pairs if t == p)
    labels = sorted({t for t, _ in pairs} | {p for _, p in pairs})
    classes: dict[str, dict[str, Any]] = {}
    for label in labels:
        tp = sum(1 for t, p in pairs if t == label and p == label)
        fp = sum(1 for t, p in pairs if t != label and p == label)
        fn = sum(1 for t, p in pairs if t == label and p != label)
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        classes[label] = {
            "support": tp + fn,
            "predicted": tp + fp,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    # Macro-F1 averages only classes that occur in the truth, so an unused
    # class (e.g. "uncertain") cannot drag the score to zero.
    f1s = [c["f1"] for c in classes.values() if c["support"] > 0 and c["f1"] is not None]
    return {
        "n": total,
        "accuracy": _ratio(correct, total),
        "macro_f1": (sum(f1s) / len(f1s)) if f1s else None,
        "classes": classes,
    }


def confusion(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    table: dict[str, Counter[str]] = {}
    for truth, predicted in pairs:
        table.setdefault(truth, Counter())[predicted] += 1
    return {t: dict(sorted(c.items())) for t, c in sorted(table.items())}


def compute_metrics(scored: list[Scored]) -> dict[str, Any]:
    photo_pairs = [(s.truth.photo_type, s.prediction.photo_type) for s in scored]
    outfit = [s for s in scored if s.truth.photo_type == "outfit"]

    metrics: dict[str, Any] = {"photo_type": per_class_metrics(photo_pairs)}
    metrics["photo_type"]["confusion"] = confusion(photo_pairs)

    for field in CATEGORY_FIELDS:
        pairs = [(getattr(s.truth, field), getattr(s.prediction, field)) for s in outfit]
        block = per_class_metrics(pairs)
        block["confusion"] = confusion(pairs)
        metrics[field] = block

    exact = sum(
        1
        for s in outfit
        if s.prediction.photo_type == "outfit"
        and all(getattr(s.truth, f) == getattr(s.prediction, f) for f in CATEGORY_FIELDS)
    )
    metrics["exact_match"] = {"n": len(outfit), "accuracy": _ratio(exact, len(outfit))}
    return metrics


def find_misclassified(scored: list[Scored]) -> list[dict[str, Any]]:
    """One row per photo with at least one wrong field."""
    rows: list[dict[str, Any]] = []
    for s in scored:
        errors: dict[str, dict[str, str]] = {}
        if s.truth.photo_type != s.prediction.photo_type:
            errors["photo_type"] = {"expected": s.truth.photo_type, "predicted": s.prediction.photo_type}
        if s.truth.photo_type == "outfit":
            for field in CATEGORY_FIELDS:
                expected, predicted = getattr(s.truth, field), getattr(s.prediction, field)
                if expected != predicted:
                    errors[field] = {"expected": expected, "predicted": predicted}
        if errors:
            rows.append(
                {
                    "file": s.truth.file,
                    "photo_type_confidence": s.prediction.photo_type_confidence,
                    "errors": errors,
                }
            )
    return rows


def find_unknown(scored: list[Scored]) -> list[dict[str, Any]]:
    """True-outfit photos where the model answered ``unknown`` for a field whose
    true label is *not* ``unknown`` (a missed garment, as opposed to a wrong one)."""
    rows: list[dict[str, Any]] = []
    for s in scored:
        if s.truth.photo_type != "outfit":
            continue
        fields = {
            f: {"expected": getattr(s.truth, f)}
            for f in CATEGORY_FIELDS
            if getattr(s.prediction, f) == "unknown" and getattr(s.truth, f) != "unknown"
        }
        if fields or s.prediction.photo_type == "uncertain":
            rows.append(
                {
                    "file": s.truth.file,
                    "photo_type": s.prediction.photo_type,
                    "unknown_fields": fields,
                }
            )
    return rows


def headline(metrics: dict[str, Any]) -> dict[str, float | None]:
    """Flat metric map used by thresholds and baseline comparison."""
    pt = metrics["photo_type"]["classes"]
    flat: dict[str, float | None] = {
        "photo_type.accuracy": metrics["photo_type"]["accuracy"],
        "photo_type.outfit.precision": pt.get("outfit", {}).get("precision"),
        "photo_type.outfit.recall": pt.get("outfit", {}).get("recall"),
        "photo_type.non_outfit.precision": pt.get("non_outfit", {}).get("precision"),
        "photo_type.non_outfit.recall": pt.get("non_outfit", {}).get("recall"),
        "exact_match.accuracy": metrics["exact_match"]["accuracy"],
    }
    for field in CATEGORY_FIELDS:
        flat[f"{field}.accuracy"] = metrics[field]["accuracy"]
        flat[f"{field}.macro_f1"] = metrics[field]["macro_f1"]
    return flat
