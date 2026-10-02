"""End-to-end integration tests for Phase 1 acceptance criteria."""

from __future__ import annotations

from pathlib import Path

from dedupe import cli


def test_phase1_e2e_lifecycle(tmp_path: Path) -> None:
    # 1. Setup workspace folders
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = tmp_path / "dedupe.db"

    cfg_file = tmp_path / "test_dedupe.toml"
    cfg_file.write_text(
        f"""
[pipeline]
combiner = "any"
register_after_check = true

[source]
type = "local"
path = "{data_dir.as_posix()}"

[store]
type = "sqlite"
path = "{db_file.as_posix()}"

[detectors.exact_sha256]
enabled = true
""",
        encoding="utf-8",
    )

    # Create dummy original video clips
    orig_a = data_dir / "clip_a.mp4"
    orig_a.write_bytes(b"Simulated raw video footage bytes - CLIP A")

    orig_b = data_dir / "clip_b.mp4"
    orig_b.write_bytes(b"Simulated raw video footage bytes - CLIP B")

    # 2. Index the folder
    ret_index = cli.main(
        ["--config", str(cfg_file), "index", str(data_dir), "--status", "approved"]
    )
    assert ret_index == cli.EXIT_CLEAR

    # Verify stats
    ret_stats = cli.main(["--config", str(cfg_file), "stats"])
    assert ret_stats == cli.EXIT_CLEAR

    # 3. Check identical file -> already_indexed (exit code 12)
    ret_check_self = cli.main(["--config", str(cfg_file), "check", str(orig_a), "--json"])
    assert ret_check_self == cli.EXIT_ALREADY_INDEXED

    # 4. Check renamed copy of an indexed file
    test_dir = tmp_path / "incoming"
    test_dir.mkdir()
    renamed_copy = test_dir / "resold_renamed_clip_a.mp4"
    renamed_copy.write_bytes(orig_a.read_bytes())

    ret_check_dup = cli.main(["--config", str(cfg_file), "check", str(renamed_copy), "--json"])
    # Identical bytes -> already_indexed (code 12)
    assert ret_check_dup == cli.EXIT_ALREADY_INDEXED

    # 5. Check unrelated new file -> clear (exit code 0)
    unrelated = test_dir / "brand_new_unique_clip.mp4"
    unrelated.write_bytes(b"Simulated completely unrelated unique video bytes - CLIP C")

    ret_check_clear = cli.main(["--config", str(cfg_file), "check", str(unrelated), "--json"])
    assert ret_check_clear == cli.EXIT_CLEAR

    # 6. Disabling detector via config works
    cfg_disabled = tmp_path / "disabled.toml"
    cfg_disabled.write_text(
        f"""
[pipeline]
combiner = "any"

[store]
type = "sqlite"
path = "{(tmp_path / "disabled.db").as_posix()}"

[detectors.exact_sha256]
enabled = false
""",
        encoding="utf-8",
    )

    unindexed_copy = test_dir / "copy_without_exact_det.mp4"
    unindexed_copy.write_bytes(b"Some video content bytes")

    ret_check_disabled = cli.main(
        ["--config", str(cfg_disabled), "check", str(unindexed_copy), "--json"]
    )
    assert ret_check_disabled == cli.EXIT_CLEAR
