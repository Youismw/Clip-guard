"""Evaluation harness and synthetic edit benchmarking module."""

from __future__ import annotations

from dedupe.eval.edits import EditCatalog, generate_synthetic_clip
from dedupe.eval.harness import EvalConfig, EvaluationHarness
from dedupe.eval.report import generate_markdown_report, write_reports

__all__ = [
    "EditCatalog",
    "generate_synthetic_clip",
    "EvalConfig",
    "EvaluationHarness",
    "generate_markdown_report",
    "write_reports",
]
