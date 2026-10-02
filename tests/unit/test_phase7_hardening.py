from __future__ import annotations

from pathlib import Path

from dedupe import cli


def test_documentation_structure() -> None:
    root = Path(__file__).resolve().parent.parent.parent

    # 1. README.md checks
    readme = root / "README.md"
    assert readme.exists(), "README.md must exist in project root"
    readme_text = readme.read_text(encoding="utf-8")
    assert "## 1. System Architecture" in readme_text
    assert "## 2. Detectors & Algorithms" in readme_text
    assert "## 4. CLI Reference & Exit Codes" in readme_text
    assert "## 6. Known Limitations and Risks" in readme_text
    assert "docs/extending.md" in readme_text
    assert "docs/runbook.md" in readme_text

    # 2. docs/extending.md checks
    extending = root / "docs" / "extending.md"
    assert extending.exists(), "docs/extending.md must exist"
    ext_text = extending.read_text(encoding="utf-8")
    assert "Adding a New Detector" in ext_text
    assert "Adding a New Storage Backend" in ext_text
    assert "Adding a New Video Source" in ext_text

    # 3. docs/runbook.md checks
    runbook = root / "docs" / "runbook.md"
    assert runbook.exists(), "docs/runbook.md must exist"
    rb_text = runbook.read_text(encoding="utf-8")
    assert "Threshold Tuning Runbook" in rb_text
    assert "Backfill and Re-Indexing Runbook" in rb_text
    assert "Production Cloud SQS Worker Runbook" in rb_text


def test_cli_subparsers_and_exit_codes() -> None:
    parser = cli.build_parser()
    assert parser is not None

    # Check all expected commands are present in subparsers
    expected_cmds = {
        "doctor",
        "index",
        "check",
        "stats",
        "gui",
        "eval",
        "pilot",
        "backfill",
        "worker",
    }
    assert set(parser._subparsers._group_actions[0].choices.keys()) == expected_cmds

    # Check standardized exit codes match Section 8 specification
    assert cli.EXIT_CLEAR == 0
    assert cli.EXIT_DUPLICATE == 10
    assert cli.EXIT_REVIEW == 11
    assert cli.EXIT_ALREADY_INDEXED == 12
    assert cli.EXIT_ERROR == 1
    assert cli.EXIT_USAGE_ERROR == 2


def test_dockerfile_and_requirements_pinning() -> None:
    root = Path(__file__).resolve().parent.parent.parent

    # 1. requirements.txt
    req = root / "requirements.txt"
    assert req.exists()
    lines = [
        line.strip()
        for line in req.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) >= 3
    for line in lines:
        assert "==" in line, f"Requirement {line} must be strictly pinned with =="

    # 2. Dockerfile
    dockerfile = root / "Dockerfile"
    assert dockerfile.exists()
    df_text = dockerfile.read_text(encoding="utf-8")
    assert "python:3.11-slim-bookworm" in df_text
    assert "HEALTHCHECK" in df_text
    assert "requirements.txt" in df_text
    assert 'ENTRYPOINT ["dedupe"]' in df_text
