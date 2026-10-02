"""Prepares a full pilot dataset with 30 duplicate pairs in data/known_pairs.csv."""

from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

from dedupe.eval.edits import EditCatalog, _get_ffmpeg_cmd


def generate_custom_clip(
    output_path: Path,
    pattern_filter: str,
    frequency: int,
    duration_s: float = 9.0,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _get_ffmpeg_cmd()
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        pattern_filter,
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100",
        "-t",
        f"{duration_s:.2f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed for {output_path}: {res.stderr}")


def main() -> None:
    data_dir = Path("data")
    pilot_orig_dir = data_dir / "pilot_originals"
    pilot_dup_dir = data_dir / "pilot_duplicates"
    distinct_dir = data_dir / "distinct"

    # Clean existing pilot directories to ensure pristine visual isolation
    shutil.rmtree(pilot_orig_dir, ignore_errors=True)
    shutil.rmtree(pilot_dup_dir, ignore_errors=True)
    shutil.rmtree(distinct_dir, ignore_errors=True)

    pilot_orig_dir.mkdir(parents=True, exist_ok=True)
    pilot_dup_dir.mkdir(parents=True, exist_ok=True)
    distinct_dir.mkdir(parents=True, exist_ok=True)

    catalog = EditCatalog()

    # 15 distinct tasks, each with completely unique fractal / simulation parameters
    tasks = [
        ("dishwashing_pot", "mandelbrot=s=640x360:rate=25:start_x=-0.7:start_y=0.27", 400),
        ("dishwashing_pan", "mandelbrot=s=640x360:rate=25:start_x=0.2:start_y=-0.6", 430),
        ("cutting_carrot", "mandelbrot=s=640x360:rate=25:start_x=-1.2:start_y=0.1", 460),
        ("cutting_onion", "mandelbrot=s=640x360:rate=25:start_x=-0.5:start_y=-0.5", 490),
        ("wiping_counter", "mandelbrot=s=640x360:rate=25:start_x=0.35:start_y=0.35", 520),
        ("stirring_soup", "mandelbrot=s=640x360:rate=25:start_x=-0.1:start_y=0.8", 550),
        ("folding_towel", "mandelbrot=s=640x360:rate=25:start_x=0.4:start_y=-0.2", 580),
        ("sorting_cutlery", "mandelbrot=s=640x360:rate=25:start_x=-0.8:start_y=-0.15", 610),
        ("placing_cup", "life=s=640x360:rate=25:mold=10:seed=111", 640),
        ("opening_drawer", "life=s=640x360:rate=25:mold=10:seed=222", 670),
        ("closing_cabinet", "life=s=640x360:rate=25:mold=10:seed=333", 700),
        ("pouring_water", "life=s=640x360:rate=25:mold=10:seed=444", 730),
        ("peeling_potato", "life=s=640x360:rate=25:mold=10:seed=555", 760),
        ("clearing_table", "life=s=640x360:rate=25:mold=10:seed=666", 790),
        ("stacking_plates", "life=s=640x360:rate=25:mold=10:seed=777", 820),
    ]

    # Pair edits catalog: 2 edits per task = 30 duplicate pairs
    edit_pairs_plan = [
        ("trim_head_1s", "reencode_h264_q1"),
        ("trim_tail_1s", "reencode_h264_q2"),
        ("color_adjust", "resize_720p"),
        ("strip_metadata", "hflip"),
        ("reencode_h264_q1", "trim_head_1s"),
        ("reencode_h264_q2", "trim_tail_1s"),
        ("resize_720p", "color_adjust"),
        ("hflip", "strip_metadata"),
        ("trim_head_1s", "reencode_h264_q2"),
        ("trim_tail_1s", "reencode_h264_q1"),
        ("color_adjust", "hflip"),
        ("resize_720p", "strip_metadata"),
        ("reencode_h264_q1", "color_adjust"),
        ("reencode_h264_q2", "resize_720p"),
        ("exact_copy", "trim_head_1s"),
    ]

    pairs_rows = [["original_path", "returned_path", "alteration"]]

    print("Generating 15 visually distinct original task videos...")
    for i, (task_name, filter_expr, freq) in enumerate(tasks):
        orig_file = pilot_orig_dir / f"{task_name}.mp4"
        print(f"  [{i + 1}/15] Original: {orig_file.name}")
        generate_custom_clip(orig_file, filter_expr, freq, duration_s=9.0)

        # Generate 2 duplicate variants per original = 30 pairs
        e1, e2 = edit_pairs_plan[i]
        for e in (e1, e2):
            v = catalog.apply_single_edit(orig_file, pilot_dup_dir, e, duration_s=9.0)
            pairs_rows.append([str(orig_file), str(v.output_path), e])

    csv_path = data_dir / "known_pairs.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(pairs_rows)

    print(f"Generated {len(pairs_rows) - 1} duplicate pairs in {csv_path}.")

    # Generate 10 distinct negative clips with completely separate visual patterns & frequencies
    print("Generating 10 visually isolated negative clips in data/distinct...")
    distinct_specs = [
        ("distinct_life_888.mp4", "life=s=640x360:rate=25:mold=10:seed=888", 850),
        ("distinct_life_999.mp4", "life=s=640x360:rate=25:mold=10:seed=999", 880),
        ("distinct_life_1010.mp4", "life=s=640x360:rate=25:mold=10:seed=1010", 910),
        ("distinct_life_1212.mp4", "life=s=640x360:rate=25:mold=10:seed=1212", 940),
        ("distinct_life_1313.mp4", "life=s=640x360:rate=25:mold=10:seed=1313", 970),
        (
            "distinct_mandel_a.mp4",
            "mandelbrot=s=640x360:rate=25:start_x=-0.75:start_y=0.1",
            1000,
        ),
        (
            "distinct_mandel_b.mp4",
            "mandelbrot=s=640x360:rate=25:start_x=0.1:start_y=-0.7",
            1030,
        ),
        (
            "distinct_mandel_c.mp4",
            "mandelbrot=s=640x360:rate=25:start_x=-0.3:start_y=0.6",
            1060,
        ),
        (
            "distinct_mandel_d.mp4",
            "mandelbrot=s=640x360:rate=25:start_x=0.25:start_y=0.55",
            1090,
        ),
        (
            "distinct_mandel_e.mp4",
            "mandelbrot=s=640x360:rate=25:start_x=-0.65:start_y=-0.35",
            1120,
        ),
    ]

    for fname, filter_expr, freq in distinct_specs:
        d_path = distinct_dir / fname
        print(f"  Distinct negative: {fname}")
        generate_custom_clip(d_path, filter_expr, freq, duration_s=9.0)

    print(f"Distinct directory contains {len(list(distinct_dir.glob('*.mp4')))} negative clips.")


if __name__ == "__main__":
    main()
