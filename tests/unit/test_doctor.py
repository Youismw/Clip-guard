"""Unit tests for dedupe doctor diagnostics and CLI."""

from __future__ import annotations

import json
from pathlib import Path

from dedupe import cli
from dedupe.doctor import (
    check_python_version,
    format_report_json,
    format_report_text,
    parse_and_validate_config,
    run_doctor,
)


def test_python_version_check() -> None:
    result = check_python_version()
    assert result.passed is True
    assert "Python" in result.message


def test_config_validation_missing_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "nonexistent.toml"
    ok, msg, cfg = parse_and_validate_config(missing_file)
    assert ok is False
    assert "does not exist" in msg
    assert cfg is None


def test_config_validation_valid(tmp_path: Path) -> None:
    cfg_file = tmp_path / "test_dedupe.toml"
    cfg_file.write_text(
        """
[pipeline]
combiner = "any"

[detectors.frame_phash]
enabled = true
radius = 3
num_chunks = 4
""",
        encoding="utf-8",
    )
    ok, msg, cfg = parse_and_validate_config(cfg_file)
    assert ok is True
    assert "Valid config" in msg
    assert cfg is not None
    assert cfg["pipeline"]["combiner"] == "any"


def test_config_validation_invalid_pigeonhole(tmp_path: Path) -> None:
    cfg_file = tmp_path / "invalid_dedupe.toml"
    cfg_file.write_text(
        """
[detectors.frame_phash]
enabled = true
radius = 3
num_chunks = 3
""",
        encoding="utf-8",
    )
    ok, msg, cfg = parse_and_validate_config(cfg_file)
    assert ok is False
    assert "num_chunks (3) must be >= radius + 1 (4)" in msg


def test_run_doctor_structure() -> None:
    report = run_doctor()
    names = [c.name for c in report.checks]
    assert "python" in names
    assert "config" in names
    assert "ffmpeg" in names
    assert "ffprobe" in names
    assert "fpcalc" in names

    # Text report formatting
    text = format_report_text(report)
    assert "ClipGuard Doctor Diagnostics" in text

    # JSON report formatting
    json_str = format_report_json(report)
    parsed = json.loads(json_str)
    assert "ok" in parsed
    assert "checks" in parsed
    assert len(parsed["checks"]) == len(report.checks)


def test_cli_usage_error(capsys: object) -> None:
    exit_code = cli.main([])
    assert exit_code == cli.EXIT_USAGE_ERROR


def test_cli_doctor_json(capsys: object) -> None:
    exit_code = cli.main(["doctor", "--json"])
    # Exit code is 0 if all required dependencies exist, or 1 if ffmpeg is missing
    assert exit_code in {cli.EXIT_CLEAR, cli.EXIT_ERROR}
