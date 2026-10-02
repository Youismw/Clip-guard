"""Unit tests for synthetic video generator and edit catalog."""

from __future__ import annotations

from pathlib import Path

from dedupe.eval.edits import EditCatalog, generate_synthetic_clip


def test_generate_synthetic_clip(tmp_path: Path) -> None:
    clip_path = tmp_path / "test_orig.mp4"
    res = generate_synthetic_clip(clip_path, duration_s=2.0)
    assert res.exists()
    assert res.stat().st_size > 0


def test_edit_catalog_single_edits(tmp_path: Path) -> None:
    orig = tmp_path / "base.mp4"
    generate_synthetic_clip(orig, duration_s=3.0)

    catalog = EditCatalog()
    out_dir = tmp_path / "variants"

    # Exact copy
    v_exact = catalog.apply_single_edit(orig, out_dir, "exact_copy")
    assert v_exact.output_path.exists()
    assert v_exact.is_gated is True
    assert v_exact.output_path.stat().st_size == orig.stat().st_size

    # Trim edit
    v_trim = catalog.apply_single_edit(orig, out_dir, "trim_head_1s", duration_s=3.0)
    assert v_trim.output_path.exists()
    assert v_trim.offset_s == 1.0

    # Color adjust
    v_color = catalog.apply_single_edit(orig, out_dir, "color_adjust", duration_s=3.0)
    assert v_color.output_path.exists()

    # Horizontal flip
    v_flip = catalog.apply_single_edit(orig, out_dir, "hflip", duration_s=3.0)
    assert v_flip.output_path.exists()
    assert v_flip.flipped is True
