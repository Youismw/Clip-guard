"""Domain models for ClipGuard duplicate video detection."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ClipRef:
    """Reference to a video clip with stable identity and storage location."""

    clip_id: str  # sha256 of the file bytes (stable identity)
    uri: str  # file:///..., later s3://bucket/key
    local_path: Path = Path("")  # materialized path used for decoding
    status: str = "approved"  # "approved" | "rejected" | "pending"


@dataclass(frozen=True)
class Match:
    """Individual match between a query clip and an indexed clip."""

    detector: str
    matched_clip_id: str
    confidence: float  # 0.0 - 1.0
    evidence: Mapping[str, Any]  # e.g. {"offset_s": 1.0, "matched_seconds": 42, "flipped": False}
    matched_status: Optional[str] = None


@dataclass(frozen=True)
class DetectorResult:
    """Result of running a single detector on a clip."""

    detector: str
    version: str
    matches: Sequence[Match]
    elapsed_ms: int
    error: Optional[str] = None


@dataclass(frozen=True)
class Verdict:
    """Consolidated verdict for a checked clip (Schema Version 1)."""

    schema_version: int
    clip_id: str
    uri: str
    verdict: str  # "clear" | "review" | "duplicate" | "already_indexed" | "error"
    reason: str
    matches: Sequence[Match]
    detectors: Sequence[Mapping[str, Any]]
    layer: Optional[str] = None  # "exact" | "visual" | "audio" | "none"
    intent_assessment: Optional[str] = (
        None  # "unedited_likely_unaware" | "edited_likely_intentional" | "clean"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "clip_id": self.clip_id,
            "uri": self.uri,
            "verdict": self.verdict,
            "layer": self.layer,
            "intent_assessment": self.intent_assessment,
            "reason": self.reason,
            "matches": [asdict(m) for m in self.matches],
            "detectors": [dict(d) for d in self.detectors],
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
