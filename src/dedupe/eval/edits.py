"""Synthetic video and edit generation catalog for evaluation harness."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class EditVariant:
    variant_name: str
    original_path: Path
    output_path: Path
    is_gated: bool
    edit_type: str
    offset_s: float = 0.0
    flipped: bool = False


def _get_ffmpeg_cmd() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        # Fallback to standard installation paths if not on current PATH
        standard_path = Path("C:/Users/ROHIT CHAUHAN/AppData/Local/Microsoft/WinGet/Packages")
        matches = list(standard_path.glob("**/ffmpeg.exe"))
        if matches:
            return str(matches[0])
        raise RuntimeError("ffmpeg binary not found on PATH or standard directories.")
    return exe


def generate_synthetic_clip(
    output_path: Path,
    duration_s: float = 12.0,
    pattern: str = "testsrc2",
    frequency: int = 440,
    size: str = "640x360",
    rate: int = 25,
) -> Path:
    """Generate a clean, deterministic synthetic video with audio using lavfi."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _get_ffmpeg_cmd()

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"{pattern}=size={size}:rate={rate}",
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

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg synthetic generation failed: {proc.stderr}")
    return output_path


class EditCatalog:
    """Applies catalog of synthetic edits specified in Section 11."""

    GATED_EDITS = {
        "exact_copy",
        "trim_head_1s",
        "trim_tail_1s",
        "trim_both_3s",
        "reencode_h264_q1",
        "reencode_h264_q2",
        "reencode_h265",
        "resize_720p",
        "resize_480p",
        "strip_metadata",
        "watermark",
        "color_adjust",
        "hflip",
        "audio_reencode",
    }

    REPORT_ONLY_EDITS = {
        "audio_replace",
        "crop_10",
        "speed_105",
    }

    def __init__(self, ffmpeg_bin: Optional[str] = None) -> None:
        self.ffmpeg = ffmpeg_bin or _get_ffmpeg_cmd()

    def apply_single_edit(
        self,
        original: Path,
        output_dir: Path,
        edit_name: str,
        duration_s: float = 12.0,
    ) -> EditVariant:
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = original.suffix or ".mp4"
        out_file = output_dir / f"{original.stem}__{edit_name}{ext}"
        is_gated = edit_name in self.GATED_EDITS
        offset_s = 0.0
        flipped = False

        if edit_name == "exact_copy":
            shutil.copy2(original, out_file)
            return EditVariant(
                variant_name=edit_name,
                original_path=original,
                output_path=out_file,
                is_gated=True,
                edit_type="exact_copy",
            )

        cmd = [self.ffmpeg, "-y", "-i", str(original)]

        if edit_name == "trim_head_1s":
            cmd = [self.ffmpeg, "-y", "-ss", "1.0", "-i", str(original), "-c", "copy"]
            offset_s = 1.0
        elif edit_name == "trim_tail_1s":
            new_dur = max(1.0, duration_s - 1.0)
            cmd = [self.ffmpeg, "-y", "-i", str(original), "-t", f"{new_dur:.2f}", "-c", "copy"]
        elif edit_name == "trim_both_3s":
            new_dur = max(2.0, duration_s - 4.0)
            cmd = [
                self.ffmpeg,
                "-y",
                "-ss",
                "2.0",
                "-i",
                str(original),
                "-t",
                f"{new_dur:.2f}",
                "-c",
                "copy",
            ]
            offset_s = 2.0
        elif edit_name == "reencode_h264_q1":
            cmd += ["-c:v", "libx264", "-crf", "23", "-c:a", "copy"]
        elif edit_name == "reencode_h264_q2":
            cmd += ["-c:v", "libx264", "-crf", "32", "-c:a", "copy"]
        elif edit_name == "reencode_h265":
            cmd += ["-c:v", "libx265", "-crf", "28", "-c:a", "copy"]
        elif edit_name == "resize_720p":
            cmd += ["-vf", "scale=-2:720", "-c:a", "copy"]
        elif edit_name == "resize_480p":
            cmd += ["-vf", "scale=-2:480", "-c:a", "copy"]
        elif edit_name == "strip_metadata":
            cmd += ["-map_metadata", "-1", "-c", "copy"]
        elif edit_name == "watermark":
            # Overlay a small rectangle watermark in top-left
            cmd += ["-vf", "drawbox=x=10:y=10:w=60:h=30:color=white@0.8:t=fill", "-c:a", "copy"]
        elif edit_name == "color_adjust":
            cmd += ["-vf", "eq=brightness=0.06:contrast=1.1", "-c:a", "copy"]
        elif edit_name == "hflip":
            cmd += ["-vf", "hflip", "-c:a", "copy"]
            flipped = True
        elif edit_name == "audio_reencode":
            cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "64k", "-af", "volume=1.2"]
        elif edit_name == "audio_replace":
            cmd = [
                self.ffmpeg,
                "-y",
                "-i",
                str(original),
                "-f",
                "lavfi",
                "-i",
                "anoisesrc=sample_rate=44100:amplitude=0.1",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-shortest",
            ]
        elif edit_name == "crop_10":
            cmd += ["-vf", "crop=in_w*0.9:in_h*0.9,scale=in_w:in_h", "-c:a", "copy"]
        elif edit_name == "speed_105":
            cmd += [
                "-filter_complex",
                "[0:v]setpts=PTS/1.05[v];[0:a]atempo=1.05[a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
            ]
        else:
            raise ValueError(f"Unknown edit name: '{edit_name}'")

        cmd.append(str(out_file))

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err = proc.stderr
            raise RuntimeError(f"FFmpeg edit '{edit_name}' failed on {original.name}: {err}")

        return EditVariant(
            variant_name=edit_name,
            original_path=original,
            output_path=out_file,
            is_gated=is_gated,
            edit_type=edit_name,
            offset_s=offset_s,
            flipped=flipped,
        )

    def apply_catalog(
        self,
        original: Path,
        output_dir: Path,
        requested_edits: Optional[Sequence[str]] = None,
        duration_s: float = 12.0,
    ) -> Sequence[EditVariant]:
        """Apply all or selected edits to the original clip."""
        edits_to_run = requested_edits or list(self.GATED_EDITS | self.REPORT_ONLY_EDITS)
        variants: list[EditVariant] = []

        for edit_name in edits_to_run:
            var = self.apply_single_edit(
                original=original,
                output_dir=output_dir,
                edit_name=edit_name,
                duration_s=duration_s,
            )
            variants.append(var)

        return variants
