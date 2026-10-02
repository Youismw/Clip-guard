"""Report generator for evaluation harness producing JSON and Markdown reports."""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict
from pathlib import Path

from dedupe.eval.harness import EvalReportData


def generate_markdown_report(data: EvalReportData) -> str:
    dt_str = datetime.datetime.fromtimestamp(data.timestamp).strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = [
        "# ClipGuard: Evaluation Benchmark Report",
        "",
        f"**Generated:** {dt_str}  ",
        f"**Active Detectors:** `{', '.join(data.active_detectors)}`  ",
        f"**Original Clips Indexed:** {data.total_originals}  ",
        f"**Total Query Benchmark Tests:** {data.total_queries}  ",
        "",
        "---",
        "",
        "## 1. Executive Summary & Gated Metrics",
        "",
        "| Metric | Target | Result | Status |",
        "|---|---|---|---|",
    ]

    # Target S2: Gated edits recall >= 95%
    if data.gated_recall >= 0.95:
        gated_status = "PASS"
    elif "frame_phash" not in data.active_detectors:
        gated_status = "BASELINE (Exact Hash)"
    else:
        gated_status = "SUB-TARGET"
    pct = data.gated_recall * 100
    lines.append(
        f"| **S2: Gated Synthetic Edits Recall** | >= 95.0% | **{pct:.1f}%** | {gated_status} |"
    )

    # Target S3 & S4: Distinct clips
    if data.distinct is not None:
        fp_pct = data.distinct.fp_rate * 100
        fp_stat = "PASS" if data.distinct.fp_rate <= 0.01 else "FAIL"
        lines.append(
            f"| **S3: False Positive Rate (Distinct)** | <= 1.0% | **{fp_pct:.2f}%** | {fp_stat} |"
        )

        rev_pct = data.distinct.review_rate * 100
        rev_stat = "PASS" if data.distinct.review_rate <= 0.05 else "FAIL"
        lines.append(
            f"| **S4: Review Queue Size (Distinct)** | <= 5.0% | **{rev_pct:.2f}%** | {rev_stat} |"
        )
    else:
        lines.append("| **S3: False Positive Rate (Distinct)** | <= 1.0% | N/A | — |")
        lines.append("| **S4: Review Queue Size (Distinct)** | <= 5.0% | N/A | — |")

    if "frame_phash" in data.active_detectors:
        note_lines = [
            "",
            "> **Active Detection:** `exact_sha256` and `frame_phash` (Layer 2) active.",
            "> Multi-layer detection handles temporal shifts, re-encodes, and visual transforms.",
        ]
    else:
        note_lines = [
            "",
            "> **Note on Baseline:** With only `exact_sha256` active, the system catches",
            "> byte-identical copies (100% recall), but misses re-encodes and visual edits",
            "> (0% recall).",
        ]
    lines.extend(note_lines)
    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Edit Category Breakdown",
            "",
            "| Category | Type | Queries | Caught | Missed | Recall | 95% Wilson CI |",
            "|---|---|---|---|---|---|---|",
        ]
    )

    for cat in data.categories:
        type_badge = "**Gated**" if cat.is_gated else "Report-only"
        ci_str = f"[{cat.ci_lower * 100:.1f}%, {cat.ci_upper * 100:.1f}%]"
        rec_str = f"**{cat.recall * 100:.1f}%**"
        lines.append(
            f"| `{cat.category}` | {type_badge} | {cat.total_queries} | "
            f"{cat.caught_count} | {cat.miss_count} | {rec_str} | {ci_str} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Query Details Sample",
            "",
            "| Category | Verdict | Caught? | Confidence | Matched ID | Latency |",
            "|---|---|---|---|---|---|",
        ]
    )

    # Show first 15 records as sample
    for r in data.records[:15]:
        mid = (r.matched_clip_id[:12] + "...") if r.matched_clip_id else "—"
        caught_badge = "Yes" if r.is_caught else "No"
        lines.append(
            f"| `{r.category}` | `{r.verdict}` | {caught_badge} | "
            f"{r.confidence:.2f} | `{mid}` | {r.elapsed_ms} ms |"
        )

    if len(data.records) > 15:
        rem = len(data.records) - 15
        lines.append(f"*(...and {rem} more queries recorded in eval_report.json)*")

    lines.append("")
    return "\n".join(lines)


def write_reports(data: EvalReportData, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "eval_report.json"
    md_path = output_dir / "eval_report.md"

    # Write JSON
    raw_dict = asdict(data)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(raw_dict, f, indent=2)

    # Write Markdown
    md_content = generate_markdown_report(data)
    with md_path.open("w", encoding="utf-8") as f:
        f.write(md_content)

    return json_path, md_path
