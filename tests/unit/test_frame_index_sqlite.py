"""Unit tests for SQLite chunked pigeonhole FrameIndex implementation."""

from __future__ import annotations

from pathlib import Path

from dedupe.media.phash import hamming_distance
from dedupe.stores.sqlite import SqliteStore


def test_frame_index_top_bit_roundtrip(tmp_path: Path) -> None:
    """Hashes with top bit set (>= 2^63) correctly round-trip and match in SQLite lookup."""
    db_file = tmp_path / "test_frames.db"
    store = SqliteStore(db_file)

    # Top bit set hash
    h_top_bit = (1 << 63) | 0x12345678ABCD
    clip_id = "clip_top_bit_001"

    # Insert frame at t = 1000 ms
    store.frames.add(clip_id, [(1000, h_top_bit)])

    # Exact lookup
    hits = store.frames.lookup(h_top_bit, radius=0)
    assert len(hits) == 1
    assert hits[0] == (clip_id, 1000)

    # Lookup with 2 bit flips (distance 2 <= radius 3)
    h_nearby = h_top_bit ^ 0b11
    assert hamming_distance(h_top_bit, h_nearby) == 2
    hits_nearby = store.frames.lookup(h_nearby, radius=3)
    assert len(hits_nearby) == 1
    assert hits_nearby[0] == (clip_id, 1000)

    # Lookup with radius 1 (distance 2 > 1) -> must NOT match
    hits_far = store.frames.lookup(h_nearby, radius=1)
    assert len(hits_far) == 0

    store.close()


def test_frame_index_metrics_and_frequency(tmp_path: Path) -> None:
    """frame_count and clip_frequency properly count clips in SQLite."""
    db_file = tmp_path / "test_freq.db"
    store = SqliteStore(db_file)

    common_hash = 0xAAAAAAAAAAAAAAAA  # Top bit set
    unique_hash1 = 0x1111111111111111
    unique_hash2 = 0x2222222222222222

    # Add clip1 with common_hash and unique_hash1
    store.frames.add("clip1", [(0, common_hash), (1000, unique_hash1)])
    # Add clip2 with common_hash and unique_hash2
    store.frames.add("clip2", [(0, common_hash), (1000, unique_hash2)])

    assert store.frames.frame_count("clip1") == 2
    assert store.frames.frame_count("clip2") == 2
    assert store.frames.frame_count("non_existent") == 0

    # common_hash appears in 2 distinct clips
    assert store.frames.clip_frequency(common_hash) == 2
    # unique hashes appear in 1 clip
    assert store.frames.clip_frequency(unique_hash1) == 1
    assert store.frames.clip_frequency(unique_hash2) == 1
    assert store.frames.clip_frequency(0x9999999999999999) == 0

    store.close()
