"""Unit tests for ExactSha256Detector."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dedupe.detectors.exact_sha256 import ExactSha256Detector
from dedupe.models import ClipRef
from dedupe.stores.sqlite import SqliteStore


def test_exact_sha256_detector(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test_detector.db")
    detector = ExactSha256Detector(params={}, store=store)

    assert detector.requirements() == []

    # Create dummy files
    f1 = tmp_path / "clip1.mp4"
    f1.write_bytes(b"video content bytes for clip 1")
    expected_sha1 = hashlib.sha256(b"video content bytes for clip 1").hexdigest()

    f2 = tmp_path / "clip2.mp4"
    f2.write_bytes(b"video content bytes for clip 2 (different)")

    clip1 = ClipRef(
        clip_id=expected_sha1,
        uri=f1.as_uri(),
        local_path=f1,
        status="approved",
    )
    store.clips.add(clip1)

    # Extract
    fp1 = detector.extract(clip1)
    assert fp1 == expected_sha1

    # Search before register -> empty
    matches = detector.search(fp1, exclude_clip_id=clip1.clip_id)
    assert matches == []

    # Register clip1
    detector.register(clip1.clip_id, fp1)

    # Search with exclude_clip_id = clip1 -> still empty (never returns self)
    matches_self = detector.search(fp1, exclude_clip_id=clip1.clip_id)
    assert matches_self == []

    # Query with another clip having the exact same bytes (renamed copy)
    matches_other = detector.search(fp1, exclude_clip_id="other_clip_id")
    assert len(matches_other) == 1
    assert matches_other[0].matched_clip_id == clip1.clip_id
    assert matches_other[0].confidence == 1.0
    assert matches_other[0].matched_status == "approved"

    store.close()
