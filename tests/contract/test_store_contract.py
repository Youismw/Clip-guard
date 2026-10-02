"""Contract tests verifying all Store implementations conform to identical behavior."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from dedupe.models import ClipRef
from dedupe.stores.base import Store
from dedupe.stores.postgres import PostgresStore
from dedupe.stores.sqlite import SqliteStore


class StoreContractSuite:
    """Reusable contract test suite executed against every Store implementation."""

    @staticmethod
    def assert_store_contract(store: Store) -> None:
        # 1. ClipRepo CRUD & Idempotency
        c1 = ClipRef(
            clip_id="11" * 32,
            uri="file:///data/clip1.mp4",
            local_path=Path("/data/clip1.mp4"),
            status="approved",
        )
        c2 = ClipRef(
            clip_id="22" * 32,
            uri="file:///data/clip2.mp4",
            local_path=Path("/data/clip2.mp4"),
            status="pending",
        )

        assert not store.clips.exists(c1.clip_id)
        assert store.clips.get(c1.clip_id) is None
        assert store.clips.count() == 0

        # Add clips
        store.clips.add(c1)
        store.clips.add(c2)

        assert store.clips.exists(c1.clip_id)
        assert store.clips.exists(c2.clip_id)
        assert store.clips.count() == 2
        assert store.clips.count(status="approved") == 1
        assert store.clips.count(status="pending") == 1

        fetched1 = store.clips.get(c1.clip_id)
        assert fetched1 is not None
        assert fetched1.clip_id == c1.clip_id
        assert fetched1.status == "approved"

        # Idempotency: re-adding with updated status should update, not error or duplicate
        c1_updated = ClipRef(
            clip_id="11" * 32,
            uri="file:///data/clip1_new.mp4",
            local_path=Path("/data/clip1_new.mp4"),
            status="rejected",
        )
        store.clips.add(c1_updated)
        assert store.clips.count() == 2
        fetched1_up = store.clips.get(c1.clip_id)
        assert fetched1_up is not None
        assert fetched1_up.status == "rejected"

        all_clips = list(store.clips.iter_all())
        assert len(all_clips) == 2

        # 2. ExactIndex Tests
        sha_a = "aa" * 32
        store.exact.add(c1.clip_id, sha_a)
        # Idempotent re-add
        store.exact.add(c1.clip_id, sha_a)

        found = store.exact.find(sha_a)
        assert len(found) == 1
        assert found[0] == c1.clip_id
        assert store.exact.find("non_existent_sha") == []

        # 3. FrameIndex Tests (including top-bit set hash and radius lookup)
        # Hash with top bit set: 0xF000000000000001
        top_bit_hash = 0xF000000000000001
        # Similar hash with 1 bit difference (Hamming distance 1 <= radius 3)
        similar_hash = 0xF000000000000003
        # Completely different hash
        different_hash = 0x00000000FFFFFFFF

        frames_c1 = [
            (0, top_bit_hash),
            (1000, 0x1234567812345678),
            (2000, 0x9999999999999999),
        ]
        store.frames.add(c1.clip_id, frames_c1)
        assert store.frames.frame_count(c1.clip_id) == 3

        # Lookup top_bit_hash directly (distance 0)
        hits_exact = store.frames.lookup(top_bit_hash, radius=3)
        assert (c1.clip_id, 0) in hits_exact

        # Lookup similar_hash (distance 1 <= 3)
        hits_similar = store.frames.lookup(similar_hash, radius=3)
        assert (c1.clip_id, 0) in hits_similar

        # Lookup different_hash (distance > 3) -> should not match (c1, 0)
        hits_diff = store.frames.lookup(different_hash, radius=3)
        assert (c1.clip_id, 0) not in hits_diff

        # Clip frequency count
        assert store.frames.clip_frequency(top_bit_hash) == 1
        assert store.frames.clip_frequency(0xAAAAAAAAAAAAAAAA) == 0

        # 4. AudioIndex Tests
        subfps = [123456, 789012, 345678]
        store.audio.add(c1.clip_id, subfps)

        # Lookup subfingerprint
        hits_audio = store.audio.lookup(123456)
        assert len(hits_audio) >= 1
        assert (c1.clip_id, 0) in hits_audio

        # Get full sequence
        retrieved_audio = store.audio.get_fingerprint(c1.clip_id)
        assert list(retrieved_audio) == subfps


def test_sqlite_store_contract(tmp_path: Path) -> None:
    db_file = tmp_path / "contract_test.db"
    store = SqliteStore(db_file)
    try:
        StoreContractSuite.assert_store_contract(store)
    finally:
        store.close()


class MockPostgresCursor:
    """Cursor simulating Postgres cursor behavior on an in-memory SQLite backend."""

    def __init__(self, raw_conn: sqlite3.Connection) -> None:
        self._conn = raw_conn
        self._cur = raw_conn.cursor()

    def __enter__(self) -> MockPostgresCursor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._cur.close()

    def _convert_query(self, query: str) -> str:
        # Convert Postgres %s to SQLite ?
        q = re.sub(r"%s", "?", query)
        # Convert SERIAL / BIGSERIAL to INTEGER
        q = re.sub(r"\b(BIG)?SERIAL\b", "INTEGER", q, flags=re.IGNORECASE)
        q = re.sub(r"\bDOUBLE PRECISION\b", "REAL", q, flags=re.IGNORECASE)
        q = re.sub(r"\bVARCHAR\(\d+\)", "TEXT", q, flags=re.IGNORECASE)
        # Convert ON CONFLICT (col) DO NOTHING to OR IGNORE
        if "ON CONFLICT" in q and "DO NOTHING" in q:
            q = re.sub(r"INSERT INTO", "INSERT OR IGNORE INTO", q, flags=re.IGNORECASE)
            q = re.sub(r"ON CONFLICT\s*\([^)]+\)\s*DO NOTHING", "", q, flags=re.IGNORECASE)
        # Convert ON CONFLICT (col) DO UPDATE to ON CONFLICT(col) DO UPDATE
        q = re.sub(
            r"ON CONFLICT\s*\(([a-zA-Z0-9_]+)\)\s*DO UPDATE SET",
            r"ON CONFLICT(\1) DO UPDATE SET",
            q,
        )
        return q

    def execute(self, query: str, params: Any = None) -> Any:
        converted = self._convert_query(query)
        if ";" in converted.strip().rstrip(";"):
            # Multi-statement script
            return self._conn.executescript(converted)
        if params is None:
            return self._cur.execute(converted)
        return self._cur.execute(converted, params)

    def executemany(self, query: str, param_seq: Any) -> Any:
        converted = self._convert_query(query)
        return self._cur.executemany(converted, param_seq)

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> Any:
        return self._cur.fetchall()


class MockPostgresConnection:
    """Connection conforming to psycopg2 API backed by in-memory SQLite for contract testing."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)

    def cursor(self) -> MockPostgresCursor:
        return MockPostgresCursor(self._conn)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def test_postgres_store_contract() -> None:
    """Verify PostgresStore implements the Store contract identically."""
    mock_conn = MockPostgresConnection()
    pg_store = PostgresStore(config_or_conn=mock_conn)
    try:
        StoreContractSuite.assert_store_contract(pg_store)
    finally:
        pg_store.close()
