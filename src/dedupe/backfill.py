"""Resumable backfill manager for indexing existing approved footage (Phase 6)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dedupe.pipeline import Pipeline
from dedupe.sources.base import ClipSource


class BackfillManager:
    """Manages resumable backfills with upfront cost estimates and checkpointing."""

    def __init__(
        self,
        pipeline: Pipeline,
        source: ClipSource,
        checkpoint_path: Path,
        assumed_duration_s: float = 12.0,
        estimated_throughput_cph: float = 1600.0,
    ) -> None:
        self.pipeline = pipeline
        self.source = source
        self.checkpoint_path = checkpoint_path
        self.assumed_duration_s = assumed_duration_s
        self.estimated_throughput_cph = estimated_throughput_cph

    def load_checkpoint(self) -> set[str]:
        """Load set of already indexed clip URIs or IDs from checkpoint file."""
        if not self.checkpoint_path.exists():
            return set()
        try:
            with self.checkpoint_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("indexed_uris", []))
        except Exception as exc:
            sys.stderr.write(f"Warning: Could not read checkpoint file ({exc}), starting fresh.\n")
            return set()

    def save_checkpoint(self, indexed_uris: set[str], total_count: int) -> None:
        """Persist indexed clip set to checkpoint file."""
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.checkpoint_path.with_suffix(".tmp")
        data = {
            "timestamp": time.time(),
            "total_indexed": len(indexed_uris),
            "total_target": total_count,
            "indexed_uris": sorted(indexed_uris),
        }
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(self.checkpoint_path)

    def print_cost_estimate(self, total_clips: int, already_indexed: int) -> None:
        """Print upfront cost, duration, and time estimate before beginning backfill."""
        remaining = max(0, total_clips - already_indexed)
        total_seconds = remaining * self.assumed_duration_s
        total_hours = total_seconds / 3600.0
        est_hours_run = (
            (remaining / self.estimated_throughput_cph)
            if self.estimated_throughput_cph > 0
            else 0.0
        )

        sys.stdout.write("=" * 60 + "\n")
        sys.stdout.write("ClipGuard Backfill Pre-flight Cost & Time Estimate\n")
        sys.stdout.write("=" * 60 + "\n")
        sys.stdout.write(f"Total Clips in Source:    {total_clips}\n")
        sys.stdout.write(f"Already Indexed:          {already_indexed}\n")
        sys.stdout.write(f"Remaining to Process:     {remaining}\n")
        sys.stdout.write(f"Estimated Footage Volume: {total_hours:.1f} hours of video\n")
        mins = est_hours_run * 60
        sys.stdout.write(
            f"Estimated Machine Time:   {mins:.1f} minutes ({est_hours_run:.2f} hrs)\n"
        )
        sys.stdout.write(
            f"Estimated Throughput:     ~{self.estimated_throughput_cph:.0f} clips/hr\n"
        )
        sys.stdout.write(f"Checkpoint File:          {self.checkpoint_path}\n")
        sys.stdout.write("=" * 60 + "\n")

    def run(self, dry_run: bool = False) -> int:
        """Execute backfill loop. Returns count of newly indexed clips."""
        # 1. Discover all clips from source
        all_clips = list(self.source.iter_clips())
        total_clips = len(all_clips)

        # 2. Load checkpoint
        indexed_uris = self.load_checkpoint()
        self.print_cost_estimate(total_clips, len(indexed_uris))

        if dry_run:
            sys.stdout.write("Dry-run requested. Exiting without indexing.\n")
            return 0

        # 3. Index remaining clips
        newly_indexed = 0
        t0 = time.perf_counter()

        for idx, clip in enumerate(all_clips, start=1):
            if clip.uri in indexed_uris or self.pipeline.store.clips.exists(clip.clip_id):
                indexed_uris.add(clip.uri)
                continue

            try:
                self.pipeline.index(clip)
                indexed_uris.add(clip.uri)
                newly_indexed += 1

                # Save checkpoint periodically every 10 clips
                if newly_indexed % 10 == 0:
                    self.save_checkpoint(indexed_uris, total_clips)
                    elapsed = max(0.001, time.perf_counter() - t0)
                    rate = (newly_indexed / elapsed) * 3600.0
                    sys.stderr.write(
                        f"  [{idx}/{total_clips}] Indexed {newly_indexed} clips "
                        f"({rate:.0f} clips/hr)...\n"
                    )
            except Exception as exc:
                sys.stderr.write(f"Error indexing clip '{clip.uri}': {exc}\n")

        # 4. Final checkpoint save
        self.save_checkpoint(indexed_uris, total_clips)
        sys.stdout.write(
            f"Backfill Complete. Indexed {newly_indexed} new clip(s). "
            f"Total indexed in database: {len(indexed_uris)}.\n"
        )
        return newly_indexed
