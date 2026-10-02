"""Unit tests for FramePhashDetector: offset voting, mirror checking, and junk-hash filtering."""

from __future__ import annotations

from pathlib import Path

from dedupe.detectors.frame_phash import FrameFingerprint, FramePhashDetector
from dedupe.models import ClipRef
from dedupe.stores.sqlite import SqliteStore


def test_offset_voting_finds_known_offset(tmp_path: Path) -> None:
    """Offset voting recovers the exact known temporal offset between query and indexed clip."""
    db_file = tmp_path / "voting.db"
    store = SqliteStore(db_file)

    detector = FramePhashDetector(
        params={
            "fps": 1.0,
            "radius": 3,
            "offset_bin_s": 2.0,
            "flag_threshold": 0.80,
            "review_threshold": 0.40,
            "review_min_seconds": 4.0,
        },
        store=store,
    )

    # Database clip has 10 frames from t = 0s to 9s
    # Distinct hashes per frame
    indexed_hashes = [0x1000000000000000 * (i + 1) for i in range(10)]
    db_frames = [(i * 1000, indexed_hashes[i]) for i in range(10)]

    orig_clip = ClipRef(
        clip_id="orig_123",
        uri="file:///orig.mp4",
        local_path=Path("orig.mp4"),
        status="approved",
    )
    store.clips.add(orig_clip)
    detector.register(orig_clip.clip_id, db_frames)

    # Query clip is trimmed from head by 3 seconds:
    # Query frame at t=0s corresponds to DB frame at t=3s (offset = +3.0s)
    # Query has 6 frames (frames 3 to 8 of original)
    query_frames = [(j * 1000, indexed_hashes[j + 3]) for j in range(6)]
    query_fp = FrameFingerprint(
        normal_frames=query_frames,
        flipped_frames=[],
        valid_frames_count=6,
        duration_s=6.0,
    )

    matches = detector.search(query_fp, exclude_clip_id="query_456")

    assert len(matches) == 1
    match = matches[0]
    assert match.matched_clip_id == "orig_123"
    assert match.detector == "frame_phash"
    assert match.confidence >= 0.80  # Full overlap of query frames
    assert not match.evidence["flipped"]
    # Offset bin: offset = 3.0s, bin = round(3.0 / 2.0) * 2.0 = 2.0s or 4.0s
    assert abs(match.evidence["offset_s"] - 3.0) <= 2.0
    assert match.evidence["matched_seconds"] >= 4.0

    store.close()


def test_mirror_check_detects_flipped_video(tmp_path: Path) -> None:
    """When a query clip is horizontally flipped, detector matches with flipped: True."""
    db_file = tmp_path / "mirror.db"
    store = SqliteStore(db_file)

    detector = FramePhashDetector(
        params={
            "fps": 1.0,
            "radius": 3,
            "flag_threshold": 0.80,
            "review_threshold": 0.40,
            "review_min_seconds": 4.0,
        },
        store=store,
    )

    orig_hashes = [0x2000000000000000 * (i + 1) for i in range(8)]
    db_frames = [(i * 1000, orig_hashes[i]) for i in range(8)]

    orig_clip = ClipRef(
        clip_id="orig_mirror",
        uri="file:///orig.mp4",
        local_path=Path("orig.mp4"),
        status="approved",
    )
    store.clips.add(orig_clip)
    detector.register(orig_clip.clip_id, db_frames)

    # For query, normal orientation has unrelated hashes,
    # but flipped_frames has the original matching hashes
    unrelated_hashes = [0x9999999999999999 ^ i for i in range(8)]
    normal_query = [(i * 1000, unrelated_hashes[i]) for i in range(8)]
    flipped_query = [(i * 1000, orig_hashes[i]) for i in range(8)]

    query_fp = FrameFingerprint(
        normal_frames=normal_query,
        flipped_frames=flipped_query,
        valid_frames_count=8,
        duration_s=8.0,
    )

    matches = detector.search(query_fp, exclude_clip_id="query_mirror")
    assert len(matches) == 1
    match = matches[0]
    assert match.matched_clip_id == "orig_mirror"
    assert match.evidence["flipped"] is True
    assert match.confidence >= 0.80

    store.close()


def test_junk_hash_filtering(tmp_path: Path) -> None:
    """Hashes that appear in many clips exceed max_clip_frequency and are ignored in search."""
    db_file = tmp_path / "junk.db"
    store = SqliteStore(db_file)

    detector = FramePhashDetector(
        params={
            "fps": 1.0,
            "radius": 3,
            "max_clip_frequency": 2,  # Any hash in > 2 clips is junk
            "review_min_seconds": 2.0,
        },
        store=store,
    )

    junk_hash = 0x5555555555555555

    # Index junk_hash into 3 different clips
    for c_id in ["clipA", "clipB", "clipC"]:
        store.clips.add(ClipRef(c_id, f"file:///{c_id}", Path(c_id), "approved"))
        detector.register(c_id, [(0, junk_hash)])

    assert store.frames.clip_frequency(junk_hash) == 3

    # Query with junk_hash only
    query_fp = FrameFingerprint(
        normal_frames=[(0, junk_hash), (1000, junk_hash), (2000, junk_hash)],
        flipped_frames=[],
        valid_frames_count=3,
        duration_s=3.0,
    )

    matches = detector.search(query_fp, exclude_clip_id="query_junk")
    # All frames had junk_hash which exceeded max_clip_frequency -> 0 matches returned
    assert len(matches) == 0

    store.close()
