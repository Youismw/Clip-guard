"""Core pipeline orchestration for checking and indexing video clips."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional, Union

# Import detectors to ensure self-registration
import dedupe.detectors.audio_chromaprint  # noqa: F401
import dedupe.detectors.exact_sha256  # noqa: F401
import dedupe.detectors.frame_phash  # noqa: F401
from dedupe.combiner import Combiner
from dedupe.config import Config, PipelineConfig, load_config
from dedupe.detectors.base import Detector
from dedupe.models import ClipRef, DetectorResult, Verdict
from dedupe.registry import create_detector, list_detectors
from dedupe.stores.base import Store
from dedupe.stores.sqlite import SqliteStore


class Pipeline:
    """Orchestrates clip ingestion, multi-detector execution, and verdict determination."""

    def __init__(
        self,
        detectors: Sequence[Detector],
        combiner: Combiner,
        store: Store,
        config: Optional[PipelineConfig] = None,
    ) -> None:
        self.detectors = list(detectors)
        self.combiner = combiner
        self.store = store
        self.config = config or PipelineConfig()

    def check(self, clip: ClipRef, register: bool = True) -> Verdict:
        """Check an incoming clip against all enabled detectors and synthesize a verdict."""
        # 1. Check if identical file is already indexed
        if self.store.clips.exists(clip.clip_id):
            return self.combiner.combine(clip, [], is_already_indexed=True)

        results: list[DetectorResult] = []
        fps_by_detector: list[tuple[Detector, Any]] = []

        # 2. Run enabled detectors with fail-soft error handling
        for detector in self.detectors:
            t0 = time.perf_counter()
            try:
                fp = detector.extract(clip)
                matches = detector.search(fp, exclude_clip_id=clip.clip_id)
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                results.append(
                    DetectorResult(
                        detector=detector.name,
                        version=detector.version,
                        matches=matches,
                        elapsed_ms=elapsed_ms,
                    )
                )
                fps_by_detector.append((detector, fp))
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                results.append(
                    DetectorResult(
                        detector=detector.name,
                        version=detector.version,
                        matches=[],
                        elapsed_ms=elapsed_ms,
                        error=str(exc),
                    )
                )

        # 3. Combine detector results into verdict
        verdict = self.combiner.combine(clip, results, is_already_indexed=False)

        # 4. Optional registration if clean / not an error
        should_register = (
            register and self.config.register_after_check and verdict.verdict != "error"
        )
        if should_register:
            self.store.clips.add(clip)
            for det, fp in fps_by_detector:
                det.register(clip.clip_id, fp)

        return verdict

    def index(self, clip: ClipRef) -> None:
        """Register a clip into the store without checking for duplicates (backfill)."""
        self.store.clips.add(clip)
        for detector in self.detectors:
            fp = detector.extract(clip)
            detector.register(clip.clip_id, fp)


def load_pipeline(
    config_path: Optional[Union[Path, str]] = None,
    config: Optional[Config] = None,
    store: Optional[Store] = None,
) -> Pipeline:
    """Factory to initialize Pipeline from configuration."""
    cfg = config if config is not None else load_config(config_path)

    # 1. Initialize Store
    if store is None:
        if cfg.store.type == "sqlite":
            store = SqliteStore(cfg.store.path)
        elif cfg.store.type == "postgres":
            from dedupe.stores.postgres import PostgresStore

            store = PostgresStore(cfg.store)
        else:
            raise ValueError(f"Unsupported store type: '{cfg.store.type}'")

    # 2. Build Detectors
    detectors: list[Detector] = []
    thresholds: dict[str, tuple[float, float]] = {}

    for d_name, d_cfg in cfg.detectors.items():
        enabled = getattr(d_cfg, "enabled", False)
        if not enabled:
            continue

        if d_name not in list_detectors():
            continue

        flag_th = getattr(d_cfg, "flag_threshold", 0.80)
        rev_th = getattr(d_cfg, "review_threshold", 0.40)
        thresholds[d_name] = (flag_th, rev_th)

        params: dict[str, Any] = {k: v for k, v in d_cfg.__dict__.items() if not k.startswith("_")}
        detector = create_detector(d_name, params=params, store=store)
        detectors.append(detector)

    # 3. Build Combiner
    combiner = Combiner(
        policy=cfg.pipeline.combiner,
        thresholds=thresholds,
        weights=dict(cfg.pipeline.weights),
    )

    return Pipeline(
        detectors=detectors,
        combiner=combiner,
        store=store,
        config=cfg.pipeline,
    )
