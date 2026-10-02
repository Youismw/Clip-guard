"""Script to run the full ablation matrix across detector subsets."""

from __future__ import annotations

import tempfile
from pathlib import Path

from dedupe.eval.harness import EvalConfig, EvaluationHarness


def run_configuration(
    name: str,
    exact: bool,
    frame: bool,
    audio: bool,
    originals_dir: Path,
    distinct_dir: Path,
    edits: list[str],
) -> tuple[str, float, float, float]:
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        toml_content = f"""
[pipeline]
combiner = "any"

[detectors.exact_sha256]
enabled = {str(exact).lower()}

[detectors.frame_phash]
enabled = {str(frame).lower()}
fps = 1
radius = 3
flag_threshold = 0.80
review_threshold = 0.40

[detectors.audio_chromaprint]
enabled = {str(audio).lower()}
ber_threshold = 0.35
flag_threshold = 0.80
review_threshold = 0.50
"""
        f.write(toml_content)
        cfg_path = Path(f.name)

    try:
        eval_cfg = EvalConfig(
            originals_dir=originals_dir,
            output_dir=Path("./eval_results"),
            distinct_dir=distinct_dir,
            pipeline_config_path=cfg_path,
            edits=edits,
        )
        harness = EvaluationHarness(eval_cfg)
        res = harness.run()
        fp_rate = res.distinct.fp_rate if res.distinct else 0.0
        return name, res.gated_recall, res.overall_recall, fp_rate
    finally:
        if cfg_path.exists():
            cfg_path.unlink()


def main() -> None:
    data_dir = Path("data")
    orig_dir = data_dir / "originals"
    dist_dir = data_dir / "distinct"
    edits = [
        "exact_copy",
        "trim_head_1s",
        "trim_tail_1s",
        "reencode_h264_q1",
        "reencode_h264_q2",
        "resize_720p",
        "strip_metadata",
        "color_adjust",
        "hflip",
        "audio_reencode",
    ]

    configs = [
        ("1. exact_sha256 only", True, False, False),
        ("2. frame_phash only", False, True, False),
        ("3. audio_chromaprint only", False, False, True),
        ("4. exact + frame_phash (Phase 3)", True, True, False),
        ("5. exact + frame + audio (Phase 4)", True, True, True),
    ]

    print("Running Ablation Matrix Benchmarks...")
    print("=" * 65)
    print(f"{'Detector Combination':<36} | {'Gated':<7} | {'Overall':<7} | {'FP Rate':<7}")
    print("-" * 65)

    results = []
    for name, exact, frame, audio in configs:
        row = run_configuration(name, exact, frame, audio, orig_dir, dist_dir, edits)
        results.append(row)
        print(
            f"{row[0]:<36} | {row[1] * 100:>5.1f}% | {row[2] * 100:>5.1f}% | {row[3] * 100:>5.2f}%"
        )

    print("=" * 65)


if __name__ == "__main__":
    main()
