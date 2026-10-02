"""Integration test for dedupe eval CLI command."""

from __future__ import annotations

from pathlib import Path

from dedupe import cli
from dedupe.eval.edits import generate_synthetic_clip


def test_eval_cli_e2e(tmp_path: Path) -> None:
    orig_dir = tmp_path / "data" / "originals"
    orig_dir.mkdir(parents=True)
    c1 = orig_dir / "clip1.mp4"
    generate_synthetic_clip(c1, duration_s=2.5)

    out_dir = tmp_path / "eval_results"

    eval_toml = tmp_path / "eval.toml"
    eval_toml.write_text(
        f"""
[eval]
originals_dir = "{orig_dir.as_posix()}"
output_dir = "{out_dir.as_posix()}"
edits = ["exact_copy", "trim_head_1s"]
""",
        encoding="utf-8",
    )

    exit_code = cli.main(["eval", "--config", str(eval_toml)])
    assert exit_code == cli.EXIT_CLEAR

    json_report = out_dir / "eval_report.json"
    md_report = out_dir / "eval_report.md"

    assert json_report.exists()
    assert md_report.exists()

    md_content = md_report.read_text(encoding="utf-8")
    assert "ClipGuard: Evaluation Benchmark Report" in md_content
    assert "exact_copy" in md_content
    assert "trim_head_1s" in md_content
