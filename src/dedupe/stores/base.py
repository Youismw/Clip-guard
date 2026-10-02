"""Abstract interfaces and protocols for storage backends."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Optional, Protocol

from dedupe.models import ClipRef


class ExactIndex(Protocol):
    def add(self, clip_id: str, sha256: str) -> None: ...
    def find(self, sha256: str) -> Sequence[str]: ...


class FrameIndex(Protocol):
    def add(self, clip_id: str, frames: Sequence[tuple[int, int]]) -> None: ...  # (t_ms, hash64)
    def lookup(self, hash64: int, radius: int) -> Sequence[tuple[str, int]]: ...  # (clip_id, t_ms)
    def frame_count(self, clip_id: str) -> int: ...
    def clip_frequency(self, hash64: int) -> int: ...


class AudioIndex(Protocol):
    def add(self, clip_id: str, subfingerprints: Sequence[int]) -> None: ...
    def lookup(self, subfingerprint: int) -> Sequence[tuple[str, int]]: ...  # (clip_id, position)
    def get_fingerprint(self, clip_id: str) -> Sequence[int]: ...


class ClipRepo(Protocol):
    def add(self, clip: ClipRef) -> None: ...
    def get(self, clip_id: str) -> Optional[ClipRef]: ...
    def exists(self, clip_id: str) -> bool: ...
    def count(self, status: Optional[str] = None) -> int: ...
    def iter_all(self) -> Iterator[ClipRef]: ...


class Store(Protocol):
    clips: ClipRepo
    exact: ExactIndex
    frames: FrameIndex
    audio: AudioIndex

    def close(self) -> None: ...
