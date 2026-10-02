"""SQLite storage implementation for ClipGuard."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Optional, Union

from dedupe.media.phash import (
    hamming_distance,
    hash_to_chunks,
    to_signed64,
    to_unsigned64,
)
from dedupe.models import ClipRef
from dedupe.stores.base import AudioIndex, ClipRepo, ExactIndex, FrameIndex, Store


class SqliteClipRepo(ClipRepo):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, clip: ClipRef) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO clips (clip_id, uri, local_path, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(clip_id) DO UPDATE SET
                    uri = excluded.uri,
                    local_path = excluded.local_path,
                    status = excluded.status
                """,
                (clip.clip_id, clip.uri, str(clip.local_path), clip.status, now),
            )

    def get(self, clip_id: str) -> Optional[ClipRef]:
        cur = self._conn.execute(
            "SELECT clip_id, uri, local_path, status FROM clips WHERE clip_id = ?",
            (clip_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return ClipRef(
            clip_id=row[0],
            uri=row[1],
            local_path=Path(row[2]),
            status=row[3],
        )

    def exists(self, clip_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM clips WHERE clip_id = ?", (clip_id,))
        return cur.fetchone() is not None

    def count(self, status: Optional[str] = None) -> int:
        if status is not None:
            cur = self._conn.execute("SELECT COUNT(*) FROM clips WHERE status = ?", (status,))
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM clips")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def iter_all(self) -> Iterator[ClipRef]:
        cur = self._conn.execute(
            "SELECT clip_id, uri, local_path, status FROM clips ORDER BY id ASC"
        )
        for row in cur:
            yield ClipRef(
                clip_id=row[0],
                uri=row[1],
                local_path=Path(row[2]),
                status=row[3],
            )


class SqliteExactIndex(ExactIndex):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, clip_id: str, sha256: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO exact_index (sha256, clip_id) VALUES (?, ?)",
                (sha256, clip_id),
            )

    def find(self, sha256: str) -> Sequence[str]:
        cur = self._conn.execute(
            "SELECT clip_id FROM exact_index WHERE sha256 = ?",
            (sha256,),
        )
        return [row[0] for row in cur.fetchall()]


class SqliteFrameIndex(FrameIndex):
    """Chunked pigeonhole perceptual frame index for SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, clip_id: str, frames: Sequence[tuple[int, int]]) -> None:
        rows = []
        for t_ms, h64 in frames:
            signed_h = to_signed64(h64)
            c0, c1, c2, c3 = hash_to_chunks(h64, 4)
            rows.append((clip_id, t_ms, signed_h, c0, c1, c2, c3))

        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO frame_index
                (clip_id, t_ms, hash64, chunk0, chunk1, chunk2, chunk3)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def lookup(self, hash64: int, radius: int) -> Sequence[tuple[str, int]]:
        c0, c1, c2, c3 = hash_to_chunks(hash64, 4)
        cur = self._conn.execute(
            """
            SELECT clip_id, t_ms, hash64 FROM frame_index
            WHERE chunk0 = ? OR chunk1 = ? OR chunk2 = ? OR chunk3 = ?
            """,
            (c0, c1, c2, c3),
        )

        matches: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()

        for clip_id, t_ms, signed_h in cur.fetchall():
            key = (clip_id, t_ms)
            if key in seen:
                continue
            seen.add(key)
            db_h64 = to_unsigned64(signed_h)
            if hamming_distance(hash64, db_h64) <= radius:
                matches.append(key)

        return matches

    def frame_count(self, clip_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM frame_index WHERE clip_id = ?",
            (clip_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def clip_frequency(self, hash64: int) -> int:
        signed_h = to_signed64(hash64)
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT clip_id) FROM frame_index WHERE hash64 = ?",
            (signed_h,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


class SqliteAudioIndex(AudioIndex):
    """Audio index for exact 32-bit Chromaprint sub-fingerprint lookup."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, clip_id: str, subfingerprints: Sequence[int]) -> None:
        rows = [(clip_id, pos, int(sfp)) for pos, sfp in enumerate(subfingerprints)]
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO audio_index (clip_id, pos, subfp)
                VALUES (?, ?, ?)
                """,
                rows,
            )

    def lookup(self, subfingerprint: int) -> Sequence[tuple[str, int]]:
        cur = self._conn.execute(
            "SELECT clip_id, pos FROM audio_index WHERE subfp = ?",
            (int(subfingerprint),),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]

    def get_fingerprint(self, clip_id: str) -> Sequence[int]:
        cur = self._conn.execute(
            "SELECT subfp FROM audio_index WHERE clip_id = ? ORDER BY pos ASC",
            (clip_id,),
        )
        return [row[0] for row in cur.fetchall()]


class SqliteStore(Store):
    def __init__(self, path: Union[Path, str]) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")

        self._init_schema()

        self.clips = SqliteClipRepo(self._conn)
        self.exact = SqliteExactIndex(self._conn)
        self.frames = SqliteFrameIndex(self._conn)
        self.audio = SqliteAudioIndex(self._conn)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_id TEXT UNIQUE NOT NULL,
                    uri TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS exact_index (
                    sha256 TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    PRIMARY KEY (sha256, clip_id)
                );

                CREATE INDEX IF NOT EXISTS idx_exact_sha256 ON exact_index(sha256);

                CREATE TABLE IF NOT EXISTS frame_index (
                    clip_id TEXT NOT NULL,
                    t_ms INTEGER NOT NULL,
                    hash64 INTEGER NOT NULL,
                    chunk0 INTEGER NOT NULL,
                    chunk1 INTEGER NOT NULL,
                    chunk2 INTEGER NOT NULL,
                    chunk3 INTEGER NOT NULL,
                    PRIMARY KEY (clip_id, t_ms)
                );

                CREATE INDEX IF NOT EXISTS idx_frame_chunk0 ON frame_index(chunk0);
                CREATE INDEX IF NOT EXISTS idx_frame_chunk1 ON frame_index(chunk1);
                CREATE INDEX IF NOT EXISTS idx_frame_chunk2 ON frame_index(chunk2);
                CREATE INDEX IF NOT EXISTS idx_frame_chunk3 ON frame_index(chunk3);
                CREATE INDEX IF NOT EXISTS idx_frame_clip ON frame_index(clip_id);
                CREATE INDEX IF NOT EXISTS idx_frame_hash64 ON frame_index(hash64);

                CREATE TABLE IF NOT EXISTS audio_index (
                    clip_id TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    subfp INTEGER NOT NULL,
                    PRIMARY KEY (clip_id, pos)
                );

                CREATE INDEX IF NOT EXISTS idx_audio_subfp ON audio_index(subfp);
                CREATE INDEX IF NOT EXISTS idx_audio_clip ON audio_index(clip_id);
                """
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
