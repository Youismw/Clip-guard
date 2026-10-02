"""Detector registry for self-registering detector modules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from dedupe.detectors.base import Detector
from dedupe.stores.base import Store

D = TypeVar("D", bound=type[Detector])

_DETECTOR_REGISTRY: dict[str, type[Detector]] = {}


def register_detector(name: str) -> Callable[[D], D]:
    """Decorator to register a Detector class by its unique name."""

    def decorator(cls: D) -> D:
        if name in _DETECTOR_REGISTRY:
            existing = _DETECTOR_REGISTRY[name]
            if existing is not cls:
                raise ValueError(f"Detector name '{name}' already registered by {existing}")
        _DETECTOR_REGISTRY[name] = cls
        return cls

    return decorator


def get_detector_cls(name: str) -> type[Detector]:
    """Retrieve registered Detector class by name."""
    if name not in _DETECTOR_REGISTRY:
        avail = list(_DETECTOR_REGISTRY.keys())
        raise KeyError(f"No detector registered with name '{name}'. Available: {avail}")
    return _DETECTOR_REGISTRY[name]


def list_detectors() -> list[str]:
    """Return all registered detector names."""
    return sorted(_DETECTOR_REGISTRY.keys())


def create_detector(name: str, params: Mapping[str, Any], store: Store) -> Detector:
    """Instantiate a detector by registered name."""
    cls = get_detector_cls(name)
    return cls(params=params, store=store)
