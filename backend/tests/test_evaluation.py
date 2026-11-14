"""Tests for the vision evaluation tooling (issue #3). No network, no API key."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.domain import UNKNOWN_TAGS, ClothingTags, VisionAnalysis
from app.evaluation.metrics import (
    Prediction,
    Scored,
    Truth,
    compute_metrics,
    find_misclassified,
    find_unknown,
    headline,
    per_class_metrics,
)
from app.evaluation.runner import (
    LabelsError,
    check_thresholds,
    evaluate,
    load_labels,
    provider_info,
    render_summary,
    write_reports,
)
from app.providers.vision.base import VisionError

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS = REPO_ROOT / "data" / "eval" / "labels.json"
THRESHOLDS = REPO_ROOT / "data" / "eval" / "thresholds.json"


def _truth(file="a.png", pt="outfit", upper="t_shirt", outer="none", lower="jeans") -> Truth:
    return Truth(file, pt, upper, outer, lower)


def _pred(pt="outfit", upper="t_shirt", outer="none", lower="jeans", conf=0.9) -> Prediction:
    return Prediction(pt, conf, upper, outer, lower)


# --- metrics -----------------------------------------------------------------


def test_per_class_metrics_hand_computed() -> None:
    # truth/pred: a->a, a->b, b->b, b->b, b->a
    pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("b", "b"), ("b", "a")]
    m = per_class_metrics(pairs)
    assert m["accuracy"] == pytest.approx(3 / 5)
    # class a: tp=1 fp=1 fn=1 -> P=R=0.5, F1=0.5
    assert m["classes"]["a"]["precision"] == pytest.approx(0.5)
    assert m["classes"]["a"]["recall"] == pytest.approx(0.5)
    assert m["classes"]["a"]["f1"] == pytest.approx(0.5)
    # class b: tp=2 fp=1 fn=1 -> P=R=2/3
    assert m["classes"]["b"]["f1"] == pytest.approx(2 / 3)
    assert m["macro_f1"] == pytest.approx((0.5 + 2 / 3) / 2)


def test_undefined_precision_is_none_not_zero() -> None:
    m = per_class_metrics([("a", "b")])
    assert m["classes"]["a"]["precision"] is None  # never predicted
    assert m["classes"]["a"]["recall"] == 0.0
    assert m["classes"]["b"]["recall"] is None  # never true
    assert m["classes"]["a"]["f1"] is None  # precision undefined -> F1 undefined
    assert m["macro_f1"] is None  # no class with a defined F1


def test_unused_class_does_not_drag_macro_f1() -> None:
    # "uncertain" is predicted but never true; macro-F1 must ignore it.
    m = per_class_metrics([("outfit", "outfit"), ("non_outfit", "non_outfit"), ("outfit", "uncertain")])
    assert m["classes"]["uncertain"]["support"] == 0
    f1s = [m["classes"][c]["f1"] for c in ("outfit", "non_outfit")]
    assert m["macro_f1"] == pytest.approx(sum(f1s) / 2)


def test_non_outfit_called_outfit_hurts_photo_type_but_categories_only_count_true_outfits() -> None:
    scored = [
        Scored(_truth("o.png"), _pred()),
        Scored(_truth("n.png", pt="non_outfit", upper="unknown", outer="unknown", lower="unknown"), _pred(upper="shirt")),
    ]
    m = compute_metrics(scored)
    assert m["photo_type"]["accuracy"] == 0.5
    assert m["photo_type"]["classes"]["outfit"]["precision"] == 0.5
    assert m["upper_type"]["n"] == 1  # the non-outfit photo is not in category metrics
    assert m["exact_match"] == {"n": 1, "accuracy": 1.0}


def test_true_outfit_predicted_non_outfit_is_a_category_error_and_not_exact() -> None:
    wrong = _pred(pt="non_outfit", upper="unknown", outer="unknown", lower="unknown")
    scored = [Scored(_truth(), wrong)]
    m = compute_metrics(scored)
    assert m["upper_type"]["accuracy"] == 0.0
    assert m["exact_match"]["accuracy"] == 0.0
    rows = find_misclassified(scored)
    assert set(rows[0]["errors"]) == {"photo_type", "upper_type", "outer_type", "lower_type"}


def test_exact_match_requires_photo_type_and_all_categories() -> None:
    scored = [
        Scored(_truth("1.png"), _pred()),
        Scored(_truth("2.png"), _pred(lower="slacks")),
        Scored(_truth("3.png"), _pred(pt="uncertain")),
    ]
    assert compute_metrics(scored)["exact_match"]["accuracy"] == pytest.approx(1 / 3)


def test_unknown_report_lists_missed_garments_not_true_unknowns() -> None:
    scored = [
        Scored(_truth("miss.png", outer="coat"), _pred(outer="unknown")),
        Scored(_truth("fine.png", outer="unknown"), _pred(outer="unknown")),
    ]
    rows = find_unknown(scored)
    assert [r["file"] for r in rows] == ["miss.png"]
    assert rows[0]["unknown_fields"] == {"outer_type": {"expected": "coat"}}


def test_headline_keys_exist_even_with_no_samples() -> None:
    flat = headline(compute_metrics([]))
    assert flat["photo_type.accuracy"] is None
    assert "upper_type.macro_f1" in flat


# --- labels manifest -----------------------------------------------------------


def test_committed_labels_are_valid_and_match_the_demo_set() -> None:
    header, truths = load_labels(LABELS)
    assert header["count"] == len(truths) == 159
    assert sum(t.photo_type == "non_outfit" for t in truths) == 20
    assert sum(t.photo_type == "outfit" for t in truths) == 139
    assert all(
        t.upper_type == t.outer_type == t.lower_type == "unknown"
        for t in truths
        if t.photo_type == "non_outfit"
    )
    manifest = json.loads((REPO_ROOT / "data" / "demo" / "photos.manifest.json").read_text())
    confirmed = {p["file"] for p in manifest["photos"] if p.get("rights_confirmed")}
    # Every labeled photo must be rights-confirmed. The manifest may list more photos
    # than are labeled yet (new photos need a hand-checked label first).
    assert {t.file for t in truths} <= confirmed


def _write_labels(path: Path, items: list[dict]) -> Path:
    path.write_text(json.dumps({"version": "t", "items": items}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "item",
    [
        {"file": "a.png", "photo_type": "uncertain", "upper_type": "unknown", "outer_type": "unknown", "lower_type": "unknown"},
        {"file": "a.png", "photo_type": "outfit", "upper_type": "spaceship", "outer_type": "none", "lower_type": "jeans"},
        {"file": "a.png", "photo_type": "outfit"},
    ],
)
def test_load_labels_rejects_bad_items(tmp_path: Path, item: dict) -> None:
    with pytest.raises(LabelsError):
        load_labels(_write_labels(tmp_path / "l.json", [item]))


def test_load_labels_rejects_duplicates_and_empty(tmp_path: Path) -> None:
    ok = {"file": "a.png", "photo_type": "outfit", "upper_type": "t_shirt", "outer_type": "none", "lower_type": "jeans"}
    with pytest.raises(LabelsError):
        load_labels(_write_labels(tmp_path / "d.json", [ok, ok]))
    with pytest.raises(LabelsError):
        load_labels(_write_labels(tmp_path / "e.json", []))
    with pytest.raises(LabelsError):
        load_labels(tmp_path / "missing.json")


# --- runner with a fake provider ------------------------------------------------


class FakeProvider:
    name = "fixture"

    def __init__(self, answers: dict[str, VisionAnalysis | VisionError]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def analyze(self, image: bytes, mime_type: str, sha256: str) -> VisionAnalysis:
        self.calls.append(mime_type)
        answer = self.answers[str(len(self.calls) - 1)]
        if isinstance(answer, VisionError):
            raise answer
        return answer


def _png(path: Path, color: int) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (color, 0, 0)).save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _analysis(pt="outfit", upper="t_shirt", outer="none", lower="jeans", conf=0.9) -> VisionAnalysis:
    tags = ClothingTags(
        upper_type=upper, outer_type=outer, lower_type=lower, colors=[], layers_visible=1, confidence=0.8
    )
    if pt == "non_outfit":
        tags = UNKNOWN_TAGS
    return VisionAnalysis(photo_type=pt, photo_type_confidence=conf, reason="test", tags=tags)


def test_evaluate_counts_missing_failures_and_scores(tmp_path: Path) -> None:
    for i, name in enumerate(["a.png", "b.png", "c.png"]):
        _png(tmp_path / name, i)
    truths = [_truth("a.png"), _truth("b.png"), _truth("c.png"), _truth("gone.png")]
    provider = FakeProvider({"0": _analysis(), "1": VisionError("timeout"), "2": _analysis(lower="slacks")})

    result = evaluate(truths, tmp_path, provider)  # type: ignore[arg-type]

    assert result["counts"]["labeled"] == 4
    assert result["counts"]["evaluated"] == 2
    assert result["counts"]["missing_files"] == 1
    assert result["counts"]["provider_errors"] == 1
    assert result["counts"]["error_rate"] == pytest.approx(1 / 3)
    assert result["provider_error_files"] == [{"file": "b.png", "kind": "timeout"}]
    assert result["metrics"]["lower_type"]["accuracy"] == 0.5
    assert [m["file"] for m in result["misclassified"]] == ["c.png"]
    assert len(provider.calls) == 3  # one call per existing file, no retries


def test_limit_caps_provider_calls(tmp_path: Path) -> None:
    for i, name in enumerate(["a.png", "b.png"]):
        _png(tmp_path / name, i)
    provider = FakeProvider({"0": _analysis(), "1": _analysis()})
    evaluate([_truth("a.png"), _truth("b.png")], tmp_path, provider, limit=1)  # type: ignore[arg-type]
    assert len(provider.calls) == 1


def test_provider_info_records_model_and_prompt_version() -> None:
    class Live:
        name = "gemini"
        model = "some-model-id"

    info = provider_info(Live())  # type: ignore[arg-type]
    assert info["model"] == "some-model-id"
    assert len(info["prompt_version"]) == 12 and info["prompt_used_by_provider"] is True
    assert provider_info(FakeProvider({}))["prompt_used_by_provider"] is False  # type: ignore[arg-type]


def test_reports_are_written_and_summary_flags_fixture_mode(tmp_path: Path) -> None:
    _png(tmp_path / "a.png", 1)
    result = evaluate([_truth("a.png")], tmp_path, FakeProvider({"0": _analysis()}))  # type: ignore[arg-type]
    result["labels"] = {"version": "t"}
    verdict = check_thresholds(result, {"regression_floor": {"photo_type.accuracy": 0.5}})
    paths = write_reports(result, tmp_path / "out", verdict)
    assert {p.name for p in paths} == {"results.json", "summary.md", "misclassified.json", "unknown.json"}
    saved = json.loads((tmp_path / "out" / "results.json").read_text())
    assert saved["provider"]["prompt_version"] and saved["verdict"]["passed"] is True
    assert "pipeline only" in render_summary(result, verdict)


# --- thresholds ----------------------------------------------------------------


def _result_with(accuracy_pairs: list[tuple[Truth, Prediction]], errors: int = 0) -> dict:
    scored = [Scored(t, p) for t, p in accuracy_pairs]
    attempted = len(scored) + errors
    return {
        "metrics": compute_metrics(scored),
        "counts": {"evaluated": len(scored), "error_rate": (errors / attempted) if attempted else None},
    }


def test_thresholds_floor_target_and_no_samples() -> None:
    good = _result_with([(_truth(), _pred())] * 4)
    ok = check_thresholds(good, {"targets": {"exact_match.accuracy": 1.0}, "regression_floor": {"exact_match.accuracy": 0.9}})
    assert ok["passed"] and not ok["below_target"]

    bad = _result_with([(_truth(), _pred()), (_truth(), _pred(upper="knit"))])
    verdict = check_thresholds(
        bad, {"targets": {"upper_type.accuracy": 0.9}, "regression_floor": {"upper_type.accuracy": 0.8}}
    )
    assert not verdict["passed"]
    assert verdict["failures"][0]["metric"] == "upper_type.accuracy"
    assert verdict["below_target"][0]["metric"] == "upper_type.accuracy"

    empty = check_thresholds(_result_with([]), {"regression_floor": {"photo_type.accuracy": 0.1}})
    assert not empty["passed"]  # nothing evaluated must never pass


def test_thresholds_error_rate_and_baseline_regression() -> None:
    cur = _result_with([(_truth(), _pred())] * 9, errors=1)
    assert check_thresholds(cur, {"max_error_rate": 0.05})["failures"][0]["metric"] == "error_rate"

    baseline = _result_with([(_truth(), _pred())] * 10)
    worse = _result_with([(_truth(), _pred())] * 8 + [(_truth(), _pred(upper="knit"))] * 2)
    verdict = check_thresholds(worse, {"max_drop_vs_baseline": 0.03}, baseline)
    assert not verdict["passed"]
    assert any(f.get("reason") == "regression_vs_baseline" for f in verdict["failures"])
    assert check_thresholds(baseline, {"max_drop_vs_baseline": 0.03}, baseline)["passed"]


def test_committed_thresholds_are_well_formed() -> None:
    t = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    flat_keys = set(headline(compute_metrics([])))
    assert set(t["targets"]) <= flat_keys and set(t["regression_floor"]) <= flat_keys
    for key, floor in t["regression_floor"].items():
        assert floor <= t["targets"].get(key, floor), key  # floor never above target
    assert 0 < t["max_error_rate"] < 1


# --- CLI ------------------------------------------------------------------------


def _load_cli():
    spec = importlib.util.spec_from_file_location("evaluate_vision", REPO_ROOT / "scripts" / "evaluate_vision.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_vision"] = module
    spec.loader.exec_module(module)
    return module


def test_cli_gemini_without_key_exits_2_and_never_builds_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)  # no .env there
    import app.providers.vision.gemini as gemini

    def boom(*a, **k):
        raise AssertionError("GeminiVisionProvider must not be built without a key")

    monkeypatch.setattr(gemini, "GeminiVisionProvider", boom)
    _png(tmp_path / "a.png", 1)
    labels = _write_labels(
        tmp_path / "labels.json",
        [{"file": "a.png", "photo_type": "outfit", "upper_type": "t_shirt", "outer_type": "none", "lower_type": "jeans"}],
    )
    code = cli.main(["--provider", "gemini", "--labels", str(labels), "--photos", str(tmp_path)])
    assert code == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_cli_exits_2_when_no_labeled_photo_exists(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()
    labels = _write_labels(
        tmp_path / "labels.json",
        [{"file": "a.png", "photo_type": "outfit", "upper_type": "t_shirt", "outer_type": "none", "lower_type": "jeans"}],
    )
    code = cli.main(["--labels", str(labels), "--photos", str(tmp_path)])
    assert code == 2
    assert "prepare_demo_photos" in capsys.readouterr().err


def test_cli_fixture_run_writes_reports_and_check_thresholds_fails_on_unknown_hashes(
    tmp_path: Path,
) -> None:
    cli = _load_cli()
    _png(tmp_path / "a.png", 7)  # hash is not in the fixture -> "uncertain"
    labels = _write_labels(
        tmp_path / "labels.json",
        [{"file": "a.png", "photo_type": "outfit", "upper_type": "t_shirt", "outer_type": "none", "lower_type": "jeans"}],
    )
    out = tmp_path / "out"
    args = ["--labels", str(labels), "--photos", str(tmp_path), "--out-dir", str(out)]
    assert cli.main(args) == 0
    assert (out / "summary.md").is_file() and (out / "results.json").is_file()
    assert cli.main([*args, "--check-thresholds"]) == 1
