"""Command-line interface for ClipGuard (dedupe)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from dedupe.config import load_config
from dedupe.doctor import format_report_json, format_report_text, run_doctor
from dedupe.pipeline import load_pipeline
from dedupe.sources.local import LocalSource, clip_from_path
from dedupe.stores.sqlite import SqliteStore

EXIT_CLEAR = 0
EXIT_DUPLICATE = 10
EXIT_REVIEW = 11
EXIT_ALREADY_INDEXED = 12
EXIT_ERROR = 1
EXIT_USAGE_ERROR = 2

VERDICT_EXIT_CODES = {
    "clear": EXIT_CLEAR,
    "duplicate": EXIT_DUPLICATE,
    "review": EXIT_REVIEW,
    "already_indexed": EXIT_ALREADY_INDEXED,
    "error": EXIT_ERROR,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dedupe",
        description="ClipGuard: Duplicate Video Detection CLI",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Global path to dedupe.toml configuration file",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # dedupe doctor
    doctor_p = subparsers.add_parser(
        "doctor",
        help="Check environment, dependencies, and configuration validity",
    )
    doctor_p.add_argument(
        "--all",
        action="store_true",
        help="Check all optional dependencies (including fpcalc even if disabled)",
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostics report in JSON format",
    )

    # dedupe index
    index_p = subparsers.add_parser("index", help="Index existing videos")
    index_p.add_argument("path", type=Path, help="Directory or file to index")
    index_p.add_argument(
        "--status",
        choices=["approved", "rejected", "pending"],
        default="approved",
        help="Initial status for indexed clips",
    )
    index_p.add_argument("--workers", type=int, default=1, help="Parallel worker count")

    # dedupe check
    check_p = subparsers.add_parser("check", help="Check incoming video for duplicates")
    check_p.add_argument("file", type=Path, help="Video file to check")
    check_p.add_argument("--no-register", action="store_true", help="Do not register after check")
    check_p.add_argument("--json", action="store_true", help="Output verdict JSON on stdout")

    # dedupe eval (Phase 2+)
    eval_p = subparsers.add_parser("eval", help="Run evaluation harness")
    eval_p.add_argument("--config", type=Path, required=True, help="Evaluation config file")

    # dedupe pilot (Phase 5)
    pilot_p = subparsers.add_parser("pilot", help="Run Section 13 Local Pilot Gate Protocol")
    pilot_p.add_argument(
        "--pairs",
        type=Path,
        default=Path("./data/known_pairs.csv"),
        help="Path to known_pairs.csv",
    )
    pilot_p.add_argument(
        "--distinct",
        type=Path,
        default=Path("./data/distinct"),
        help="Path to distinct clips dir",
    )
    pilot_p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./pilot_results"),
        help="Directory to write pilot reports",
    )

    # dedupe backfill (Phase 6)
    backfill_p = subparsers.add_parser(
        "backfill", help="Run resumable backfill for approved footage"
    )
    backfill_p.add_argument(
        "--source",
        type=str,
        default=None,
        help="Source directory or s3://bucket/prefix to backfill",
    )
    backfill_p.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("backfill_checkpoint.json"),
        help="Path to checkpoint tracking JSON file",
    )
    backfill_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate volume, footage hours, and machine time without indexing",
    )

    # dedupe worker (Phase 6)
    worker_p = subparsers.add_parser("worker", help="Run SQS cloud worker event loop")
    worker_p.add_argument("--queue-url", type=str, required=True, help="AWS SQS Queue URL to poll")
    worker_p.add_argument(
        "--results-bucket",
        type=str,
        default=None,
        help="Optional S3 bucket to output verdict JSONs",
    )
    worker_p.add_argument(
        "--once",
        action="store_true",
        help="Process available messages once and exit (batch mode)",
    )

    # dedupe stats
    subparsers.add_parser("stats", help="Display index statistics and counts")

    # dedupe gui
    subparsers.add_parser("gui", help="Launch functional desktop GUI")

    return parser


def handle_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(config_path=args.config, check_all=args.all)
    if args.json:
        sys.stdout.write(format_report_json(report) + "\n")
    else:
        sys.stdout.write(format_report_text(report) + "\n")
    return EXIT_CLEAR if report.ok else EXIT_ERROR


def handle_index(args: argparse.Namespace) -> int:
    if not args.path.exists():
        sys.stderr.write(f"Error: Path does not exist: {args.path}\n")
        return EXIT_ERROR

    pipeline = None
    try:
        pipeline = load_pipeline(config_path=args.config)
        source = LocalSource(args.path, status=args.status)
        count = 0
        for clip in source.iter_clips():
            pipeline.index(clip)
            count += 1
        sys.stderr.write(f"Successfully indexed {count} clip(s) with status '{args.status}'.\n")
        return EXIT_CLEAR
    except Exception as exc:
        sys.stderr.write(f"Error during indexing: {exc}\n")
        return EXIT_ERROR
    finally:
        if pipeline is not None:
            pipeline.store.close()


def handle_check(args: argparse.Namespace) -> int:
    if not args.file.exists():
        sys.stderr.write(f"Error: Video file does not exist: {args.file}\n")
        return EXIT_ERROR

    pipeline = None
    try:
        pipeline = load_pipeline(config_path=args.config)
        clip = clip_from_path(args.file)
        register = not args.no_register
        verdict = pipeline.check(clip, register=register)

        if args.json:
            sys.stdout.write(verdict.to_json() + "\n")
        else:
            sys.stdout.write(f"Verdict: {verdict.verdict.upper()}\n")
            if verdict.layer and verdict.layer != "none":
                sys.stdout.write(f"Layer:   {verdict.layer.upper()}\n")
            if verdict.intent_assessment and verdict.intent_assessment != "clean":
                sys.stdout.write(f"Intent:  {verdict.intent_assessment}\n")
            sys.stdout.write(f"Reason:  {verdict.reason}\n")
            if verdict.matches:
                sys.stdout.write("Matches:\n")
                for m in verdict.matches:
                    st = f" [{m.matched_status}]" if m.matched_status else ""
                    sys.stdout.write(
                        f"  - {m.detector}: {m.matched_clip_id[:16]}... "
                        f"(conf: {m.confidence:.2f}){st}\n"
                    )

        return VERDICT_EXIT_CODES.get(verdict.verdict, EXIT_ERROR)
    except Exception as exc:
        sys.stderr.write(f"Error checking file '{args.file}': {exc}\n")
        return EXIT_ERROR
    finally:
        if pipeline is not None:
            pipeline.store.close()


def handle_stats(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
        if cfg.store.type != "sqlite":
            sys.stderr.write(f"Store type '{cfg.store.type}' stats not supported.\n")
            return EXIT_ERROR

        store = SqliteStore(cfg.store.path)
        try:
            total = store.clips.count()
            approved = store.clips.count(status="approved")
            rejected = store.clips.count(status="rejected")
            pending = store.clips.count(status="pending")

            sys.stdout.write("ClipGuard Store Statistics\n")
            sys.stdout.write("=" * 30 + "\n")
            sys.stdout.write(f"Database: {cfg.store.path}\n")
            sys.stdout.write(f"Total clips:     {total}\n")
            sys.stdout.write(f"  - Approved:    {approved}\n")
            sys.stdout.write(f"  - Rejected:    {rejected}\n")
            sys.stdout.write(f"  - Pending:     {pending}\n")
            return EXIT_CLEAR
        finally:
            store.close()
    except Exception as exc:
        sys.stderr.write(f"Error retrieving stats: {exc}\n")
        return EXIT_ERROR


def handle_eval(args: argparse.Namespace) -> int:
    config_file = args.config
    if not config_file or not config_file.exists():
        sys.stderr.write(f"Error: Evaluation config file does not exist: {config_file}\n")
        return EXIT_ERROR

    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef, import-not-found]

        with config_file.open("rb") as f:
            eval_toml = tomllib.load(f)

        eval_sec = eval_toml.get("eval", {})
        originals_dir = Path(eval_sec.get("originals_dir", "./data/originals"))
        output_dir = Path(eval_sec.get("output_dir", "./eval_results"))
        distinct_raw = eval_sec.get("distinct_dir")
        distinct_dir = Path(distinct_raw) if distinct_raw else None
        pairs_raw = eval_sec.get("known_pairs_csv")
        pairs_csv = Path(pairs_raw) if pairs_raw else None
        edits = eval_sec.get("edits")

        if not originals_dir.exists():
            sys.stderr.write(f"Error: originals_dir does not exist: {originals_dir}\n")
            return EXIT_ERROR

        from dedupe.eval.harness import EvalConfig, EvaluationHarness
        from dedupe.eval.report import write_reports

        kwargs: dict[str, Any] = {
            "originals_dir": originals_dir,
            "output_dir": output_dir,
            "distinct_dir": distinct_dir,
            "known_pairs_csv": pairs_csv,
            "pipeline_config_path": None,
        }
        if edits:
            kwargs["edits"] = edits

        eval_cfg = EvalConfig(**kwargs)
        harness = EvaluationHarness(eval_cfg)
        sys.stderr.write("Running ClipGuard Evaluation Harness benchmark...\n")
        report_data = harness.run()
        json_path, md_path = write_reports(report_data, output_dir)

        sys.stdout.write("Evaluation Benchmark Complete.\n")
        sys.stdout.write(f"  - Originals Indexed:  {report_data.total_originals}\n")
        sys.stdout.write(f"  - Total Queries Run:  {report_data.total_queries}\n")
        sys.stdout.write(f"  - Gated Edits Recall: {report_data.gated_recall * 100:.1f}%\n")
        sys.stdout.write(f"  - Overall Recall:     {report_data.overall_recall * 100:.1f}%\n")
        sys.stdout.write(f"  - JSON Report:        {json_path}\n")
        sys.stdout.write(f"  - Markdown Report:    {md_path}\n")
        return EXIT_CLEAR
    except Exception as exc:
        sys.stderr.write(f"Error during evaluation: {exc}\n")
        return EXIT_ERROR


def handle_pilot(args: argparse.Namespace) -> int:
    pairs_file = args.pairs
    if not pairs_file.exists():
        sys.stderr.write(
            f"Error: known_pairs.csv not found at: {pairs_file}\n"
            "Run 'python scripts/prepare_pilot_dataset.py' to generate test data.\n"
        )
        return EXIT_ERROR

    try:
        from dedupe.eval.pilot import (
            PilotProtocolRunner,
            load_known_pairs_csv,
            write_pilot_reports,
        )

        pairs = load_known_pairs_csv(pairs_file)
        if not pairs:
            sys.stderr.write(f"Error: No valid duplicate pairs found in {pairs_file}\n")
            return EXIT_ERROR

        distinct_dir = args.distinct if args.distinct.exists() else None
        runner = PilotProtocolRunner(
            pairs=pairs,
            distinct_dir=distinct_dir,
            config_path=args.config,
        )

        sys.stderr.write(f"Executing Section 13 Local Pilot Protocol ({len(pairs)} pairs)...\n")
        report = runner.run()
        out_dir = args.output_dir
        json_p, md_p = write_pilot_reports(report, out_dir)

        c = report.checklist
        sys.stdout.write("=" * 60 + "\n")
        sys.stdout.write("ClipGuard Section 13 Local Pilot Gate Report\n")
        sys.stdout.write("=" * 60 + "\n")
        sys.stdout.write(f"Gate Decision:       {c.decision}\n")
        sys.stdout.write(
            f"Recall (real pairs): {c.recall_real_pairs_result} "
            f"(target {c.recall_real_pairs_target})\n"
        )
        sys.stdout.write(
            f"Recall (scripted):   {c.recall_scripted_result} (target {c.recall_scripted_target})\n"
        )
        sys.stdout.write(f"False-positive rate: {c.fp_rate_result} (target {c.fp_rate_target})\n")
        sys.stdout.write(
            f"Review queue size:   {c.review_queue_result} (target {c.review_queue_target})\n"
        )
        sys.stdout.write(f"Throughput:          {c.throughput_result}\n")
        sys.stdout.write(
            f"Determinism (runs):  {c.determinism_result} (target {c.determinism_target})\n"
        )
        sys.stdout.write("-" * 60 + "\n")
        sys.stdout.write(f"JSON Report:     {json_p}\n")
        sys.stdout.write(f"Markdown Report: {md_p}\n")
        sys.stdout.write("=" * 60 + "\n")

        return EXIT_CLEAR if c.decision == "GO" else EXIT_ERROR
    except Exception as exc:
        sys.stderr.write(f"Error running local pilot protocol: {exc}\n")
        return EXIT_ERROR


def handle_backfill(args: argparse.Namespace) -> int:
    pipeline = None
    try:
        cfg = load_config(args.config)
        pipeline = load_pipeline(config_path=args.config)
        source_arg = args.source
        if source_arg:
            if source_arg.startswith("s3://"):
                from urllib.parse import urlparse

                from dedupe.sources.s3 import S3Source

                p = urlparse(source_arg)
                source: Any = S3Source(
                    bucket=p.netloc,
                    prefix=p.path.lstrip("/"),
                    status="approved",
                    region=cfg.source.region,
                    endpoint_url=cfg.source.endpoint_url,
                )
            else:
                source = LocalSource(Path(source_arg), status="approved")
        else:
            if cfg.source.type == "s3":
                from dedupe.sources.s3 import S3Source

                b = cfg.source.approved_bucket or cfg.source.bucket
                source = S3Source(
                    bucket=b,
                    prefix=cfg.source.prefix,
                    status="approved",
                    region=cfg.source.region,
                    endpoint_url=cfg.source.endpoint_url,
                )
            else:
                source = LocalSource(Path(cfg.source.path), status="approved")

        from dedupe.backfill import BackfillManager

        mgr = BackfillManager(
            pipeline=pipeline,
            source=source,
            checkpoint_path=args.checkpoint,
        )
        mgr.run(dry_run=args.dry_run)
        return EXIT_CLEAR
    except Exception as exc:
        sys.stderr.write(f"Error during backfill: {exc}\n")
        return EXIT_ERROR
    finally:
        if pipeline is not None:
            pipeline.store.close()


def handle_worker(args: argparse.Namespace) -> int:
    pipeline = None
    try:
        cfg = load_config(args.config)
        pipeline = load_pipeline(config_path=args.config)

        from dedupe.worker import SqsWorker

        worker = SqsWorker(
            pipeline=pipeline,
            queue_url=args.queue_url,
            results_bucket=args.results_bucket,
            region=cfg.source.region,
            endpoint_url=cfg.source.endpoint_url,
        )
        if args.once:
            count = worker.run_once(max_messages=10)
            sys.stdout.write(f"Worker finished single batch: processed {count} message(s).\n")
        else:
            worker.run_loop()
        return EXIT_CLEAR
    except Exception as exc:
        sys.stderr.write(f"Error in SQS worker: {exc}\n")
        return EXIT_ERROR
    finally:
        if pipeline is not None:
            pipeline.store.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parser.print_help(sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE_ERROR if exc.code != 0 else EXIT_CLEAR

    if args.command == "doctor":
        return handle_doctor(args)
    elif args.command == "index":
        return handle_index(args)
    elif args.command == "check":
        return handle_check(args)
    elif args.command == "stats":
        return handle_stats(args)
    elif args.command == "gui":
        from dedupe.gui import launch_gui

        launch_gui(config_path=args.config)
        return EXIT_CLEAR
    elif args.command == "eval":
        return handle_eval(args)
    elif args.command == "pilot":
        return handle_pilot(args)
    elif args.command == "backfill":
        return handle_backfill(args)
    elif args.command == "worker":
        return handle_worker(args)
    else:
        parser.print_help(sys.stderr)
        return EXIT_USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main())
