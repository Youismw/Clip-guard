"""Unit tests for SQLite Store and indexes."""

from __future__ import annotations

from pathlib import Path

from dedupe.models import ClipRef
from dedupe.stores.sqlite import SqliteStore


def test_sqlite_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "test_wal.db"
    store = SqliteStore(db_path)
    cur = store._conn.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"
    store.close()


def test_sqlite_clip_repo_crud(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test_repo.db")
    clip1 = ClipRef(
        clip_id="1" * 64,
        uri="file:///data/clip1.mp4",
        local_path=Path("/data/clip1.mp4"),
        status="approved",
    )
    clip2 = ClipRef(
        clip_id="2" * 64,
        uri="file:///data/clip2.mp4",
        local_path=Path("/data/clip2.mp4"),
        status="pending",
    )

    # Add and retrieve
    store.clips.add(clip1)
    store.clips.add(clip2)

    assert store.clips.exists(clip1.clip_id) is True
    assert store.clips.exists("3" * 64) is False

    retrieved = store.clips.get(clip1.clip_id)
    assert retrieved is not None
    assert retrieved.clip_id == clip1.clip_id
    assert retrieved.status == "approved"

    # Count
    assert store.clips.count() == 2
    assert store.clips.count(status="approved") == 1
    assert store.clips.count(status="pending") == 1
    assert store.clips.count(status="rejected") == 0

    # Iteration
    all_clips = list(store.clips.iter_all())
    assert len(all_clips) == 2
    assert {c.clip_id for c in all_clips} == {clip1.clip_id, clip2.clip_id}

    # Idempotent update
    clip1_updated = ClipRef(
        clip_id="1" * 64,
        uri="file:///data/clip1_moved.mp4",
        local_path=Path("/data/clip1_moved.mp4"),
        status="rejected",
    )
    store.clips.add(clip1_updated)
    assert store.clips.count() == 2
    assert store.clips.get(clip1.clip_id).status == "rejected"

    store.close()


def test_sqlite_exact_index(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "test_exact.db")
    sha = "abc" * 20 + "abcd"

    # Add and find
    store.exact.add("clip_a", sha)
    store.exact.add("clip_b", sha)
    # Idempotent re-add
    store.exact.add("clip_a", sha)

    matches = store.exact.find(sha)
    assert sorted(matches) == ["clip_a", "clip_b"]

    assert store.exact.find("nonexistent") == []
    store.close()
