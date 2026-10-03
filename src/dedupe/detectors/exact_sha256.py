"""Exact SHA-256 detector for byte-identical duplicates."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from dedupe.detectors.base import Detector
from dedupe.models import ClipRef, Match
from dedupe.registry import register_detector
from dedupe.stores.base import Store

CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunk


@register_detector("exact_sha256")
class ExactSha256Detector(Detector):
    """Detector for exact byte-identical copies using streaming SHA-256."""

    name: ClassVar[str] = "exact_sha256"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: Mapping[str, Any], store: Store) -> None:
        super().__init__(params, store)
        self.flag_threshold = float(params.get("flag_threshold", 1.0))
        self.review_threshold = float(params.get("review_threshold", 1.0))

    def requirements(self) -> Sequence[str]:
        return []

    def extract(self, clip: ClipRef) -> str:
        """Stream file in 1 MiB chunks and return hex SHA-256 (reuses clip.clip_id if already computed)."""
        if (
            isinstance(clip.clip_id, str)
            and len(clip.clip_id) == 64
            and all(c in "0123456789abcdefABCDEF" for c in clip.clip_id)
        ):
            return clip.clip_id.lower()

        h = hashlib.sha256()
        with clip.local_path.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def search(self, fp: Any, exclude_clip_id: str) -> Sequence[Match]:
        """Query exact index for matching clip_ids excluding query clip itself."""
        sha256_str = str(fp)
        matches: list[Match] = []
        matching_ids = self.store.exact.find(sha256_str)

        for matched_id in matching_ids:
            if matched_id == exclude_clip_id:
                continue

            matched_clip = self.store.clips.get(matched_id)
            status = matched_clip.status if matched_clip else None

            matches.append(
                Match(
                    detector=self.name,
                    matched_clip_id=matched_id,
                    confidence=1.0,
                    evidence={"matched_sha256": sha256_str},
                    matched_status=status,
                )
            )

        return matches

    def register(self, clip_id: str, fp: Any) -> None:
        """Add hash to index (idempotent)."""
        self.store.exact.add(clip_id, str(fp))
