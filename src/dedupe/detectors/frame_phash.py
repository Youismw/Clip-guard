"""Perceptual frame hash (pHash) detector with temporal offset voting and mirror check."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

import numpy as np

from dedupe.detectors.base import Detector
from dedupe.media.ffmpeg import decode_frames_gray
from dedupe.media.phash import compute_phash64, is_low_information
from dedupe.models import ClipRef, Match
from dedupe.registry import register_detector
from dedupe.stores.base import Store


@dataclass(frozen=True)
class FrameFingerprint:
    """Fingerprint containing temporal sequences of 64-bit frame hashes."""

    normal_frames: Sequence[tuple[int, int]]  # (t_ms, hash64)
    flipped_frames: Sequence[tuple[int, int]]  # (t_ms, hash64)
    valid_frames_count: int
    duration_s: float


@register_detector("frame_phash")
class FramePhashDetector(Detector):
    """Visual duplicate detector using pure numpy pHash and offset voting."""

    name: ClassVar[str] = "frame_phash"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: Mapping[str, Any], store: Store) -> None:
        super().__init__(params, store)
        self.fps = float(params.get("fps", 1.0))
        self.radius = int(params.get("radius", 3))
        self.num_chunks = int(params.get("num_chunks", 4))
        self.offset_bin_s = float(params.get("offset_bin_s", 2.0))
        self.min_frame_std = float(params.get("min_frame_std", 8.0))
        self.flag_threshold = float(params.get("flag_threshold", 0.80))
        self.review_threshold = float(params.get("review_threshold", 0.40))
        self.review_min_seconds = float(params.get("review_min_seconds", 8.0))
        self.max_clip_frequency: Optional[int] = params.get("max_clip_frequency")

    def requirements(self) -> Sequence[str]:
        return ["ffmpeg"]

    def extract(self, clip: ClipRef) -> FrameFingerprint:
        """Decode clip at target fps, drop low-information frames, and compute pHashes."""
        normal_frames: list[tuple[int, int]] = []
        flipped_frames: list[tuple[int, int]] = []

        for t_ms, frame in decode_frames_gray(clip.local_path, fps=self.fps):
            if is_low_information(frame, min_frame_std=self.min_frame_std):
                continue

            h_normal = compute_phash64(frame)
            # Horizontal flip in pure numpy for mirror check
            h_flipped = compute_phash64(np.fliplr(frame))

            normal_frames.append((t_ms, h_normal))
            flipped_frames.append((t_ms, h_flipped))

        duration_s = (len(normal_frames) / self.fps) if normal_frames else 0.0
        return FrameFingerprint(
            normal_frames=normal_frames,
            flipped_frames=flipped_frames,
            valid_frames_count=len(normal_frames),
            duration_s=duration_s,
        )

    def register(self, clip_id: str, fp: Any) -> None:
        """Register normal orientation frame hashes into the frame index."""
        if isinstance(fp, FrameFingerprint):
            frames = fp.normal_frames
        elif isinstance(fp, Sequence):
            frames = fp
        else:
            frames = []

        self.store.frames.add(clip_id, frames)

    def _vote_frames(
        self,
        query_frames: Sequence[tuple[int, int]],
        exclude_clip_id: str,
        max_allowed_freq: int,
    ) -> dict[str, tuple[float, float, float, float]]:
        """Run temporal offset voting against the frame index.

        Returns:
            dict mapping cand_clip_id -> (confidence, offset_s, coverage, matched_seconds)
        """
        if not query_frames:
            return {}

        valid_query_count = len(query_frames)
        # cand_clip_id -> bin_idx -> set of query t_ms
        cand_bins: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))

        for t_q_ms, hash64 in query_frames:
            # 1. Skip junk hashes (high frequency across clips)
            freq = self.store.frames.clip_frequency(hash64)
            if freq > max_allowed_freq:
                continue

            # 2. Query index within Hamming radius
            hits = self.store.frames.lookup(hash64, radius=self.radius)
            t_q_s = t_q_ms / 1000.0

            for cand_id, t_db_ms in hits:
                if cand_id == exclude_clip_id:
                    continue
                t_db_s = t_db_ms / 1000.0
                offset_s = t_db_s - t_q_s
                bin_idx = int(round(offset_s / self.offset_bin_s))
                cand_bins[cand_id][bin_idx].add(t_q_ms)

        results: dict[str, tuple[float, float, float, float]] = {}

        # 3. Add neighbor votes and evaluate best bin per candidate
        for cand_id, bins in cand_bins.items():
            best_bin = 0
            best_matched_frames: set[int] = set()

            for b, votes in bins.items():
                left = votes | bins.get(b - 1, set())
                right = votes | bins.get(b + 1, set())
                best_adjacent = left if len(left) >= len(right) else right

                if len(best_adjacent) > len(best_matched_frames):
                    best_matched_frames = best_adjacent
                    best_bin = b

            matched_count = len(best_matched_frames)
            coverage = matched_count / valid_query_count if valid_query_count > 0 else 0.0
            matched_seconds = matched_count / self.fps
            offset_s = best_bin * self.offset_bin_s

            # Confidence function: min(1, coverage / 0.6)
            base_conf = min(1.0, coverage / 0.6)
            effective_min_s = min(self.review_min_seconds, valid_query_count / self.fps)
            if matched_seconds < effective_min_s:
                confidence = min(base_conf, max(0.0, self.review_threshold - 0.01))
            else:
                confidence = base_conf

            results[cand_id] = (confidence, offset_s, coverage, matched_seconds)

        return results

    def search(self, fp: Any, exclude_clip_id: str) -> Sequence[Match]:
        """Query index with both normal and flipped frames, returning highest confidence matches."""
        if isinstance(fp, FrameFingerprint):
            normal_frames = fp.normal_frames
            flipped_frames = fp.flipped_frames
        elif isinstance(fp, Sequence):
            normal_frames = fp
            flipped_frames = []
        else:
            return []

        # Determine junk hash cutoff
        if self.max_clip_frequency is not None:
            max_allowed_freq = int(self.max_clip_frequency)
        else:
            total_clips = self.store.clips.count()
            max_allowed_freq = max(20, int(0.01 * total_clips))

        # Vote normal frames
        normal_scores = self._vote_frames(normal_frames, exclude_clip_id, max_allowed_freq)

        # Vote flipped frames for mirror check
        flipped_scores: dict[str, tuple[float, float, float, float]] = {}
        if flipped_frames:
            flipped_scores = self._vote_frames(flipped_frames, exclude_clip_id, max_allowed_freq)

        all_candidates = set(normal_scores.keys()) | set(flipped_scores.keys())
        matches: list[Match] = []

        for cand_id in all_candidates:
            norm_res = normal_scores.get(cand_id, (0.0, 0.0, 0.0, 0.0))
            flip_res = flipped_scores.get(cand_id, (0.0, 0.0, 0.0, 0.0))

            # Pick best orientation
            if flip_res[0] > norm_res[0]:
                conf, offset_s, cov, match_s = flip_res
                is_flipped = True
            else:
                conf, offset_s, cov, match_s = norm_res
                is_flipped = False

            if conf >= self.review_threshold:
                cand_clip = self.store.clips.get(cand_id)
                status = cand_clip.status if cand_clip else None
                matches.append(
                    Match(
                        detector=self.name,
                        matched_clip_id=cand_id,
                        confidence=conf,
                        evidence={
                            "offset_s": round(offset_s, 2),
                            "coverage": round(cov, 3),
                            "matched_seconds": round(match_s, 1),
                            "flipped": is_flipped,
                        },
                        matched_status=status,
                    )
                )

        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches
