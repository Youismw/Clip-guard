"""Base abstract class for all duplicate detectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from dedupe.models import ClipRef, Match
from dedupe.stores.base import Store


class Detector(ABC):
    """Abstract base class for modular detectors (Design Principle 2)."""

    name: ClassVar[str]
    version: ClassVar[str]

    def __init__(self, params: Mapping[str, Any], store: Store) -> None:
        self.params = params
        self.store = store

    @abstractmethod
    def requirements(self) -> Sequence[str]:
        """External binaries needed, e.g. ['ffmpeg']. Checked by `dedupe doctor`."""

    @abstractmethod
    def extract(self, clip: ClipRef) -> Any:
        """Compute a serializable fingerprint. No store access."""

    @abstractmethod
    def search(self, fp: Any, exclude_clip_id: str) -> Sequence[Match]:
        """Query the index. Must never return the clip itself."""

    @abstractmethod
    def register(self, clip_id: str, fp: Any) -> None:
        """Add the fingerprint to the index. Must be idempotent."""
