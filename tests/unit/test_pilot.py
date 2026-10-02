"""Unit tests for Section 13 Local Pilot Protocol runner."""

from __future__ import annotations

from pathlib import Path

from dedupe.eval.edits import generate_synthetic_clip
from dedupe.eval.pilot import (
    DuplicatePair,
    PilotProtocolRunner,
    load_known_pairs_csv,
    write_pilot_reports,
)


def test_pilot_protocol_runner_synthetic(tmp_path: Path) -> None:
    # 1. Generate 4 originals
    orig_dir = tmp_path / "origs"
    orig_dir.mkdir()
    dup_dir = tmp_path / "dups"
    dup_dir.mkdir()

    pairs: list[DuplicatePair] = []
    csv_rows = ["original_path,returned_path,alteration"]

    for i in range(4):
        orig_p = orig_dir / f"orig_{i}.mp4"
        dup_p = dup_dir / f"dup_{i}.mp4"
        generate_synthetic_clip(orig_p, duration_s=4.0, frequency=300 + i * 100)
        # Duplicate is exact copy or slight variant
        generate_synthetic_clip(dup_p, duration_s=4.0, frequency=300 + i * 100)

        pairs.append(DuplicatePair(orig_p, dup_p, f"test_alteration_{i}"))
        csv_rows.append(f"{orig_p},{dup_p},test_alteration_{i}")

    # 2. Distinct clips
    dist_dir = tmp_path / "distinct"
    dist_dir.mkdir()
    d1 = dist_dir / "distinct_1.mp4"
    generate_synthetic_clip(d1, duration_s=4.0, pattern="mandelbrot", frequency=999)

    # Test load_known_pairs_csv
    csv_path = tmp_path / "test_pairs.csv"
    csv_path.write_text("\n".join(csv_rows), encoding="utf-8")
    loaded_pairs = load_known_pairs_csv(csv_path)
    assert len(loaded_pairs) == 4

    # Run pilot
    runner = PilotProtocolRunner(
        pairs=loaded_pairs,
        distinct_dir=dist_dir,
        scripted_edits_recall=1.0,
    )
    report = runner.run()

    assert report.total_pairs == 4
    assert report.tuning_pairs_count == 2
    assert report.validation_pairs_count == 2
    assert report.distinct_clips_count == 1
    assert report.checklist.recall_real_pairs_pass is True
    assert report.checklist.determinism_pass is True
    assert report.checklist.decision == "GO"

    # Test report writer
    out_dir = tmp_path / "pilot_out"
    json_p, md_p = write_pilot_reports(report, out_dir)
    assert json_p.exists()
    assert md_p.exists()
    md_text = md_p.read_text(encoding="utf-8")
    assert "Section 13 Go/No-Go Checklist" in md_text
    assert "**`GO`**" in md_text
