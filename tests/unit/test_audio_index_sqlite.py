"""Unit tests for SQLite AudioIndex implementation."""

from __future__ import annotations

from pathlib import Path

from dedupe.stores.sqlite import SqliteStore


def test_audio_index_crud_and_lookup(tmp_path: Path) -> None:
    db_file = tmp_path / "test_audio.db"
    store = SqliteStore(db_file)

    clip_id = "clip_audio_01"
    subfps = [12345678, 23456789, 34567890, 45678901]

    # Add subfingerprints
    store.audio.add(clip_id, subfps)

    # Verify retrieval of sequence
    recovered = store.audio.get_fingerprint(clip_id)
    assert list(recovered) == subfps

    # Verify lookup by subfingerprint
    hits = store.audio.lookup(23456789)
    assert len(hits) == 1
    assert hits[0] == (clip_id, 1)  # (clip_id, pos)

    hits_missing = store.audio.lookup(99999999)
    assert len(hits_missing) == 0

    store.close()
