"""FFmpeg integration for decoding raw video frames without temporary files."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Union

import numpy as np


class MediaError(Exception):
    """Base exception for media processing errors."""


class ToolMissingError(MediaError):
    """Raised when an external required tool (e.g. ffmpeg) is missing."""


class DecodeError(MediaError):
    """Raised when ffmpeg fails to decode a video stream."""


def find_ffmpeg() -> str:
    """Locate the ffmpeg executable on PATH or known system locations."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    # Windows winget fallback location
    winget_path = Path("C:/Users/ROHIT CHAUHAN/AppData/Local/Microsoft/WinGet/Packages")
    matches = list(winget_path.glob("**/ffmpeg.exe"))
    if matches:
        return str(matches[0])

    raise ToolMissingError("ffmpeg binary not found on PATH or standard directories.")


def find_ffprobe() -> str:
    """Locate the ffprobe executable on PATH or known system locations."""
    exe = shutil.which("ffprobe")
    if exe:
        return exe

    winget_path = Path("C:/Users/ROHIT CHAUHAN/AppData/Local/Microsoft/WinGet/Packages")
    matches = list(winget_path.glob("**/ffprobe.exe"))
    if matches:
        return str(matches[0])

    raise ToolMissingError("ffprobe binary not found on PATH or standard directories.")


def get_video_duration(video_path: Union[Path, str]) -> float:
    """Get video duration in seconds via ffprobe."""
    path = Path(video_path)
    if not path.exists():
        raise DecodeError(f"Video file does not exist: {path}")

    ffprobe = find_ffprobe()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            raise DecodeError(f"ffprobe failed: {proc.stderr}")
        return float(proc.stdout.strip())
    except Exception as exc:
        raise DecodeError(f"Failed to probe video duration: {exc}") from exc


def decode_frames_gray(
    video_path: Union[Path, str],
    fps: float = 1.0,
    width: int = 32,
    height: int = 32,
    timeout_s: float = 120.0,
) -> Iterator[tuple[int, np.ndarray]]:
    """Decode video to 32x32 grayscale frames piped directly into memory.

    Yields:
        (t_ms, frame_ndarray) where frame_ndarray is a 2D uint8 numpy array of shape (32, 32).
    """
    path = Path(video_path)
    if not path.exists():
        raise DecodeError(f"Video file does not exist: {path}")

    ffmpeg = find_ffmpeg()
    bytes_per_frame = width * height

    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vf",
        f"fps={fps},scale={width}:{height}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    frame_idx = 0
    ms_per_frame = 1000.0 / fps

    try:
        while True:
            assert proc.stdout is not None
            raw_bytes = proc.stdout.read(bytes_per_frame)
            if not raw_bytes:
                break
            if len(raw_bytes) < bytes_per_frame:
                # Truncated trailing bytes, ignore
                break

            frame = np.frombuffer(raw_bytes, dtype=np.uint8).reshape((height, width))
            t_ms = int(round(frame_idx * ms_per_frame))
            yield t_ms, frame
            frame_idx += 1

        proc.wait(timeout=timeout_s)
        if proc.returncode != 0:
            stderr_msg = ""
            if proc.stderr:
                stderr_msg = proc.stderr.read().decode("utf-8", errors="replace")
            raise DecodeError(f"ffmpeg decode error (exit {proc.returncode}): {stderr_msg}")
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise DecodeError(f"ffmpeg timed out after {timeout_s} seconds") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
