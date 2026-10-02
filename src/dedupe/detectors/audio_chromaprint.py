"""Acoustic duplicate detector using Chromaprint (fpcalc) and bit error rate verification."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from dedupe.detectors.base import Detector
from dedupe.media.ffmpeg import ToolMissingError
from dedupe.models import ClipRef, Match
from dedupe.registry import register_detector
from dedupe.stores.base import Store


def find_fpcalc() -> str:
    """Locate fpcalc executable on PATH or system directories."""
    exe = shutil.which("fpcalc")
    if exe:
        return exe

    winget_path = Path("C:/Users/ROHIT CHAUHAN/AppData/Local/Microsoft/WinGet/Packages")
    matches = list(winget_path.glob("**/fpcalc.exe"))
    if matches:
        return str(matches[0])

    raise ToolMissingError("fpcalc executable not found on PATH or system directories.")


@register_detector("audio_chromaprint")
class AudioChromaprintDetector(Detector):
    """Acoustic fingerprint detector using raw 32-bit Chromaprint sub-fingerprints."""

    name: ClassVar[str] = "audio_chromaprint"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: Mapping[str, Any], store: Store) -> None:
        super().__init__(params, store)
        self.ber_threshold = float(params.get("ber_threshold", 0.35))
        self.flag_threshold = float(params.get("flag_threshold", 0.80))
        self.review_threshold = float(params.get("review_threshold", 0.50))
        self.min_overlap_frames = int(params.get("min_overlap_frames", 8))

    def requirements(self) -> Sequence[str]:
        return ["fpcalc"]

    def extract(self, clip: ClipRef) -> Sequence[int]:
        """Compute raw 32-bit sub-fingerprints for audio stream using fpcalc."""
        try:
            exe = find_fpcalc()
        except ToolMissingError:
            # Detector fail-soft
            return []

        cmd = [
            exe,
            "-raw",
            "-length",
            "10000",  # Sufficient to cover entire clip without default cap
            "-json",
            str(clip.local_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode != 0:
                # Video may have no audio track or corrupt audio; fail-soft
                return []

            data = json.loads(proc.stdout)
            subfps: list[int] = data.get("fingerprint", [])
            return subfps
        except Exception:
            # Fail-soft on decode/timeout error
            return []

    def register(self, clip_id: str, fp: Any) -> None:
        """Register sub-fingerprints into the audio index."""
        subfps = list(fp) if isinstance(fp, Sequence) else []
        self.store.audio.add(clip_id, subfps)

    def search(self, fp: Any, exclude_clip_id: str) -> Sequence[Match]:
        """Query audio index by sub-fingerprint voting and bit error rate verification."""
        query_subfps = list(fp) if isinstance(fp, Sequence) else []
        if len(query_subfps) < self.min_overlap_frames:
            return []

        # Offset voting: cand_clip_id -> offset -> votes
        cand_offsets: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

        for p_q, subfp in enumerate(query_subfps):
            hits = self.store.audio.lookup(subfp)
            for cand_id, p_db in hits:
                if cand_id == exclude_clip_id:
                    continue
                offset = p_db - p_q
                cand_offsets[cand_id][offset] += 1

        matches: list[Match] = []

        for cand_id, offsets in cand_offsets.items():
            best_offset = max(offsets, key=lambda k: offsets[k])
            votes = offsets[best_offset]
            if votes < 2:
                continue

            db_subfps = list(self.store.audio.get_fingerprint(cand_id))
            if not db_subfps:
                continue

            # Compute aligned overlap
            start_q = max(0, -best_offset)
            end_q = min(len(query_subfps), len(db_subfps) - best_offset)
            overlap_len = end_q - start_q

            if overlap_len < self.min_overlap_frames:
                continue

            # Compute Bit Error Rate (BER) across aligned overlap
            total_bits = overlap_len * 32
            bit_errors = 0
            for q_idx in range(start_q, end_q):
                db_idx = q_idx + best_offset
                bit_errors += (query_subfps[q_idx] ^ db_subfps[db_idx]).bit_count()

            ber = bit_errors / total_bits

            if ber <= self.ber_threshold:
                # Confidence is inverse function of BER
                conf = max(
                    0.0,
                    min(
                        1.0,
                        1.0 - (ber / self.ber_threshold) * (1.0 - self.review_threshold),
                    ),
                )
                if ber <= 0.15:
                    conf = max(conf, self.flag_threshold)

                if conf >= self.review_threshold:
                    cand_clip = self.store.clips.get(cand_id)
                    status = cand_clip.status if cand_clip else None
                    matches.append(
                        Match(
                            detector=self.name,
                            matched_clip_id=cand_id,
                            confidence=conf,
                            evidence={
                                "offset_s": round(best_offset / 6.25, 2),
                                "ber": round(ber, 4),
                                "overlap_subfps": overlap_len,
                            },
                            matched_status=status,
                        )
                    )

        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches
