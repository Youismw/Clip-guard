"""PostgreSQL storage implementation for ClipGuard (Phase 6)."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from dedupe.config import StoreConfig
from dedupe.media.phash import (
    hamming_distance,
    hash_to_chunks,
    to_signed64,
    to_unsigned64,
)
from dedupe.models import ClipRef
from dedupe.stores.base import AudioIndex, ClipRepo, ExactIndex, FrameIndex, Store


class PostgresClipRepo(ClipRepo):
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def add(self, clip: ClipRef) -> None:
        now = time.time()
        local_p = str(clip.local_path) if clip.local_path is not None else ""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO clips (clip_id, uri, local_path, status, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (clip_id) DO UPDATE SET
                    uri = EXCLUDED.uri,
                    local_path = EXCLUDED.local_path,
                    status = EXCLUDED.status;
                """,
                (clip.clip_id, clip.uri, local_p, clip.status, now),
            )
        self._conn.commit()

    def get(self, clip_id: str) -> Optional[ClipRef]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT clip_id, uri, local_path, status FROM clips WHERE clip_id = %s;",
                (clip_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            loc = Path(row[2]) if row[2] else Path("")
            return ClipRef(
                clip_id=row[0],
                uri=row[1],
                local_path=loc,
                status=row[3],
            )

    def exists(self, clip_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM clips WHERE clip_id = %s;", (clip_id,))
            return cur.fetchone() is not None

    def count(self, status: Optional[str] = None) -> int:
        with self._conn.cursor() as cur:
            if status is not None:
                cur.execute("SELECT COUNT(*) FROM clips WHERE status = %s;", (status,))
            else:
                cur.execute("SELECT COUNT(*) FROM clips;")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def iter_all(self) -> Iterator[ClipRef]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT clip_id, uri, local_path, status FROM clips ORDER BY id ASC;")
            rows = cur.fetchall()
        for row in rows:
            loc = Path(row[2]) if row[2] else Path("")
            yield ClipRef(
                clip_id=row[0],
                uri=row[1],
                local_path=loc,
                status=row[3],
            )


class PostgresExactIndex(ExactIndex):
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def add(self, clip_id: str, sha256: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exact_index (sha256, clip_id)
                VALUES (%s, %s)
                ON CONFLICT (sha256) DO NOTHING;
                """,
                (sha256, clip_id),
            )
        self._conn.commit()

    def find(self, sha256: str) -> Sequence[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT clip_id FROM exact_index WHERE sha256 = %s;",
                (sha256,),
            )
            return [row[0] for row in cur.fetchall()]


class PostgresFrameIndex(FrameIndex):
    """Chunked pigeonhole perceptual frame index for PostgreSQL."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def add(self, clip_id: str, frames: Sequence[tuple[int, int]]) -> None:
        if not frames:
            return
        rows = []
        for t_ms, h64 in frames:
            signed_h = to_signed64(h64)
            c0, c1, c2, c3 = hash_to_chunks(h64, 4)
            rows.append((clip_id, t_ms, signed_h, c0, c1, c2, c3))

        with self._conn.cursor() as cur:
            try:
                from psycopg2.extras import execute_values  # type: ignore[import-untyped]

                execute_values(
                    cur,
                    """
                    INSERT INTO frame_index
                    (clip_id, t_ms, hash64, chunk0, chunk1, chunk2, chunk3)
                    VALUES %s;
                    """,
                    rows,
                )
            except (ImportError, AttributeError):
                cur.executemany(
                    """
                    INSERT INTO frame_index
                    (clip_id, t_ms, hash64, chunk0, chunk1, chunk2, chunk3)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    rows,
                )
        self._conn.commit()

    def lookup(self, hash64: int, radius: int) -> Sequence[tuple[str, int]]:
        c0, c1, c2, c3 = hash_to_chunks(hash64, 4)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT clip_id, t_ms, hash64 FROM frame_index
                WHERE chunk0 = %s OR chunk1 = %s OR chunk2 = %s OR chunk3 = %s;
                """,
                (c0, c1, c2, c3),
            )
            rows = cur.fetchall()

        matches: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()

        for clip_id, t_ms, signed_h in rows:
            key = (clip_id, t_ms)
            if key in seen:
                continue
            seen.add(key)
            db_h64 = to_unsigned64(signed_h)
            if hamming_distance(hash64, db_h64) <= radius:
                matches.append(key)

        return matches

    def frame_count(self, clip_id: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM frame_index WHERE clip_id = %s;",
                (clip_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def clip_frequency(self, hash64: int) -> int:
        signed_h = to_signed64(hash64)
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT clip_id) FROM frame_index WHERE hash64 = %s;",
                (signed_h,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


class PostgresAudioIndex(AudioIndex):
    """Audio index for 32-bit Chromaprint sub-fingerprint lookup in PostgreSQL."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def add(self, clip_id: str, subfingerprints: Sequence[int]) -> None:
        if not subfingerprints:
            return
        rows = [(clip_id, pos, int(sfp)) for pos, sfp in enumerate(subfingerprints)]
        with self._conn.cursor() as cur:
            try:
                from psycopg2.extras import execute_values

                execute_values(
                    cur,
                    "INSERT INTO audio_index (clip_id, pos, subfp) VALUES %s;",
                    rows,
                )
            except (ImportError, AttributeError):
                cur.executemany(
                    "INSERT INTO audio_index (clip_id, pos, subfp) VALUES (%s, %s, %s);",
                    rows,
                )
        self._conn.commit()

    def lookup(self, subfingerprint: int) -> Sequence[tuple[str, int]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT clip_id, pos FROM audio_index WHERE subfp = %s;",
                (int(subfingerprint),),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]

    def get_fingerprint(self, clip_id: str) -> Sequence[int]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT subfp FROM audio_index WHERE clip_id = %s ORDER BY pos ASC;",
                (clip_id,),
            )
            return [row[0] for row in cur.fetchall()]


class PostgresStore(Store):
    """Production PostgreSQL storage backend implementing the Store protocol."""

    def __init__(
        self,
        config_or_conn: Union[StoreConfig, Any] = None,
        dsn: Optional[str] = None,
    ) -> None:
        self._conn: Any = None
        if config_or_conn is not None and hasattr(config_or_conn, "cursor"):
            # Existing database connection passed directly (useful for tests/mocking)
            self._conn = config_or_conn
        else:
            import psycopg2  # type: ignore[import-untyped]

            if dsn:
                self._conn = psycopg2.connect(dsn)
            elif isinstance(config_or_conn, StoreConfig):
                self._conn = psycopg2.connect(
                    host=config_or_conn.host,
                    port=config_or_conn.port,
                    dbname=config_or_conn.database,
                    user=config_or_conn.user,
                    password=config_or_conn.password,
                    sslmode=config_or_conn.sslmode,
                )
            else:
                self._conn = psycopg2.connect(
                    host="localhost",
                    port=5432,
                    dbname="clipguard",
                    user="postgres",
                )

        self._init_schema()

        self.clips = PostgresClipRepo(self._conn)
        self.exact = PostgresExactIndex(self._conn)
        self.frames = PostgresFrameIndex(self._conn)
        self.audio = PostgresAudioIndex(self._conn)

    def _init_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS clips (
                    id SERIAL PRIMARY KEY,
                    clip_id VARCHAR(64) UNIQUE NOT NULL,
                    uri TEXT NOT NULL,
                    local_path TEXT,
                    status VARCHAR(32) NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                );

                CREATE TABLE IF NOT EXISTS exact_index (
                    sha256 VARCHAR(64) PRIMARY KEY,
                    clip_id VARCHAR(64) NOT NULL
                );

                CREATE TABLE IF NOT EXISTS frame_index (
                    id BIGSERIAL PRIMARY KEY,
                    clip_id VARCHAR(64) NOT NULL,
                    t_ms INTEGER NOT NULL,
                    hash64 BIGINT NOT NULL,
                    chunk0 INTEGER NOT NULL,
                    chunk1 INTEGER NOT NULL,
                    chunk2 INTEGER NOT NULL,
                    chunk3 INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pg_frame_c0 ON frame_index(chunk0);
                CREATE INDEX IF NOT EXISTS idx_pg_frame_c1 ON frame_index(chunk1);
                CREATE INDEX IF NOT EXISTS idx_pg_frame_c2 ON frame_index(chunk2);
                CREATE INDEX IF NOT EXISTS idx_pg_frame_c3 ON frame_index(chunk3);
                CREATE INDEX IF NOT EXISTS idx_pg_frame_cid ON frame_index(clip_id);
                CREATE INDEX IF NOT EXISTS idx_pg_frame_h64 ON frame_index(hash64);

                CREATE TABLE IF NOT EXISTS audio_index (
                    id BIGSERIAL PRIMARY KEY,
                    clip_id VARCHAR(64) NOT NULL,
                    pos INTEGER NOT NULL,
                    subfp BIGINT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pg_audio_sfp ON audio_index(subfp);
                CREATE INDEX IF NOT EXISTS idx_pg_audio_cid ON audio_index(clip_id);

                CREATE TABLE IF NOT EXISTS verdicts (
                    clip_id VARCHAR(64) PRIMARY KEY,
                    verdict VARCHAR(32) NOT NULL,
                    layer VARCHAR(32),
                    intent VARCHAR(64),
                    reason TEXT,
                    verdict_json TEXT,
                    created_at DOUBLE PRECISION
                );
                """
            )
        self._conn.commit()

    def record_verdict(
        self,
        clip_id: str,
        verdict: str,
        layer: str,
        intent: str,
        reason: str,
        verdict_json: str,
    ) -> None:
        """Write verdict to production results table."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO verdicts
                (clip_id, verdict, layer, intent, reason, verdict_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (clip_id) DO UPDATE SET
                    verdict = EXCLUDED.verdict,
                    layer = EXCLUDED.layer,
                    intent = EXCLUDED.intent,
                    reason = EXCLUDED.reason,
                    verdict_json = EXCLUDED.verdict_json,
                    created_at = EXCLUDED.created_at;
                """,
                (clip_id, verdict, layer, intent, reason, verdict_json, time.time()),
            )
        self._conn.commit()

    def close(self) -> None:
        if self._conn and not getattr(self._conn, "closed", False):
            self._conn.close()
