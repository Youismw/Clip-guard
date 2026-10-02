"""Abstract source protocol for reading clips."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from dedupe.models import ClipRef


class ClipSource(Protocol):
    """Protocol for abstracting clip sources (local disk, S3, etc.)."""

    def iter_clips(self) -> Iterator[ClipRef]: ...

    def materialize(self, uri: str) -> AbstractContextManager[Path]:
        """Yield a local path to the video file, cleaning up on exit."""
        ...
