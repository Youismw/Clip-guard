"""Table-driven unit tests for Combiner policies and rules (Section 6)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from dedupe.combiner import Combiner
from dedupe.models import ClipRef, DetectorResult, Match


@pytest.fixture
def sample_clip() -> ClipRef:
    return ClipRef(
        clip_id="a" * 64,
        uri="file:///test/clip_a.mp4",
        local_path=Path("/test/clip_a.mp4"),
        status="pending",
    )


def test_already_indexed(sample_clip: ClipRef) -> None:
    combiner = Combiner(policy="any")
    verdict = combiner.combine(sample_clip, [], is_already_indexed=True)
    assert verdict.verdict == "already_indexed"
    assert "already indexed" in verdict.reason


def test_all_detectors_error(sample_clip: ClipRef) -> None:
    combiner = Combiner(policy="any")
    results = [
        DetectorResult(
            detector="exact_sha256",
            version="1.0.0",
            matches=[],
            elapsed_ms=10,
            error="IO Error",
        ),
        DetectorResult(
            detector="frame_phash",
            version="1.0.0",
            matches=[],
            elapsed_ms=50,
            error="Decoder crashed",
        ),
    ]
    verdict = combiner.combine(sample_clip, results)
    assert verdict.verdict == "error"
    assert "All detectors failed" in verdict.reason


def test_partial_error_uses_remaining_detectors(sample_clip: ClipRef) -> None:
    combiner = Combiner(policy="any", thresholds={"exact_sha256": (1.0, 1.0)})
    results = [
        DetectorResult(
            detector="exact_sha256",
            version="1.0.0",
            matches=[
                Match(
                    detector="exact_sha256",
                    matched_clip_id="b" * 64,
                    confidence=1.0,
                    evidence={},
                )
            ],
            elapsed_ms=10,
            error=None,
        ),
        DetectorResult(
            detector="frame_phash",
            version="1.0.0",
            matches=[],
            elapsed_ms=50,
            error="Decode failure",
        ),
    ]
    verdict = combiner.combine(sample_clip, results)
    assert verdict.verdict == "duplicate"
    assert len(verdict.matches) == 1
    assert verdict.detectors[1]["error"] == "Decode failure"


@pytest.mark.parametrize(
    ("conf", "flag_th", "rev_th", "expected_verdict"),
    [
        (1.0, 0.80, 0.40, "duplicate"),
        (0.80, 0.80, 0.40, "duplicate"),
        (0.79, 0.80, 0.40, "review"),
        (0.40, 0.80, 0.40, "review"),
        (0.39, 0.80, 0.40, "clear"),
        (0.0, 0.80, 0.40, "clear"),
    ],
)
def test_policy_any_threshold_table(
    sample_clip: ClipRef,
    conf: float,
    flag_th: float,
    rev_th: float,
    expected_verdict: str,
) -> None:
    combiner = Combiner(policy="any", thresholds={"test_det": (flag_th, rev_th)})
    matches: Sequence[Match] = (
        [
            Match(
                detector="test_det",
                matched_clip_id="b" * 64,
                confidence=conf,
                evidence={},
            )
        ]
        if conf > 0
        else []
    )
    results = [
        DetectorResult(
            detector="test_det",
            version="1.0.0",
            matches=matches,
            elapsed_ms=25,
            error=None,
        )
    ]
    verdict = combiner.combine(sample_clip, results)
    assert verdict.verdict == expected_verdict


def test_policy_corroborate_upgrades_review_to_duplicate(sample_clip: ClipRef) -> None:
    combiner = Combiner(
        policy="corroborate",
        thresholds={
            "frame_phash": (0.80, 0.40),
            "audio_chromaprint": (0.80, 0.50),
        },
    )
    # Video detector yields review confidence 0.55 on clip B
    # Audio detector also matches clip B with confidence 0.70
    results = [
        DetectorResult(
            detector="frame_phash",
            version="1.0.0",
            matches=[
                Match(
                    detector="frame_phash",
                    matched_clip_id="b" * 64,
                    confidence=0.55,
                    evidence={},
                )
            ],
            elapsed_ms=100,
        ),
        DetectorResult(
            detector="audio_chromaprint",
            version="1.0.0",
            matches=[
                Match(
                    detector="audio_chromaprint",
                    matched_clip_id="b" * 64,
                    confidence=0.70,
                    evidence={},
                )
            ],
            elapsed_ms=80,
        ),
    ]
    verdict = combiner.combine(sample_clip, results)
    assert verdict.verdict == "duplicate"
    assert "Corroborated" in verdict.reason


def test_policy_weighted(sample_clip: ClipRef) -> None:
    combiner = Combiner(
        policy="weighted",
        weights={"det_a": 0.6, "det_b": 0.4},
        global_flag_threshold=0.75,
        global_review_threshold=0.45,
    )
    # Score on clip B = 0.6 * 0.9 + 0.4 * 0.8 = 0.54 + 0.32 = 0.86 >= 0.75 -> duplicate
    results = [
        DetectorResult(
            detector="det_a",
            version="1.0.0",
            matches=[
                Match(detector="det_a", matched_clip_id="b" * 64, confidence=0.9, evidence={})
            ],
            elapsed_ms=10,
        ),
        DetectorResult(
            detector="det_b",
            version="1.0.0",
            matches=[
                Match(detector="det_b", matched_clip_id="b" * 64, confidence=0.8, evidence={})
            ],
            elapsed_ms=15,
        ),
    ]
    verdict = combiner.combine(sample_clip, results)
    assert verdict.verdict == "duplicate"
    assert "Weighted score 0.86" in verdict.reason


def test_policy_corroborate_different_clips_remains_review(sample_clip: ClipRef) -> None:
    """If video matches clip B but audio matches clip C, do not upgrade to duplicate."""
    combiner = Combiner(
        policy="corroborate",
        thresholds={
            "frame_phash": (0.80, 0.40),
            "audio_chromaprint": (0.80, 0.50),
        },
    )
    results = [
        DetectorResult(
            detector="frame_phash",
            version="1.0.0",
            matches=[
                Match(
                    detector="frame_phash",
                    matched_clip_id="b" * 64,
                    confidence=0.55,
                    evidence={},
                )
            ],
            elapsed_ms=100,
        ),
        DetectorResult(
            detector="audio_chromaprint",
            version="1.0.0",
            matches=[
                Match(
                    detector="audio_chromaprint",
                    matched_clip_id="c" * 64,
                    confidence=0.70,
                    evidence={},
                )
            ],
            elapsed_ms=80,
        ),
    ]
    verdict = combiner.combine(sample_clip, results)
    # Stays review because matched clip IDs differ
    assert verdict.verdict == "review"
    assert "Corroborated" not in verdict.reason


def test_policy_weighted_review_and_clear(sample_clip: ClipRef) -> None:
    combiner = Combiner(
        policy="weighted",
        weights={"det_a": 0.5, "det_b": 0.5},
        global_flag_threshold=0.80,
        global_review_threshold=0.40,
    )
    # 0.5 * 0.5 + 0.5 * 0.6 = 0.55 -> review
    results_rev = [
        DetectorResult(
            detector="det_a",
            version="1.0.0",
            matches=[
                Match(detector="det_a", matched_clip_id="b" * 64, confidence=0.5, evidence={})
            ],
            elapsed_ms=10,
        ),
        DetectorResult(
            detector="det_b",
            version="1.0.0",
            matches=[
                Match(detector="det_b", matched_clip_id="b" * 64, confidence=0.6, evidence={})
            ],
            elapsed_ms=15,
        ),
    ]
    verdict_rev = combiner.combine(sample_clip, results_rev)
    assert verdict_rev.verdict == "review"

    # 0.5 * 0.2 + 0.5 * 0.2 = 0.20 -> clear
    results_clear = [
        DetectorResult(
            detector="det_a",
            version="1.0.0",
            matches=[
                Match(detector="det_a", matched_clip_id="b" * 64, confidence=0.2, evidence={})
            ],
            elapsed_ms=10,
        )
    ]
    verdict_clear = combiner.combine(sample_clip, results_clear)
    assert verdict_clear.verdict == "clear"
