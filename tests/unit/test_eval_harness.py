"""Unit tests for EvaluationHarness and report generation."""

from __future__ import annotations

import json
from pathlib import Path

from dedupe.eval.edits import generate_synthetic_clip
from dedupe.eval.harness import EvalConfig, EvaluationHarness, _calc_wilson_ci
from dedupe.eval.report import write_reports


def test_wilson_ci_bounds() -> None:
    low, high = _calc_wilson_ci(10, 10)
    assert low > 0.65
    assert high == 1.0

    low0, high0 = _calc_wilson_ci(0, 10)
    assert low0 == 0.0
    assert high0 < 0.35


def test_evaluation_harness_baseline(tmp_path: Path) -> None:
    # 1. Generate 2 synthetic originals
    orig_dir = tmp_path / "originals"
    orig_dir.mkdir()
    c1 = orig_dir / "orig_1.mp4"
    c2 = orig_dir / "orig_2.mp4"
    generate_synthetic_clip(c1, duration_s=3.0, frequency=440)
    generate_synthetic_clip(c2, duration_s=3.0, frequency=880)

    # 2. Distinct clip
    dist_dir = tmp_path / "distinct"
    dist_dir.mkdir()
    d1 = dist_dir / "dist_1.mp4"
    generate_synthetic_clip(d1, duration_s=3.0, frequency=1200)

    out_dir = tmp_path / "eval_out"

    # Write explicit baseline config (exact_sha256 only)
    baseline_cfg = tmp_path / "baseline.toml"
    baseline_cfg.write_text(
        """
[pipeline]
combiner = "any"

[detectors.exact_sha256]
enabled = true

[detectors.frame_phash]
enabled = false
""",
        encoding="utf-8",
    )

    # Only run exact_copy and reencode_h264_q1 to verify exact hash baseline
    eval_cfg = EvalConfig(
        originals_dir=orig_dir,
        output_dir=out_dir,
        distinct_dir=dist_dir,
        pipeline_config_path=baseline_cfg,
        edits=["exact_copy", "reencode_h264_q1"],
    )

    harness = EvaluationHarness(eval_cfg)
    report_data = harness.run()

    # Verify baseline properties with exact_sha256:
    # - exact_copy must be caught (100%)
    # - reencode_h264_q1 must be missed (0%)
    cat_dict = {c.category: c for c in report_data.categories}
    assert "exact_copy" in cat_dict
    assert cat_dict["exact_copy"].recall == 1.0
    assert cat_dict["exact_copy"].caught_count == 2

    assert "reencode_h264_q1" in cat_dict
    assert cat_dict["reencode_h264_q1"].recall == 0.0

    # Distinct false positive rate must be 0.0
    assert report_data.distinct is not None
    assert report_data.distinct.fp_rate == 0.0

    # Test report writers
    json_p, md_p = write_reports(report_data, out_dir)
    assert json_p.exists()
    assert md_p.exists()

    with json_p.open("r", encoding="utf-8") as f:
        parsed = json.load(f)
        assert parsed["total_originals"] == 2
        assert len(parsed["records"]) == 4

    md_text = md_p.read_text(encoding="utf-8")
    assert "ClipGuard: Evaluation Benchmark Report" in md_text
    assert "Gated Synthetic Edits Recall" in md_text


def test_evaluation_harness_visual_detection(tmp_path: Path) -> None:
    """Verify that enabling frame_phash catches re-encoded visual copies."""
    orig_dir = tmp_path / "orig_v"
    orig_dir.mkdir()
    c1 = orig_dir / "orig_v1.mp4"
    generate_synthetic_clip(c1, duration_s=4.0, frequency=440)

    out_dir = tmp_path / "eval_out_v"

    visual_cfg = tmp_path / "visual.toml"
    visual_cfg.write_text(
        """
[pipeline]
combiner = "any"

[detectors.exact_sha256]
enabled = true

[detectors.frame_phash]
enabled = true
fps = 1
radius = 3
flag_threshold = 0.80
review_threshold = 0.40
review_min_seconds = 2.0
""",
        encoding="utf-8",
    )

    eval_cfg = EvalConfig(
        originals_dir=orig_dir,
        output_dir=out_dir,
        pipeline_config_path=visual_cfg,
        edits=["reencode_h264_q1"],
    )

    harness = EvaluationHarness(eval_cfg)
    report = harness.run()

    cat_dict = {c.category: c for c in report.categories}
    assert "reencode_h264_q1" in cat_dict
    assert cat_dict["reencode_h264_q1"].recall == 1.0
