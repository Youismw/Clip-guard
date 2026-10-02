"""Evaluation harness orchestrating benchmarks across edit categories."""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from dedupe.config import Config, load_config
from dedupe.eval.edits import EditCatalog, EditVariant
from dedupe.models import Verdict
from dedupe.pipeline import load_pipeline
from dedupe.sources.local import LocalSource, clip_from_path
from dedupe.stores.sqlite import SqliteStore


@dataclass(frozen=True)
class EvalConfig:
    originals_dir: Path
    output_dir: Path
    distinct_dir: Optional[Path] = None
    known_pairs_csv: Optional[Path] = None
    pipeline_config_path: Optional[Path] = None
    edits: Sequence[str] = field(
        default_factory=lambda: [
            "exact_copy",
            "trim_head_1s",
            "trim_tail_1s",
            "reencode_h264_q1",
            "reencode_h264_q2",
            "resize_720p",
            "strip_metadata",
            "color_adjust",
            "hflip",
        ]
    )


@dataclass(frozen=True)
class QueryRecord:
    query_id: str
    query_path: str
    target_original_id: str
    category: str
    is_gated: bool
    verdict: str
    is_caught: bool  # duplicate or review
    confidence: float
    matched_clip_id: Optional[str]
    elapsed_ms: int


@dataclass(frozen=True)
class CategoryMetric:
    category: str
    is_gated: bool
    total_queries: int
    caught_count: int
    duplicate_count: int
    review_count: int
    miss_count: int
    recall: float
    ci_lower: float
    ci_upper: float


@dataclass(frozen=True)
class DistinctMetric:
    total_clips: int
    false_positives: int
    reviews: int
    clears: int
    fp_rate: float
    review_rate: float


@dataclass(frozen=True)
class EvalReportData:
    timestamp: float
    pipeline_config: Mapping[str, Any]
    active_detectors: Sequence[str]
    total_originals: int
    total_queries: int
    gated_recall: float
    overall_recall: float
    categories: Sequence[CategoryMetric]
    distinct: Optional[DistinctMetric]
    records: Sequence[QueryRecord]


def _calc_wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Calculate 95% Wilson score interval for binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return max(0.0, centre - margin), min(1.0, centre + margin)


class EvaluationHarness:
    """Runs automated benchmark queries against an isolated index and computes metrics."""

    def __init__(self, eval_cfg: EvalConfig) -> None:
        self.cfg = eval_cfg
        self.edit_catalog = EditCatalog()

    def run(self) -> EvalReportData:
        temp_dir = Path(tempfile.mkdtemp(prefix="clipguard_eval_"))
        eval_db_path = temp_dir / "eval_store.db"

        try:
            # 1. Initialize isolated store and pipeline
            base_cfg: Config = load_config(self.cfg.pipeline_config_path)
            # Override store path to isolated eval DB
            eval_store = SqliteStore(eval_db_path)
            pipeline = load_pipeline(
                config_path=self.cfg.pipeline_config_path,
                store=eval_store,
            )

            # 2. Index originals
            source = LocalSource(self.cfg.originals_dir, status="approved")
            originals_list = list(source.iter_clips())
            for orig in originals_list:
                pipeline.index(orig)

            orig_map = {orig.local_path.stem: orig for orig in originals_list}

            # 3. Generate synthetic variants and run query checks
            edits_dir = temp_dir / "generated_variants"
            all_variants: list[EditVariant] = []

            for orig in originals_list:
                vars_for_orig = self.edit_catalog.apply_catalog(
                    original=orig.local_path,
                    output_dir=edits_dir,
                    requested_edits=self.cfg.edits,
                )
                all_variants.extend(vars_for_orig)

            records: list[QueryRecord] = []

            for var in all_variants:
                q_clip = clip_from_path(var.output_path)
                t0 = time.perf_counter()
                verdict: Verdict = pipeline.check(q_clip, register=False)
                elapsed = int((time.perf_counter() - t0) * 1000)

                # Target original
                target_orig = orig_map.get(var.original_path.stem)
                target_id = target_orig.clip_id if target_orig else ""

                top_match = verdict.matches[0] if verdict.matches else None
                top_conf = top_match.confidence if top_match else 0.0
                matched_id = top_match.matched_clip_id if top_match else None

                # Caught if verdict is duplicate or review or already_indexed
                is_caught = verdict.verdict in {"duplicate", "review", "already_indexed"}

                records.append(
                    QueryRecord(
                        query_id=q_clip.clip_id,
                        query_path=str(var.output_path),
                        target_original_id=target_id,
                        category=var.edit_type,
                        is_gated=var.is_gated,
                        verdict=verdict.verdict,
                        is_caught=is_caught,
                        confidence=top_conf,
                        matched_clip_id=matched_id,
                        elapsed_ms=elapsed,
                    )
                )

            # 4. Check distinct (unrelated) clips if provided
            distinct_metric: Optional[DistinctMetric] = None
            if self.cfg.distinct_dir and self.cfg.distinct_dir.exists():
                distinct_source = LocalSource(self.cfg.distinct_dir)
                distinct_clips = list(distinct_source.iter_clips())
                total_dist = len(distinct_clips)
                fps = 0
                revs = 0
                clears = 0

                for d_clip in distinct_clips:
                    v = pipeline.check(d_clip, register=False)
                    if v.verdict == "duplicate":
                        fps += 1
                    elif v.verdict == "review":
                        revs += 1
                    else:
                        clears += 1

                fp_rate = (fps / total_dist) if total_dist > 0 else 0.0
                rev_rate = (revs / total_dist) if total_dist > 0 else 0.0
                distinct_metric = DistinctMetric(
                    total_clips=total_dist,
                    false_positives=fps,
                    reviews=revs,
                    clears=clears,
                    fp_rate=fp_rate,
                    review_rate=rev_rate,
                )

            # 5. Aggregate metrics per category
            cat_metrics = self._aggregate_categories(records)

            gated_queries = [r for r in records if r.is_gated]
            gated_caught = sum(1 for r in gated_queries if r.is_caught)
            gated_recall = (gated_caught / len(gated_queries)) if gated_queries else 0.0

            total_caught = sum(1 for r in records if r.is_caught)
            overall_recall = (total_caught / len(records)) if records else 0.0

            active_dets = [d.name for d in pipeline.detectors]

            return EvalReportData(
                timestamp=time.time(),
                pipeline_config=asdict(base_cfg.pipeline),
                active_detectors=active_dets,
                total_originals=len(originals_list),
                total_queries=len(records),
                gated_recall=gated_recall,
                overall_recall=overall_recall,
                categories=cat_metrics,
                distinct=distinct_metric,
                records=records,
            )

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _aggregate_categories(self, records: Sequence[QueryRecord]) -> Sequence[CategoryMetric]:
        by_cat: dict[str, list[QueryRecord]] = {}
        for r in records:
            by_cat.setdefault(r.category, []).append(r)

        metrics: list[CategoryMetric] = []
        for cat_name, recs in sorted(by_cat.items()):
            n = len(recs)
            caught = sum(1 for r in recs if r.is_caught)
            dups = sum(1 for r in recs if r.verdict in {"duplicate", "already_indexed"})
            revs = sum(1 for r in recs if r.verdict == "review")
            miss = n - caught
            rec = caught / n if n > 0 else 0.0
            ci_low, ci_high = _calc_wilson_ci(caught, n)

            metrics.append(
                CategoryMetric(
                    category=cat_name,
                    is_gated=recs[0].is_gated,
                    total_queries=n,
                    caught_count=caught,
                    duplicate_count=dups,
                    review_count=revs,
                    miss_count=miss,
                    recall=rec,
                    ci_lower=ci_low,
                    ci_upper=ci_high,
                )
            )

        return metrics
