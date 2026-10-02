"""Environment and dependency diagnostics (dedupe doctor)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]

MIN_PYTHON_VERSION = (3, 9)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    message: str
    required: bool = True
    details: Optional[str] = None


@dataclass(frozen=True)
class DoctorReport:
    checks: Sequence[CheckResult]
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
        }


def check_python_version() -> CheckResult:
    current = sys.version_info
    ver_str = f"{current.major}.{current.minor}.{current.micro}"
    min_str = f"{MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}"
    if (current.major, current.minor) >= MIN_PYTHON_VERSION:
        return CheckResult(
            name="python",
            passed=True,
            message=f"Python {ver_str} (>= {min_str})",
            required=True,
        )
    return CheckResult(
        name="python",
        passed=False,
        message=f"Python {ver_str} is below minimum requirement {min_str}",
        required=True,
    )


def _check_binary(name: str, version_args: Sequence[str], required: bool) -> CheckResult:
    executable = shutil.which(name)
    if not executable:
        # Windows winget fallback search
        winget_path = Path("C:/Users/ROHIT CHAUHAN/AppData/Local/Microsoft/WinGet/Packages")
        matches = list(winget_path.glob(f"**/{name}.exe"))
        if matches:
            executable = str(matches[0])

    if not executable:
        return CheckResult(
            name=name,
            passed=False,
            message=f"'{name}' executable not found on PATH",
            required=required,
        )

    try:
        proc = subprocess.run(
            [executable, *version_args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
        version_text = first_line[0] if first_line else "available"
        return CheckResult(
            name=name,
            passed=True,
            message=f"Found: {version_text}",
            required=required,
            details=executable,
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            message=f"Error executing '{name}': {exc}",
            required=required,
            details=str(exc),
        )


def check_ffmpeg() -> CheckResult:
    return _check_binary("ffmpeg", ["-version"], required=True)


def check_ffprobe() -> CheckResult:
    return _check_binary("ffprobe", ["-version"], required=True)


def check_fpcalc(required: bool = False) -> CheckResult:
    return _check_binary("fpcalc", ["-v"], required=required)


def parse_and_validate_config(config_path: Path) -> tuple[bool, str, Optional[Mapping[str, Any]]]:
    if not config_path.exists():
        return False, f"Config file does not exist: {config_path}", None

    try:
        with config_path.open("rb") as f:
            cfg = tomllib.load(f)
    except Exception as exc:
        return False, f"Failed to parse TOML config '{config_path}': {exc}", None

    # Validate essential sections
    frame_cfg = cfg.get("detectors", {}).get("frame_phash", {})
    if frame_cfg.get("enabled", False):
        radius = frame_cfg.get("radius", 3)
        num_chunks = frame_cfg.get("num_chunks", 4)
        if num_chunks < radius + 1:
            err_msg = (
                f"Invalid config in '{config_path}': "
                f"num_chunks ({num_chunks}) must be >= radius + 1 ({radius + 1})"
            )
            return False, err_msg, cfg

    return True, f"Valid config loaded from {config_path}", cfg


def run_doctor(config_path: Optional[Path] = None, check_all: bool = False) -> DoctorReport:
    checks: list[CheckResult] = []

    # 1. Python runtime
    checks.append(check_python_version())

    # 2. Config validation & audio requirement detection
    audio_enabled = False
    if config_path is not None:
        cfg_ok, cfg_msg, parsed_cfg = parse_and_validate_config(config_path)
        checks.append(
            CheckResult(
                name="config",
                passed=cfg_ok,
                message=cfg_msg,
                required=True,
            )
        )
        if parsed_cfg:
            audio_sec = parsed_cfg.get("detectors", {}).get("audio_chromaprint", {})
            audio_enabled = bool(audio_sec.get("enabled", False))
    else:
        default_cfg = Path("dedupe.toml")
        if default_cfg.exists():
            cfg_ok, cfg_msg, parsed_cfg = parse_and_validate_config(default_cfg)
            checks.append(
                CheckResult(
                    name="config",
                    passed=cfg_ok,
                    message=f"(dedupe.toml) {cfg_msg}",
                    required=False,
                )
            )
            if parsed_cfg:
                audio_sec = parsed_cfg.get("detectors", {}).get("audio_chromaprint", {})
                audio_enabled = bool(audio_sec.get("enabled", False))
        else:
            checks.append(
                CheckResult(
                    name="config",
                    passed=True,
                    message="No dedupe.toml found (using defaults/example)",
                    required=False,
                )
            )

    # 3. Core binaries
    checks.append(check_ffmpeg())
    checks.append(check_ffprobe())

    # 4. Optional binaries (fpcalc)
    fpcalc_required = audio_enabled or check_all
    checks.append(check_fpcalc(required=fpcalc_required))

    # All required checks must pass
    all_ok = all(c.passed for c in checks if c.required)
    return DoctorReport(checks=checks, ok=all_ok)


def format_report_text(report: DoctorReport) -> str:
    lines = ["ClipGuard Doctor Diagnostics", "=" * 32]
    for check in report.checks:
        status_tag = "[OK]  " if check.passed else ("[FAIL]" if check.required else "[WARN]")
        req_tag = "" if check.required else " (optional)"
        lines.append(f"{status_tag} {check.name}{req_tag}: {check.message}")
    lines.append("-" * 32)
    lines.append("Result: PASS" if report.ok else "Result: FAIL (missing required components)")
    return "\n".join(lines)


def format_report_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), indent=2)
