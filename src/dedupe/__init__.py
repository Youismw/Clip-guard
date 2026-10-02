"""ClipGuard duplicate video detection package."""

from __future__ import annotations

from dedupe.models import ClipRef, DetectorResult, Match, Verdict
from dedupe.pipeline import Pipeline, load_pipeline

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ClipRef",
    "Match",
    "DetectorResult",
    "Verdict",
    "Pipeline",
    "load_pipeline",
]
