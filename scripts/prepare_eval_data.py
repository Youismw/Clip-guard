"""Script to populate data/originals and data/distinct with synthetic clips for evaluation."""

from pathlib import Path

from dedupe.eval.edits import generate_synthetic_clip


def main() -> None:
    data_dir = Path("data")
    originals_dir = data_dir / "originals"
    distinct_dir = data_dir / "distinct"

    originals_dir.mkdir(parents=True, exist_ok=True)
    distinct_dir.mkdir(parents=True, exist_ok=True)

    print("Generating synthetic originals...")
    generate_synthetic_clip(
        originals_dir / "dishwashing_01.mp4",
        duration_s=12.0,
        pattern="testsrc2",
        frequency=440,
    )
    generate_synthetic_clip(
        originals_dir / "cutting_veg_01.mp4",
        duration_s=12.0,
        pattern="smptebars",
        frequency=880,
    )

    print("Generating distinct negative clips...")
    generate_synthetic_clip(
        distinct_dir / "distinct_unrelated_01.mp4",
        duration_s=10.0,
        pattern="mandelbrot",
        frequency=220,
    )
    generate_synthetic_clip(
        distinct_dir / "distinct_unrelated_02.mp4",
        duration_s=10.0,
        pattern="testsrc",
        frequency=330,
    )
    print("Done! Generated 2 originals and 2 distinct clips.")


if __name__ == "__main__":
    main()
