"""Local filesystem clip source implementation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Union
from urllib.parse import unquote, urlparse

from dedupe.models import ClipRef
from dedupe.sources.base import ClipSource

CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks
KNOWN_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".flv",
    ".wmv",
}


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 of a file using 1 MiB streaming chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def clip_from_path(path: Path, status: str = "approved") -> ClipRef:
    """Create a ClipRef from a local file path."""
    resolved = path.resolve()
    sha = compute_file_sha256(resolved)
    return ClipRef(
        clip_id=sha,
        uri=resolved.as_uri(),
        local_path=resolved,
        status=status,
    )


class LocalSource(ClipSource):
    """Source that iterates over clips on local disk and materializes them."""

    def __init__(
        self,
        path: Union[Path, str],
        status: str = "approved",
        recursive: bool = True,
    ) -> None:
        self.root = Path(path).resolve()
        self.status = status
        self.recursive = recursive

    def iter_paths(self) -> Iterator[Path]:
        """Iterate over candidate video file paths on local disk without hashing."""
        if not self.root.exists():
            return

        if self.root.is_file():
            yield self.root
            return

        pattern = "**/*" if self.recursive else "*"
        for item in sorted(self.root.glob(pattern)):
            if item.is_file() and not item.name.startswith("."):
                ext = item.suffix.lower()
                if ext in KNOWN_VIDEO_EXTENSIONS or ext == "":
                    yield item

    def iter_clips(self) -> Iterator[ClipRef]:
        for path in self.iter_paths():
            yield clip_from_path(path, status=self.status)

    @contextmanager
    def materialize(self, uri: str) -> Iterator[Path]:
        if uri.startswith("file://"):
            parsed = urlparse(uri)
            # Handle Windows drive paths like file:///C:/path
            path_str = unquote(parsed.path)
            if path_str.startswith("/") and len(path_str) > 2 and path_str[2] == ":":
                path_str = path_str[1:]
            local = Path(path_str)
        else:
            local = Path(uri)

        yield local.resolve()
