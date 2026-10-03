"""Functional desktop GUI for ClipGuard duplicate video detection."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Optional, Union
from urllib.parse import unquote, urlparse

from dedupe.config import AudioChromaprintConfig, Config, load_config
from dedupe.models import ClipRef, Verdict
from dedupe.pipeline import Pipeline, load_pipeline
from dedupe.sources.local import LocalSource, clip_from_path
from dedupe.sources.s3 import S3Source
from dedupe.stores.base import Store
from dedupe.stores.sqlite import SqliteStore


def clean_uri_to_display_path(uri: str) -> str:
    """Convert file:/// or s3:// URI into a clean, human-readable file path or URL."""
    if uri.startswith("file:///"):
        raw_path = unquote(urlparse(uri).path)
        if re.match(r"^/[a-zA-Z]:", raw_path):
            clean_path = raw_path[1:]
        else:
            clean_path = raw_path
        return clean_path.replace("/", "\\")
    return uri


def format_file_size(size_bytes: int) -> str:
    """Format bytes into readable MB or GB string."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} bytes"


def build_smart_report(
    target_path: str,
    verdict: Verdict,
    target_info: dict[str, Any],
    matched_info: Optional[dict[str, Any]],
) -> str:
    """Generate a comprehensive forensic audit report explaining match findings and edits."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("                     CLIPGUARD SMART FORENSIC AUDIT REPORT")
    lines.append("=" * 80)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"Report Generated : {timestamp}")
    lines.append(f"Final Verdict    : {verdict.verdict.upper()}")
    lines.append(f"Detection Layer  : {verdict.layer or 'None'}")
    lines.append(f"Seller Intent    : {verdict.intent_assessment or 'Clean'}")
    lines.append("-" * 80)

    # 1. Video Files Comparison
    lines.append("1. VIDEO FILES COMPARISON")
    lines.append("-" * 80)
    lines.append("Incoming Target Video:")
    lines.append(f"  • Filename   : {target_info.get('filename', 'Unknown')}")
    lines.append(f"  • Full Path  : {target_info.get('path', target_path)}")
    lines.append(f"  • File Size  : {target_info.get('size_str', 'N/A')}")
    lines.append(f"  • SHA-256    : {verdict.clip_id}")

    if matched_info:
        lines.append("\nMatched Database Reference Video:")
        lines.append(f"  • Filename   : {matched_info.get('filename', 'Unknown')}")
        lines.append(f"  • Full Path  : {matched_info.get('display_path', matched_info.get('uri', ''))}")
        lines.append(f"  • Library Tag: {matched_info.get('status', 'approved').upper()}")
        lines.append(f"  • SHA-256    : {matched_info.get('clip_id', 'N/A')}")
        if matched_info.get("size_str"):
            lines.append(f"  • File Size  : {matched_info['size_str']}")
    else:
        lines.append("\nMatched Database Reference Video: None (Clip is unique)")

    lines.append("\n" + "-" * 80)
    lines.append("2. FORENSIC FINDINGS: WHY THIS VERDICT WAS REACHED")
    lines.append("-" * 80)

    if verdict.verdict == "already_indexed":
        lines.append("• Exact Bit-for-Bit Hash Match (Layer 1 - SHA-256):")
        lines.append("  The incoming video has the EXACT same SHA-256 cryptographic hash as an existing")
        lines.append("  clip in the reference database.")
        lines.append("  -> What this means: The file was submitted under a different filename or resubmitted")
        lines.append("     without any re-encoding, trimming, or frame alterations.")
    elif verdict.verdict == "duplicate":
        lines.append("• Confirmed Duplicate Match Across Detection Layers:")
        if verdict.layer == "exact":
            lines.append("  -> Triggered by Layer 1 (Exact SHA-256). Identical byte payload.")
        elif verdict.layer == "visual":
            lines.append("  -> Triggered by Layer 2 (Visual Perceptual pHash). Visual frame structure matched.")
        elif verdict.layer == "audio":
            lines.append("  -> Triggered by Layer 3 (Acoustic Chromaprint). Acoustic frequency fingerprint matched.")

        for m in verdict.matches:
            lines.append(f"\n  [Detector: {m.detector}]")
            lines.append(f"  Match Confidence : {m.confidence * 100:.1f}%")
            ev = m.evidence
            if m.detector == "frame_phash":
                cov = ev.get("coverage", 0) * 100
                m_sec = ev.get("matched_seconds", 0)
                offset = ev.get("offset_s", 0)
                flipped = ev.get("flipped", False)
                lines.append(f"  - Frame Overlap     : {cov:.1f}% of inspected frames directly match")
                lines.append(f"  - Matched Duration  : {m_sec:.1f} seconds of identical scene structure")
                lines.append(f"  - Temporal Offset   : {offset:+.1f}s relative to reference start")
                lines.append(f"  - Orientation       : {'Mirrored (Horizontal Flip)' if flipped else 'Standard (Normal)'}")
                if cov < 90:
                    lines.append("  - Analysis: Segment cuts, periodic trims, or inserted clips detected.")
                else:
                    lines.append("  - Analysis: Near-complete structural overlap; footage is a re-encode/derivative.")
            elif m.detector == "audio_chromaprint":
                ber = ev.get("ber", 0)
                overlap_subfps = ev.get("overlap_subfps", 0)
                offset = ev.get("offset_s", 0)
                lines.append(f"  - Bit Error Rate    : {ber:.3f} (Acoustic Hamming distance, threshold: 0.35)")
                lines.append(f"  - Acoustic Sub-fps  : {overlap_subfps} matching 32-bit sub-fingerprints")
                lines.append(f"  - Temporal Offset   : {offset:+.1f}s audio alignment")
                lines.append("  - Analysis: Acoustic frequency profile is identical to reference audio track.")
    elif verdict.verdict == "review":
        lines.append("• Borderline Similarity Detected:")
        lines.append("  The similarity score crossed the human review threshold but is below the automated flag cutoff.")
        lines.append("  Manual review of keyframes is required to decide whether this is distinct footage.")
    else:
        lines.append("• Clean Video:")
        lines.append("  No matching byte hashes, visual scenes, or acoustic fingerprints were found.")
        lines.append("  The clip appears to be genuine, unique footage.")

    lines.append("\n" + "-" * 80)
    lines.append("3. SELLER INTENT & TAMPERING ANALYSIS")
    lines.append("-" * 80)
    if verdict.intent_assessment == "unedited_likely_unaware":
        lines.append("• Classification: UNEDITED (Accidental Resubmission)")
        lines.append("  Evidence shows identical bytes. The vendor likely resubmitted this video accidentally")
        lines.append("  without knowing we already possessed it.")
    elif verdict.intent_assessment == "edited_likely_intentional":
        lines.append("• Classification: EDITED DERIVATIVE (Intentional Alteration / Evasion)")
        lines.append("  Visual or acoustic content matches, but bytes or structures were modified:")
        lines.append("  - Codec re-encoding or container re-wrapping detected.")
        lines.append("  - Video cuts, trimming, or audio manipulation used to alter the file signature.")
        lines.append("  This pattern is consistent with middleman relabeling or evasion of duplicate filters.")
    elif verdict.verdict == "clear":
        lines.append("• Classification: CLEAN ORIGINAL")
        lines.append("  No signs of duplication or derivative manipulation detected.")
    else:
        lines.append("• Classification: INCONCLUSIVE (Requires Manual Review)")

    lines.append("\n" + "-" * 80)
    lines.append("4. OPERATIONAL TRIAGE & NEXT STEPS")
    lines.append("-" * 80)
    if verdict.verdict in ("duplicate", "already_indexed"):
        lines.append("• REJECT INGESTION: Do NOT ingest this video into robotics training datasets.")
        lines.append("• Notify data management team of duplicate reference ID.")
        if verdict.intent_assessment == "edited_likely_intentional":
            lines.append("• VENDOR ESCALATION: Route to operations/vendor management for contractual review.")
        else:
            lines.append("• VENDOR NOTIFICATION: Politely inform vendor of accidental resubmission.")
    elif verdict.verdict == "review":
        lines.append("• HUMAN REVIEW: Route clip to the review queue for side-by-side keyframe inspection.")
    else:
        lines.append("• APPROVE FOR INGESTION: Safe to proceed with robot policy training pipeline.")

    lines.append("=" * 80)
    return "\n".join(lines)


class ClipGuardGUI:
    """Desktop interface focused on operational functionality, S3/local toggle, and duplicate inspection."""

    def __init__(
        self,
        root: tk.Tk,
        config_path: Optional[Path] = None,
        show_prompt: bool = True,
    ) -> None:
        self.root = root
        self.root.title("ClipGuard — Duplicate Video Detection Console")
        self.root.geometry("900x720")
        self.root.minsize(780, 580)

        self.config_path = config_path
        self.cfg = load_config(self.config_path)

        # Single check state variables
        self.audio_enabled_var = tk.BooleanVar(value=True)  # ON by default as requested
        self.conn_mode_var = tk.StringVar(value="local")  # "local" | "s3"
        self.current_verdict: Optional[Verdict] = None
        self.current_target_info: dict[str, Any] = {}
        self.current_matched_info: Optional[dict[str, Any]] = None

        # Batch audit state variables
        self.batch_path_var = tk.StringVar(value="data/real duplicate folder")
        self.batch_auto_reg_var = tk.BooleanVar(value=False)
        self.batch_running = False
        self.batch_stop_requested = False
        self.batch_results: list[dict[str, Any]] = []
        self.current_batch_selection: Optional[dict[str, Any]] = None
        self.batch_filter_mode = "all"
        self.batch_search_var = tk.StringVar()
        self.batch_status_var = tk.StringVar(
            value="Ready. Select folder or S3 URI and click 'Start Batch Audit'."
        )

        self._build_ui()
        self._refresh_stats()

        # Prompt user on startup every time GUI is opened
        if show_prompt:
            self.root.after(350, self._show_audio_prompt)

    def _show_audio_prompt(self) -> None:
        """Prompt user on launch that audio detection is active by default."""
        msg = (
            "Acoustic Audio Detection (Chromaprint) is ON by default.\n\n"
            "Tip: You can toggle Audio Detection OFF at any time using the "
            "'Audio: ON/OFF' button in the toolbar for faster and more accurate "
            "visual-only results (e.g. if clips have sped-up or replaced audio)."
        )
        messagebox.showinfo("Audio Detection Active", msg)

    def _build_pipeline(self) -> Pipeline:
        """Build pipeline instance with the current audio toggle and connection settings."""
        cfg = load_config(self.config_path)
        detectors = dict(cfg.detectors)
        audio_cfg = cfg.audio_chromaprint
        detectors["audio_chromaprint"] = AudioChromaprintConfig(
            enabled=self.audio_enabled_var.get(),
            ber_threshold=audio_cfg.ber_threshold,
            flag_threshold=audio_cfg.flag_threshold,
            review_threshold=audio_cfg.review_threshold,
        )
        custom_cfg = Config(
            pipeline=cfg.pipeline,
            source=cfg.source,
            store=cfg.store,
            detectors=detectors,
        )
        return load_pipeline(config=custom_cfg)

    def _build_ui(self) -> None:
        # Top banner with store path and controls
        banner_frame = ttk.Frame(self.root, padding="8")
        banner_frame.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(
            banner_frame,
            text="ClipGuard Detection Console",
            font=("Arial", 14, "bold"),
        ).pack(side=tk.LEFT)

        # Mode Indicator & Store Path on Right
        right_frame = ttk.Frame(banner_frame)
        right_frame.pack(side=tk.RIGHT)

        self.db_label_var = tk.StringVar(value=f"Store: {self.cfg.store.path}")
        ttk.Label(
            right_frame, textvariable=self.db_label_var, font=("Arial", 9), foreground="#555"
        ).pack(side=tk.RIGHT, padx=4)

        # Toolbar Frame directly below header
        tb_frame = ttk.LabelFrame(self.root, text="System Controls & Detection Toggles", padding="6")
        tb_frame.pack(fill=tk.X, padx=8, pady=(0, 4))

        # 1. Audio Toggle Button
        ttk.Label(tb_frame, text="Audio Detector:").pack(side=tk.LEFT, padx=(4, 2))
        self.audio_btn = tk.Button(
            tb_frame,
            text="🔊 Audio Detection: ON",
            bg="#e8f5e9",
            fg="#2e7d32",
            font=("Arial", 9, "bold"),
            relief=tk.RAISED,
            padx=8,
            pady=2,
            command=self._toggle_audio,
        )
        self.audio_btn.pack(side=tk.LEFT, padx=(0, 16))

        # 2. Connection Mode Toggle (Local vs S3)
        ttk.Label(tb_frame, text="Storage Connection:").pack(side=tk.LEFT, padx=(4, 2))
        self.mode_btn = tk.Button(
            tb_frame,
            text="📁 Mode: LOCAL DISK",
            bg="#e3f2fd",
            fg="#1565c0",
            font=("Arial", 9, "bold"),
            relief=tk.RAISED,
            padx=8,
            pady=2,
            command=self._toggle_connection_mode,
        )
        self.mode_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.mode_desc_var = tk.StringVar(value="Local filesystem storage")
        ttk.Label(
            tb_frame,
            textvariable=self.mode_desc_var,
            font=("Arial", 9, "italic"),
            foreground="#666",
        ).pack(side=tk.LEFT, padx=4)

        # Tabbed Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Tab 1: Check Single Video
        self.tab_check = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_check, text=" 🔍 Check Single Video ")
        self._build_check_tab()

        # Tab 2: Batch Folder / S3 Audit
        self.tab_batch = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_batch, text=" 📂 Batch Folder / S3 Audit ")
        self._build_batch_tab()

        # Tab 3: Index Library
        self.tab_index = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_index, text=" 📥 Index Library ")
        self._build_index_tab()

        # Tab 4: Database Stats
        self.tab_stats = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_stats, text=" 📊 Database Stats ")
        self._build_stats_tab()

        # Bottom status bar
        self.status_var = tk.StringVar(value="Ready. Audio detection is ON by default.")
        status_bar = ttk.Label(
            self.root,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding="4",
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _toggle_audio(self) -> None:
        """Toggle audio chromaprint detection on/off."""
        is_on = self.audio_enabled_var.get()
        new_val = not is_on
        self.audio_enabled_var.set(new_val)
        if new_val:
            self.audio_btn.config(
                text="🔊 Audio Detection: ON",
                bg="#e8f5e9",
                fg="#2e7d32",
            )
            self.status_var.set("Audio detection enabled (Acoustic Chromaprint active).")
        else:
            self.audio_btn.config(
                text="🔇 Audio Detection: OFF",
                bg="#ffebee",
                fg="#c62828",
            )
            self.status_var.set(
                "Audio detection disabled (Visual pHash & Exact SHA-256 only for faster/more accurate results)."
            )

    def _toggle_connection_mode(self) -> None:
        """Toggle storage connection between Local Disk and AWS S3 across all tabs."""
        current = self.conn_mode_var.get()
        if current == "local":
            self.conn_mode_var.set("s3")
            self.mode_btn.config(
                text="☁️ Mode: AWS S3 CLOUD",
                bg="#fff3e0",
                fg="#e65100",
            )
            bucket_str = self.cfg.source.bucket or "s3-bucket"
            self.mode_desc_var.set(f"AWS S3 Cloud Mode ({bucket_str})")
            self.check_prompt_lbl.config(text="S3 Video URI (e.g. s3://bucket/key.mp4):")
            self.browse_btn.config(text="Browse S3...")

            if hasattr(self, "batch_prompt_lbl"):
                self.batch_prompt_lbl.config(
                    text="Amazon S3 Video Prefix to Audit (e.g. s3://bucket/prefix/):"
                )
            if hasattr(self, "batch_browse_btn"):
                self.batch_browse_btn.config(text="Browse S3...")
            default_s3 = (
                f"s3://{self.cfg.source.bucket}/{self.cfg.source.prefix}"
                if self.cfg.source.bucket
                else "s3://"
            )
            if hasattr(self, "batch_path_var"):
                cur_b = self.batch_path_var.get().strip()
                if not cur_b or not cur_b.startswith("s3://"):
                    self.batch_path_var.set(default_s3)

            if hasattr(self, "index_prompt_lbl"):
                self.index_prompt_lbl.config(
                    text="Amazon S3 Bucket / Prefix URI to Index (e.g. s3://bucket/prefix/):"
                )
            if hasattr(self, "index_path_var"):
                cur_idx = self.index_path_var.get().strip()
                if not cur_idx or not cur_idx.startswith("s3://"):
                    self.index_path_var.set(default_s3)

            self.status_var.set("Switched to AWS S3 Cloud connection mode.")
        else:
            self.conn_mode_var.set("local")
            self.mode_btn.config(
                text="📁 Mode: LOCAL DISK",
                bg="#e3f2fd",
                fg="#1565c0",
            )
            self.mode_desc_var.set("Local filesystem storage")
            self.check_prompt_lbl.config(text="Local Video File:")
            self.browse_btn.config(text="Browse File...")

            if hasattr(self, "batch_prompt_lbl"):
                self.batch_prompt_lbl.config(text="Local Video Folder to Audit:")
            if hasattr(self, "batch_browse_btn"):
                self.batch_browse_btn.config(text="Browse Folder...")
            if hasattr(self, "batch_path_var"):
                cur_b = self.batch_path_var.get().strip()
                if cur_b.startswith("s3://"):
                    self.batch_path_var.set("data/real duplicate folder")

            if hasattr(self, "index_prompt_lbl"):
                self.index_prompt_lbl.config(text="Local Folder or File to Index:")
            if hasattr(self, "index_path_var"):
                cur_idx = self.index_path_var.get().strip()
                if cur_idx.startswith("s3://"):
                    self.index_path_var.set(self.cfg.source.path)

            self.status_var.set("Switched to Local Filesystem storage mode.")

    # ------------------ TAB 1: CHECK VIDEO ------------------
    def _build_check_tab(self) -> None:
        # File selector row
        sel_frame = ttk.LabelFrame(self.tab_check, text="Incoming Video to Check", padding="8")
        sel_frame.pack(fill=tk.X, pady=4)

        self.check_prompt_lbl = ttk.Label(sel_frame, text="Local Video File:")
        self.check_prompt_lbl.pack(anchor=tk.W, pady=(0, 2))

        entry_frame = ttk.Frame(sel_frame)
        entry_frame.pack(fill=tk.X)

        self.check_file_var = tk.StringVar()
        entry = ttk.Entry(entry_frame, textvariable=self.check_file_var, font=("Arial", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.browse_btn = ttk.Button(
            entry_frame, text="Browse File...", command=self._browse_check_file
        )
        self.browse_btn.pack(side=tk.LEFT, padx=2)

        self.auto_reg_var = tk.BooleanVar(value=True)
        auto_reg_cb = ttk.Checkbutton(
            entry_frame, text="Register if clear", variable=self.auto_reg_var
        )
        auto_reg_cb.pack(side=tk.LEFT, padx=6)

        check_btn = ttk.Button(
            entry_frame,
            text="▶ Check Video",
            command=self._run_check_async,
        )
        check_btn.pack(side=tk.LEFT, padx=4)

        # Results area
        res_frame = ttk.LabelFrame(self.tab_check, text="Verdict & Forensic Triage", padding="8")
        res_frame.pack(fill=tk.BOTH, expand=True, pady=6)

        # Big verdict banner
        self.verdict_badge = tk.Label(
            res_frame,
            text="NO VIDEO CHECKED",
            font=("Arial", 15, "bold"),
            bg="#e0e0e0",
            fg="#333",
            padx=12,
            pady=5,
            relief=tk.GROOVE,
        )
        self.verdict_badge.pack(fill=tk.X, pady=(0, 6))

        # Two-column grid for Layer & Seller Intent
        grid_frame = ttk.Frame(res_frame)
        grid_frame.pack(fill=tk.X, pady=2)

        ttk.Label(grid_frame, text="Detection Layer:", font=("Arial", 9, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=2
        )
        self.layer_val = ttk.Label(grid_frame, text="—", font=("Arial", 9))
        self.layer_val.grid(row=0, column=1, sticky=tk.W, padx=8, pady=2)

        ttk.Label(grid_frame, text="Seller Intent:", font=("Arial", 9, "bold")).grid(
            row=1, column=0, sticky=tk.W, pady=2
        )
        self.intent_val = ttk.Label(grid_frame, text="—", font=("Arial", 9))
        self.intent_val.grid(row=1, column=1, sticky=tk.W, padx=8, pady=2)

        ttk.Label(grid_frame, text="Operational Guidance:", font=("Arial", 9, "bold")).grid(
            row=2, column=0, sticky=tk.W, pady=2
        )
        self.guidance_val = ttk.Label(
            grid_frame, text="—", font=("Arial", 9, "italic"), wraplength=550
        )
        self.guidance_val.grid(row=2, column=1, sticky=tk.W, padx=8, pady=2)

        # MATCH COMPARISON CARD (Crucial User Requirement)
        self.match_card = ttk.LabelFrame(
            res_frame, text="Matched Database Video & File Identification", padding="8"
        )
        self.match_card.pack(fill=tk.X, pady=6)

        self.match_current_lbl = ttk.Label(
            self.match_card,
            text="Current Video: —",
            font=("Arial", 9),
        )
        self.match_current_lbl.pack(anchor=tk.W, pady=1)

        self.match_orig_lbl = ttk.Label(
            self.match_card,
            text="Matched Reference: —",
            font=("Arial", 9, "bold"),
            foreground="#b71c1c",
        )
        self.match_orig_lbl.pack(anchor=tk.W, pady=1)

        # Clickable Link Label for the duplicate video
        link_row = ttk.Frame(self.match_card)
        link_row.pack(fill=tk.X, pady=2)

        ttk.Label(link_row, text="Path / Link: ", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.matched_link_lbl = tk.Label(
            link_row,
            text="No duplicate matched",
            font=("Arial", 9, "underline"),
            fg="#1a73e8",
            cursor="hand2",
            wraplength=600,
            justify=tk.LEFT,
        )
        self.matched_link_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.matched_link_lbl.bind("<Button-1>", lambda e: self._open_matched_video())

        # Action Buttons row
        btn_row = ttk.Frame(self.match_card)
        btn_row.pack(fill=tk.X, pady=(6, 2))

        self.open_vid_btn = ttk.Button(
            btn_row,
            text="▶ Play Matched Video",
            command=self._open_matched_video,
            state=tk.DISABLED,
        )
        self.open_vid_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.reveal_btn = ttk.Button(
            btn_row,
            text="📂 Reveal in File Explorer",
            command=self._reveal_in_explorer,
            state=tk.DISABLED,
        )
        self.reveal_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.open_folder_btn = ttk.Button(
            btn_row,
            text="📁 Open Folder",
            command=self._open_matched_folder,
            state=tk.DISABLED,
        )
        self.open_folder_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.report_btn = ttk.Button(
            btn_row,
            text="📄 Generate Smart Report",
            command=self._open_smart_report,
            state=tk.DISABLED,
        )
        self.report_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Details Text area
        ttk.Label(res_frame, text="Evidence & Detection Log:", font=("Arial", 9, "bold")).pack(
            anchor=tk.W, pady=(4, 2)
        )
        self.details_txt = tk.Text(res_frame, height=6, font=("Consolas", 9), wrap=tk.WORD)
        self.details_txt.pack(fill=tk.BOTH, expand=True)

    def _browse_check_file(self) -> None:
        if self.conn_mode_var.get() == "s3":
            # S3 Input Mode
            default_uri = (
                f"s3://{self.cfg.source.bucket}/{self.cfg.source.prefix}"
                if self.cfg.source.bucket
                else "s3://"
            )
            uri = simpledialog.askstring(
                "S3 Video URI",
                "Enter Amazon S3 Video URI (e.g. s3://bucket/folder/video.mp4):",
                initialvalue=default_uri,
            )
            if uri:
                self.check_file_var.set(uri.strip())
        else:
            # Local File Mode
            path = filedialog.askopenfilename(
                title="Select Video to Check",
                filetypes=[
                    ("Video files", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                    ("All files", "*.*"),
                ],
            )
            if path:
                self.check_file_var.set(path)

    def _run_check_async(self) -> None:
        target_str = self.check_file_var.get().strip()
        if not target_str:
            messagebox.showwarning("Input Needed", "Please select or enter a video file to check.")
            return

        is_s3 = target_str.startswith("s3://")
        if not is_s3 and not Path(target_str).exists():
            messagebox.showerror("Error", f"Local file does not exist:\n{target_str}")
            return

        display_name = (
            Path(target_str).name if not is_s3 else Path(urlparse(target_str).path).name
        )
        self.status_var.set(f"Checking {display_name}...")
        self.verdict_badge.config(text="PROCESSING...", bg="#ffd54f", fg="#333")
        self.open_vid_btn.config(state=tk.DISABLED)
        self.reveal_btn.config(state=tk.DISABLED)
        self.open_folder_btn.config(state=tk.DISABLED)
        self.report_btn.config(state=tk.DISABLED)

        threading.Thread(
            target=self._run_check_worker, args=(target_str, is_s3), daemon=True
        ).start()

    def _run_check_worker(self, target_str: str, is_s3: bool) -> None:
        pipeline = None
        store = None
        try:
            pipeline = self._build_pipeline()
            store = pipeline.store

            target_info: dict[str, Any] = {}

            if is_s3:
                s3_src = S3Source(
                    bucket=self.cfg.source.bucket,
                    region=self.cfg.source.region,
                    endpoint_url=self.cfg.source.endpoint_url,
                )
                with s3_src.materialize(target_str) as local_temp:
                    sha256 = s3_src._compute_s3_sha256(
                        urlparse(target_str).netloc, urlparse(target_str).path.lstrip("/")
                    )
                    clip = ClipRef(
                        clip_id=sha256,
                        uri=target_str,
                        local_path=local_temp,
                        status="pending",
                    )
                    target_info = {
                        "filename": Path(urlparse(target_str).path).name,
                        "path": target_str,
                        "size_str": format_file_size(local_temp.stat().st_size),
                        "is_s3": True,
                    }
                    verdict = pipeline.check(clip, register=self.auto_reg_var.get())
            else:
                p = Path(target_str)
                clip = clip_from_path(p, status="pending")
                target_info = {
                    "filename": p.name,
                    "path": str(p.resolve()),
                    "size_str": format_file_size(p.stat().st_size),
                    "is_s3": False,
                }
                verdict = pipeline.check(clip, register=self.auto_reg_var.get())

            # Look up matched reference in store
            matched_info = None
            matched_id = None
            if verdict.matches:
                matched_id = verdict.matches[0].matched_clip_id
            elif verdict.verdict == "already_indexed":
                matched_id = verdict.clip_id

            if matched_id and store is not None:
                orig_clip = store.clips.get(matched_id)
                if orig_clip:
                    clean_path = clean_uri_to_display_path(orig_clip.uri)
                    size_s = ""
                    if Path(clean_path).exists():
                        size_s = format_file_size(Path(clean_path).stat().st_size)
                    matched_info = {
                        "clip_id": orig_clip.clip_id,
                        "uri": orig_clip.uri,
                        "display_path": clean_path,
                        "filename": Path(clean_path).name,
                        "status": orig_clip.status,
                        "size_str": size_s,
                        "is_local": not clean_path.startswith("s3://"),
                    }

            self.root.after(
                0, self._display_check_result, verdict, target_info, matched_info
            )
        except Exception as exc:
            self.root.after(0, self._display_check_error, str(exc))
        finally:
            if pipeline is not None:
                pipeline.store.close()

    def _display_check_error(self, err_msg: str) -> None:
        self.status_var.set("Check failed.")
        self.verdict_badge.config(text="ERROR", bg="#ef5350", fg="white")
        self.details_txt.delete("1.0", tk.END)
        self.details_txt.insert(tk.END, f"Error checking file:\n{err_msg}")

    def _display_check_result(
        self,
        verdict: Verdict,
        target_info: dict[str, Any],
        matched_info: Optional[dict[str, Any]],
    ) -> None:
        self.current_verdict = verdict
        self.current_target_info = target_info
        self.current_matched_info = matched_info

        self.status_var.set(f"Verdict complete: {verdict.verdict.upper()}")

        # Color-coded badge
        colors = {
            "clear": ("#66bb6a", "white"),
            "already_indexed": ("#42a5f5", "white"),
            "duplicate": ("#ef5350", "white"),
            "review": ("#ffa726", "black"),
            "error": ("#b71c1c", "white"),
        }
        bg, fg = colors.get(verdict.verdict, ("#e0e0e0", "black"))
        self.verdict_badge.config(text=f"VERDICT: {verdict.verdict.upper()}", bg=bg, fg=fg)

        # Layer description
        layer_names = {
            "exact": "Layer 1: Exact Byte Hash (exact_sha256)",
            "visual": "Layer 2: Visual Perceptual pHash (frame_phash)",
            "audio": "Layer 3: Acoustic Chromaprint (audio_chromaprint)",
            "none": "None (No duplicate matched)",
        }
        self.layer_val.config(
            text=layer_names.get(verdict.layer or "none", verdict.layer or "none")
        )

        # Intent Assessment & Operational guidance
        if verdict.intent_assessment == "unedited_likely_unaware":
            self.intent_val.config(
                text="UNEDITED DUPLICATE (Accidental / Unaware)", foreground="#1565c0"
            )
            self.guidance_val.config(
                text=(
                    "Identical bit-for-bit file. The vendor likely resubmitted the clip accidentally. "
                    "Standard procedure: politely notify vendor without contractual penalties."
                ),
                foreground="#1565c0",
            )
        elif verdict.intent_assessment == "edited_likely_intentional":
            self.intent_val.config(
                text="EDITED DERIVATIVE (Intentional Alteration / Evasion)", foreground="#c62828"
            )
            self.guidance_val.config(
                text=(
                    "Visual or audio content matches, but bytes were altered (re-encoded, trimmed, or altered). "
                    "This indicates intentional alteration or relabeling. Route to management/operations."
                ),
                foreground="#c62828",
            )
        elif verdict.verdict == "clear":
            self.intent_val.config(text="CLEAN (No Duplicate)", foreground="#2e7d32")
            self.guidance_val.config(
                text="Clip is unique across all detection layers. Safe to approve for robotics.",
                foreground="#2e7d32",
            )
        else:
            self.intent_val.config(text="BORDERLINE / REVIEW", foreground="#e65100")
            self.guidance_val.config(
                text="Score falls in the human review queue. Inspect side-by-side frames.",
                foreground="#e65100",
            )

        # MATCH CARD DETAILS
        cur_name = target_info.get("filename", "Unknown")
        cur_size = target_info.get("size_str", "")
        self.match_current_lbl.config(
            text=f"Current Checked Video: {cur_name} ({cur_size})"
        )

        if matched_info:
            orig_name = matched_info.get("filename", "Unknown")
            orig_stat = matched_info.get("status", "approved").upper()
            orig_path = matched_info.get("display_path", "")
            conf_str = ""
            if verdict.matches:
                conf_str = f" | Confidence: {verdict.matches[0].confidence * 100:.1f}%"
            self.match_orig_lbl.config(
                text=f"MATCHED EXISTING VIDEO: {orig_name} [{orig_stat}]{conf_str}",
                foreground="#c62828",
            )
            self.matched_link_lbl.config(
                text=f"🔗 {orig_path} (Click to open)",
                fg="#1a73e8",
            )
            self.open_vid_btn.config(state=tk.NORMAL)
            if matched_info.get("is_local") and Path(orig_path).exists():
                self.reveal_btn.config(state=tk.NORMAL)
                self.open_folder_btn.config(state=tk.NORMAL)
            else:
                self.reveal_btn.config(state=tk.DISABLED)
                self.open_folder_btn.config(state=tk.DISABLED)
        else:
            self.match_orig_lbl.config(
                text="MATCHED EXISTING VIDEO: None (No duplicate found in database)",
                foreground="#2e7d32",
            )
            self.matched_link_lbl.config(
                text="Unique footage — no match URL",
                fg="#777",
            )
            self.open_vid_btn.config(state=tk.DISABLED)
            self.reveal_btn.config(state=tk.DISABLED)
            self.open_folder_btn.config(state=tk.DISABLED)

        # Always enable Smart Report button once checked
        self.report_btn.config(state=tk.NORMAL)

        # Details text
        self.details_txt.delete("1.0", tk.END)
        self.details_txt.insert(tk.END, f"Reason: {verdict.reason}\n")
        self.details_txt.insert(tk.END, f"Target Clip SHA-256: {verdict.clip_id}\n\n")

        if verdict.matches:
            self.details_txt.insert(tk.END, "Candidate Matches:\n")
            for m in verdict.matches:
                st = f" [Status: {m.matched_status}]" if m.matched_status else ""
                self.details_txt.insert(
                    tk.END,
                    f"  • Detector : {m.detector}\n"
                    f"    Matched ID: {m.matched_clip_id}\n"
                    f"    Confidence: {m.confidence:.3f}{st}\n"
                    f"    Evidence  : {json.dumps(dict(m.evidence))}\n\n",
                )
        else:
            self.details_txt.insert(tk.END, "No candidate matches detected across active layers.\n")

        self._refresh_stats()

    def _launch_media_file(self, path_str: str) -> None:
        """Launch video in system player or S3 presigned browser URL."""
        if not path_str:
            return
        if path_str.startswith("s3://"):
            try:
                s3_src = S3Source(
                    bucket=self.cfg.source.bucket,
                    region=self.cfg.source.region,
                    endpoint_url=self.cfg.source.endpoint_url,
                )
                url = s3_src.generate_presigned_url(path_str)
                webbrowser.open(url)
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to open S3 presigned URL:\n{exc}")
        else:
            p = Path(path_str).resolve()
            if p.exists():
                try:
                    os.startfile(str(p))
                except Exception as exc:
                    messagebox.showerror("Error", f"Failed to launch video player:\n{exc}")
            else:
                messagebox.showwarning(
                    "File Not Found", f"Matched file path does not exist on disk:\n{p}"
                )

    def _reveal_media_file(self, path_str: str) -> None:
        """Reveal file in Windows Explorer or open S3 link."""
        if not path_str:
            return
        if path_str.startswith("s3://"):
            try:
                s3_src = S3Source(
                    bucket=self.cfg.source.bucket,
                    region=self.cfg.source.region,
                    endpoint_url=self.cfg.source.endpoint_url,
                )
                url = s3_src.generate_presigned_url(path_str)
                webbrowser.open(url)
            except Exception as exc:
                messagebox.showinfo(
                    "Amazon S3 Video",
                    f"Matched clip is stored in Amazon S3:\n{path_str}\n\nPresigned URL error: {exc}",
                )
            return

        p = Path(path_str).resolve()
        if p.exists() and p.is_file():
            try:
                cmd = f'explorer.exe /select,"{p}"'
                subprocess.Popen(cmd)
            except Exception as exc:
                try:
                    os.startfile(str(p.parent))
                except Exception:
                    messagebox.showerror("Error", f"Failed to reveal in Explorer:\n{exc}")
        elif p.exists() and p.is_dir():
            os.startfile(str(p))
        elif p.parent.exists():
            os.startfile(str(p.parent))
        else:
            messagebox.showwarning("File Not Found", f"File path does not exist on disk:\n{p}")

    def _open_media_folder(self, path_str: str) -> None:
        """Open directory in Windows Explorer."""
        if not path_str:
            return
        if path_str.startswith("s3://"):
            messagebox.showinfo("Amazon S3", f"Matched clip is stored in Amazon S3:\n{path_str}")
            return
        p = Path(path_str).resolve()
        folder = p.parent if p.is_file() or not p.exists() else p
        if folder.exists():
            try:
                os.startfile(str(folder))
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to open folder:\n{exc}")
        else:
            messagebox.showwarning("Folder Not Found", f"Folder does not exist:\n{folder}")

    def _open_matched_video(self) -> None:
        if not self.current_matched_info:
            return
        self._launch_media_file(self.current_matched_info.get("display_path", ""))

    def _reveal_in_explorer(self) -> None:
        if not self.current_matched_info:
            return
        self._reveal_media_file(self.current_matched_info.get("display_path", ""))

    def _open_matched_folder(self) -> None:
        if not self.current_matched_info:
            return
        self._open_media_folder(self.current_matched_info.get("display_path", ""))

    def _show_smart_report_dialog(
        self, report_text: str, title: str = "ClipGuard — Smart Forensic Audit Report"
    ) -> None:
        """Open a dedicated window displaying the complete Smart Forensic Report."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("780x620")
        win.minsize(650, 480)

        # Top bar in modal
        top_bar = ttk.Frame(win, padding="8")
        top_bar.pack(fill=tk.X)

        ttk.Label(
            top_bar,
            text=title,
            font=("Arial", 11, "bold"),
        ).pack(side=tk.LEFT)

        def _export_report() -> None:
            save_path = filedialog.asksaveasfilename(
                title="Save Forensic Report",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("Markdown files", "*.md"), ("All files", "*.*")],
                initialfile="clipguard_forensic_report.txt",
            )
            if save_path:
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(report_text)
                messagebox.showinfo("Saved", f"Report saved successfully to:\n{save_path}")

        def _copy_clipboard() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(report_text)
            messagebox.showinfo("Copied", "Report copied to clipboard!")

        ttk.Button(top_bar, text="💾 Export Report...", command=_export_report).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(top_bar, text="📋 Copy to Clipboard", command=_copy_clipboard).pack(
            side=tk.RIGHT, padx=4
        )

        # Scrolled Text Box
        txt_frame = ttk.Frame(win, padding="8")
        txt_frame.pack(fill=tk.BOTH, expand=True)

        txt = tk.Text(txt_frame, font=("Consolas", 10), wrap=tk.NONE)
        scroll_y = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=txt.yview)
        scroll_x = ttk.Scrollbar(txt_frame, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        txt.insert(tk.END, report_text)
        txt.config(state=tk.DISABLED)

    def _open_smart_report(self) -> None:
        """Open Smart Forensic Report for the checked video in Tab 1."""
        if not self.current_verdict:
            return

        report_text = build_smart_report(
            target_path=self.check_file_var.get().strip(),
            verdict=self.current_verdict,
            target_info=self.current_target_info,
            matched_info=self.current_matched_info,
        )
        fname = self.current_target_info.get("filename", "Video")
        self._show_smart_report_dialog(
            report_text, title=f"ClipGuard — Forensic Report: {fname}"
        )

    # ------------------ TAB 2: BATCH FOLDER / S3 AUDIT ------------------
    def _build_batch_tab(self) -> None:
        # 1. Top Source Selector Frame
        ctrl_frame = ttk.LabelFrame(
            self.tab_batch,
            text="Batch Video Source to Audit (Local Directory or Amazon S3 Prefix)",
            padding="10",
        )
        ctrl_frame.pack(fill=tk.X, pady=(0, 6))

        self.batch_prompt_lbl = ttk.Label(ctrl_frame, text="Local Video Folder to Audit:")
        self.batch_prompt_lbl.pack(anchor=tk.W, pady=(0, 2))

        entry_row = ttk.Frame(ctrl_frame)
        entry_row.pack(fill=tk.X)

        entry = ttk.Entry(entry_row, textvariable=self.batch_path_var, font=("Arial", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.batch_browse_btn = ttk.Button(
            entry_row, text="Browse Folder...", command=self._browse_batch_folder
        )
        self.batch_browse_btn.pack(side=tk.LEFT, padx=2)

        auto_reg_cb = ttk.Checkbutton(
            entry_row, text="Register novel (if clear)", variable=self.batch_auto_reg_var
        )
        auto_reg_cb.pack(side=tk.LEFT, padx=6)

        self.batch_start_btn = ttk.Button(
            entry_row, text="🚀 Run Batch Audit", command=self._run_batch_audit_async
        )
        self.batch_start_btn.pack(side=tk.LEFT, padx=4)

        self.batch_stop_btn = ttk.Button(
            entry_row, text="⏹ Stop", command=self._stop_batch_audit, state=tk.DISABLED
        )
        self.batch_stop_btn.pack(side=tk.LEFT, padx=2)

        # Quick Presets row
        preset_row = ttk.Frame(ctrl_frame)
        preset_row.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(
            preset_row,
            text="Quick Presets:",
            font=("Arial", 8, "italic"),
            foreground="#666",
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            preset_row,
            text="📁 Test Duplicates Folder",
            command=lambda: self._set_batch_preset("data/real duplicate folder", is_s3=False),
        ).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            preset_row,
            text="☁️ Configured S3 Source",
            command=lambda: self._set_batch_preset(
                f"s3://{self.cfg.source.bucket}/{self.cfg.source.prefix}"
                if self.cfg.source.bucket
                else "s3://",
                is_s3=True,
            ),
        ).pack(side=tk.LEFT, padx=2)

        # 2. Executive Summary Metrics Cards Banner
        summary_frame = ttk.LabelFrame(self.tab_batch, text="Batch Audit Summary Metrics", padding="6")
        summary_frame.pack(fill=tk.X, pady=(0, 6))

        cards_row = ttk.Frame(summary_frame)
        cards_row.pack(fill=tk.X, pady=2)

        self.card_scanned = tk.Label(
            cards_row,
            text="📦 Scanned: 0 / 0",
            bg="#f5f5f5",
            fg="#333",
            font=("Arial", 9, "bold"),
            padx=8,
            pady=3,
            relief=tk.RIDGE,
        )
        self.card_scanned.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        self.card_clean = tk.Label(
            cards_row,
            text="🟢 Originals / Clear: 0",
            bg="#e8f5e9",
            fg="#2e7d32",
            font=("Arial", 9, "bold"),
            padx=8,
            pady=3,
            relief=tk.RIDGE,
        )
        self.card_clean.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        self.card_dup = tk.Label(
            cards_row,
            text="🔴 Duplicates: 0",
            bg="#ffebee",
            fg="#c62828",
            font=("Arial", 9, "bold"),
            padx=8,
            pady=3,
            relief=tk.RIDGE,
        )
        self.card_dup.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        self.card_indexed = tk.Label(
            cards_row,
            text="🟣 Already Indexed: 0",
            bg="#f3e5f5",
            fg="#6a1b9a",
            font=("Arial", 9, "bold"),
            padx=8,
            pady=3,
            relief=tk.RIDGE,
        )
        self.card_indexed.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        self.card_review = tk.Label(
            cards_row,
            text="🟡 Under Review: 0",
            bg="#fffde7",
            fg="#f57f17",
            font=("Arial", 9, "bold"),
            padx=8,
            pady=3,
            relief=tk.RIDGE,
        )
        self.card_review.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        prog_row = ttk.Frame(summary_frame)
        prog_row.pack(fill=tk.X, pady=(4, 2))

        self.batch_progressbar = ttk.Progressbar(prog_row, mode="determinate")
        self.batch_progressbar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.batch_status_lbl = ttk.Label(
            prog_row, textvariable=self.batch_status_var, font=("Arial", 9, "italic")
        )
        self.batch_status_lbl.pack(side=tk.RIGHT)

        # 3. Master-Detail Paned Window
        paned = ttk.PanedWindow(self.tab_batch, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=2)

        # Top Pane: Master Table
        top_pane = ttk.Frame(paned)
        paned.add(top_pane, weight=5)

        # Filter & Search Toolbar
        filter_row = ttk.Frame(top_pane)
        filter_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(filter_row, text="Filter:", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.flt_all_btn = ttk.Button(
            filter_row, text="All (0)", command=lambda: self._set_batch_filter("all")
        )
        self.flt_all_btn.pack(side=tk.LEFT, padx=2)

        self.flt_dup_btn = ttk.Button(
            filter_row, text="🔴 Duplicates (0)", command=lambda: self._set_batch_filter("duplicate")
        )
        self.flt_dup_btn.pack(side=tk.LEFT, padx=2)

        self.flt_clean_btn = ttk.Button(
            filter_row, text="🟢 Clean (0)", command=lambda: self._set_batch_filter("clear")
        )
        self.flt_clean_btn.pack(side=tk.LEFT, padx=2)

        self.flt_indexed_btn = ttk.Button(
            filter_row,
            text="🟣 Already Indexed (0)",
            command=lambda: self._set_batch_filter("already_indexed"),
        )
        self.flt_indexed_btn.pack(side=tk.LEFT, padx=2)

        self.flt_review_btn = ttk.Button(
            filter_row, text="🟡 Review (0)", command=lambda: self._set_batch_filter("review")
        )
        self.flt_review_btn.pack(side=tk.LEFT, padx=2)

        # Search box
        search_f = ttk.Frame(filter_row)
        search_f.pack(side=tk.RIGHT)
        ttk.Label(search_f, text="Search:").pack(side=tk.LEFT, padx=2)
        search_entry = ttk.Entry(search_f, textvariable=self.batch_search_var, width=16)
        search_entry.pack(side=tk.LEFT, padx=2)
        self.batch_search_var.trace_add("write", lambda *_: self._render_batch_table())

        # Treeview Master Table
        tree_f = ttk.Frame(top_pane)
        tree_f.pack(fill=tk.BOTH, expand=True)

        cols = ("idx", "name", "verdict", "conf", "layer", "matched", "intent", "time")
        self.batch_tree = ttk.Treeview(tree_f, columns=cols, show="headings", height=7)
        self.batch_tree.heading("idx", text="#")
        self.batch_tree.heading("name", text="Scanned Filename")
        self.batch_tree.heading("verdict", text="Verdict")
        self.batch_tree.heading("conf", text="Confidence")
        self.batch_tree.heading("layer", text="Layer")
        self.batch_tree.heading("matched", text="Matched Reference Video")
        self.batch_tree.heading("intent", text="Seller Intent")
        self.batch_tree.heading("time", text="Latency")

        self.batch_tree.column("idx", width=35, anchor=tk.CENTER)
        self.batch_tree.column("name", width=220, anchor=tk.W)
        self.batch_tree.column("verdict", width=110, anchor=tk.CENTER)
        self.batch_tree.column("conf", width=80, anchor=tk.CENTER)
        self.batch_tree.column("layer", width=80, anchor=tk.CENTER)
        self.batch_tree.column("matched", width=220, anchor=tk.W)
        self.batch_tree.column("intent", width=160, anchor=tk.W)
        self.batch_tree.column("time", width=65, anchor=tk.CENTER)

        tree_scrl_y = ttk.Scrollbar(tree_f, orient=tk.VERTICAL, command=self.batch_tree.yview)
        tree_scrl_x = ttk.Scrollbar(tree_f, orient=tk.HORIZONTAL, command=self.batch_tree.xview)
        self.batch_tree.configure(
            yscrollcommand=tree_scrl_y.set, xscrollcommand=tree_scrl_x.set
        )

        tree_scrl_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scrl_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.batch_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.batch_tree.tag_configure("tag_duplicate", foreground="#b71c1c", background="#fff5f5")
        self.batch_tree.tag_configure("tag_clear", foreground="#2e7d32", background="#f5fff5")
        self.batch_tree.tag_configure(
            "tag_already_indexed", foreground="#6a1b9a", background="#fcf5ff"
        )
        self.batch_tree.tag_configure("tag_review", foreground="#f57f17", background="#fffff5")

        self.batch_tree.bind("<<TreeviewSelect>>", self._on_batch_row_selected)
        self.batch_tree.bind("<Double-1>", lambda _: self._open_batch_item_report())

        # Bottom Pane: Individual File Inspector & Evidence Card
        bottom_pane = ttk.LabelFrame(
            paned, text="Individual File Forensic Inspector & Evidence Card", padding="8"
        )
        paned.add(bottom_pane, weight=4)

        # Large verdict badge
        self.batch_badge = tk.Label(
            bottom_pane,
            text="NO VIDEO SELECTED — Click any row above to inspect & generate report",
            font=("Arial", 11, "bold"),
            bg="#e0e0e0",
            fg="#444",
            padx=8,
            pady=3,
            relief=tk.GROOVE,
        )
        self.batch_badge.pack(fill=tk.X, pady=(0, 4))

        # Two-column grid
        info_grid = ttk.Frame(bottom_pane)
        info_grid.pack(fill=tk.X, pady=2)

        ttk.Label(info_grid, text="Current Video:", font=("Arial", 9, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=1
        )
        self.batch_cur_lbl = ttk.Label(info_grid, text="—", font=("Arial", 9))
        self.batch_cur_lbl.grid(row=0, column=1, sticky=tk.W, padx=6, pady=1)

        ttk.Label(info_grid, text="Matched Reference:", font=("Arial", 9, "bold")).grid(
            row=1, column=0, sticky=tk.W, pady=1
        )
        self.batch_matched_lbl = ttk.Label(
            info_grid, text="—", font=("Arial", 9, "bold"), foreground="#c62828"
        )
        self.batch_matched_lbl.grid(row=1, column=1, sticky=tk.W, padx=6, pady=1)

        ttk.Label(info_grid, text="Reference Link:", font=("Arial", 9, "bold")).grid(
            row=2, column=0, sticky=tk.W, pady=1
        )
        self.batch_link_lbl = tk.Label(
            info_grid, text="—", font=("Arial", 9), fg="#1a73e8", cursor="hand2"
        )
        self.batch_link_lbl.grid(row=2, column=1, sticky=tk.W, padx=6, pady=1)
        self.batch_link_lbl.bind("<Button-1>", lambda _: self._play_batch_matched())

        ttk.Label(info_grid, text="Layer & Intent:", font=("Arial", 9, "bold")).grid(
            row=3, column=0, sticky=tk.W, pady=1
        )
        self.batch_layer_intent_lbl = ttk.Label(info_grid, text="—", font=("Arial", 9))
        self.batch_layer_intent_lbl.grid(row=3, column=1, sticky=tk.W, padx=6, pady=1)

        ttk.Label(info_grid, text="Operational Triage:", font=("Arial", 9, "bold")).grid(
            row=4, column=0, sticky=tk.W, pady=1
        )
        self.batch_guidance_lbl = ttk.Label(
            info_grid, text="—", font=("Arial", 9, "italic"), wraplength=550
        )
        self.batch_guidance_lbl.grid(row=4, column=1, sticky=tk.W, padx=6, pady=1)

        # Action Buttons Row
        act_row = ttk.Frame(bottom_pane)
        act_row.pack(fill=tk.X, pady=(4, 2))

        self.batch_play_btn = ttk.Button(
            act_row,
            text="▶ Play Matched Video",
            command=self._play_batch_matched,
            state=tk.DISABLED,
        )
        self.batch_play_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.batch_reveal_btn = ttk.Button(
            act_row,
            text="📂 Reveal in Explorer",
            command=self._reveal_batch_matched,
            state=tk.DISABLED,
        )
        self.batch_reveal_btn.pack(side=tk.LEFT, padx=4)

        self.batch_folder_btn = ttk.Button(
            act_row,
            text="📁 Open Folder",
            command=self._open_batch_matched_folder,
            state=tk.DISABLED,
        )
        self.batch_folder_btn.pack(side=tk.LEFT, padx=4)

        self.batch_report_btn = ttk.Button(
            act_row,
            text="📄 Generate Smart Report",
            command=self._open_batch_item_report,
            state=tk.DISABLED,
        )
        self.batch_report_btn.pack(side=tk.LEFT, padx=4)

        self.batch_send_single_btn = ttk.Button(
            act_row,
            text="🔍 Open in Single Check",
            command=self._send_batch_item_to_single_check,
            state=tk.DISABLED,
        )
        self.batch_send_single_btn.pack(side=tk.LEFT, padx=4)

        self.batch_export_all_btn = ttk.Button(
            act_row,
            text="📋 Export Batch Report...",
            command=self._export_batch_audit_report,
            state=tk.DISABLED,
        )
        self.batch_export_all_btn.pack(side=tk.RIGHT, padx=4)

        self.batch_export_csv_btn = ttk.Button(
            act_row,
            text="📊 Export CSV...",
            command=self._export_batch_csv,
            state=tk.DISABLED,
        )
        self.batch_export_csv_btn.pack(side=tk.RIGHT, padx=4)

        # Evidence text area
        self.batch_evidence_txt = tk.Text(bottom_pane, height=4, font=("Consolas", 9), wrap=tk.WORD)
        self.batch_evidence_txt.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    def _browse_batch_folder(self) -> None:
        if self.conn_mode_var.get() == "s3":
            self._browse_batch_s3()
        else:
            path = filedialog.askdirectory(title="Select Video Folder to Audit")
            if path:
                self.batch_path_var.set(path)

    def _browse_batch_s3(self) -> None:
        default_uri = (
            f"s3://{self.cfg.source.bucket}/{self.cfg.source.prefix}"
            if self.cfg.source.bucket
            else "s3://"
        )
        cur = self.batch_path_var.get().strip()
        if cur.startswith("s3://"):
            default_uri = cur

        uri = simpledialog.askstring(
            "Amazon S3 Video Prefix to Audit",
            "Enter Amazon S3 Bucket & Prefix to Audit:\n(Example: s3://my-bucket/incoming/)",
            initialvalue=default_uri,
        )
        if uri:
            cleaned = uri.strip()
            if not cleaned.startswith("s3://"):
                cleaned = f"s3://{cleaned}"
            self.batch_path_var.set(cleaned)
            if self.conn_mode_var.get() != "s3":
                self._toggle_connection_mode()

    def _set_batch_preset(self, path_str: str, is_s3: bool) -> None:
        if is_s3:
            self.batch_path_var.set(path_str)
            if self.conn_mode_var.get() != "s3":
                self._toggle_connection_mode()
        else:
            p = Path(path_str).resolve()
            self.batch_path_var.set(str(p))
            if self.conn_mode_var.get() != "local":
                self._toggle_connection_mode()

    def _run_batch_audit_async(self) -> None:
        target_path = self.batch_path_var.get().strip()
        if not target_path:
            messagebox.showwarning("Input Needed", "Please select a directory or S3 URI to audit.")
            return

        is_s3 = target_path.startswith("s3://")
        if not is_s3 and not Path(target_path).exists():
            messagebox.showerror("Error", f"Local path does not exist:\n{target_path}")
            return

        self.batch_running = True
        self.batch_stop_requested = False
        self.batch_results.clear()
        self._render_batch_table()

        self.batch_start_btn.config(state=tk.DISABLED)
        self.batch_stop_btn.config(state=tk.NORMAL)
        self.batch_export_all_btn.config(state=tk.DISABLED)
        self.batch_export_csv_btn.config(state=tk.DISABLED)

        self.batch_status_var.set(f"Preparing batch audit for {target_path}...")
        self.card_scanned.config(text="📦 Scanned: 0 / ...")
        self.card_clean.config(text="🟢 Originals / Clear: 0")
        self.card_dup.config(text="🔴 Duplicates: 0")
        self.card_indexed.config(text="🟣 Already Indexed: 0")
        self.card_review.config(text="🟡 Under Review: 0")

        threading.Thread(
            target=self._run_batch_worker, args=(target_path, is_s3), daemon=True
        ).start()

    def _stop_batch_audit(self) -> None:
        if self.batch_running:
            self.batch_stop_requested = True
            self.batch_status_var.set("Stopping audit after current video...")

    def _run_batch_worker(self, target_path: str, is_s3: bool) -> None:
        pipeline = None
        try:
            pipeline = self._build_pipeline()
            store = pipeline.store

            # Discover targets
            tasks: list[tuple[str, Any]] = []
            if is_s3:
                p = urlparse(target_path)
                bucket = p.netloc
                prefix = p.path.lstrip("/")
                if not bucket and target_path.startswith("s3://"):
                    raw = target_path[5:]
                    if "/" in raw:
                        bucket, prefix = raw.split("/", 1)
                    else:
                        bucket, prefix = raw, ""

                s3_src = S3Source(
                    bucket=bucket,
                    prefix=prefix,
                    region=self.cfg.source.region,
                    endpoint_url=self.cfg.source.endpoint_url,
                )
                for c in s3_src.iter_clips():
                    fname = Path(c.uri).name or c.uri
                    tasks.append((fname, ("s3", s3_src, c)))
            else:
                src = LocalSource(Path(target_path))
                for p in src.iter_paths():
                    tasks.append((p.name, ("local", p)))

            total_items = len(tasks)
            if total_items == 0:
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Batch Audit", f"No supported video files found in:\n{target_path}"
                    ),
                )
                self.root.after(0, self._on_batch_audit_complete, 0)
                return

            self.root.after(
                0,
                lambda t=total_items: (
                    self.batch_progressbar.config(maximum=t, value=0),
                    self.batch_status_var.set(f"Auditing {t} video(s)..."),
                    self.card_scanned.config(text=f"📦 Scanned: 0 / {t}"),
                ),
            )

            def _audit_item(task_tuple: tuple[int, str, Any]) -> Optional[dict[str, Any]]:
                if self.batch_stop_requested:
                    return None
                item_seq, fname, task_data = task_tuple
                t0 = time.perf_counter()
                task_type = task_data[0]
                clip = None
                target_info = {}

                if task_type == "local":
                    local_p = task_data[1]
                    clip = clip_from_path(local_p, status="pending")
                    target_info = {
                        "filename": local_p.name,
                        "path": str(local_p.resolve()),
                        "size_str": format_file_size(local_p.stat().st_size),
                        "is_s3": False,
                    }
                    verdict = pipeline.check(clip, register=self.batch_auto_reg_var.get())
                else:
                    s3_src, c = task_data[1], task_data[2]
                    with s3_src.materialize(c.uri) as local_temp:
                        clip = ClipRef(
                            clip_id=c.clip_id,
                            uri=c.uri,
                            local_path=local_temp,
                            status="pending",
                        )
                        target_info = {
                            "filename": Path(urlparse(c.uri).path).name,
                            "path": c.uri,
                            "size_str": format_file_size(local_temp.stat().st_size),
                            "is_s3": True,
                        }
                        verdict = pipeline.check(clip, register=self.batch_auto_reg_var.get())

                elapsed_s = time.perf_counter() - t0

                matched_info = None
                matched_id = None
                if verdict.matches:
                    matched_id = verdict.matches[0].matched_clip_id
                elif verdict.verdict == "already_indexed":
                    matched_id = verdict.clip_id

                if matched_id and store is not None:
                    orig_clip = store.clips.get(matched_id)
                    if orig_clip:
                        clean_path = clean_uri_to_display_path(orig_clip.uri)
                        sz_str = ""
                        if Path(clean_path).exists():
                            sz_str = format_file_size(Path(clean_path).stat().st_size)
                        matched_info = {
                            "clip_id": orig_clip.clip_id,
                            "uri": orig_clip.uri,
                            "display_path": clean_path,
                            "filename": Path(clean_path).name,
                            "status": orig_clip.status,
                            "size_str": sz_str,
                            "is_local": not clean_path.startswith("s3://"),
                        }

                conf = (
                    verdict.matches[0].confidence
                    if verdict.matches
                    else (1.0 if verdict.verdict == "already_indexed" else 0.0)
                )
                return {
                    "index": item_seq,
                    "filename": target_info.get("filename", fname),
                    "path": target_info.get("path", ""),
                    "clip_id": clip.clip_id if clip else "",
                    "verdict": verdict,
                    "confidence": conf,
                    "layer": verdict.layer,
                    "intent": verdict.intent_assessment,
                    "target_info": target_info,
                    "matched_info": matched_info,
                    "elapsed_s": elapsed_s,
                }

            completed_count = 0
            batch_workers = min(4, max(1, len(tasks)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_workers) as executor:
                futures = {
                    executor.submit(_audit_item, (i + 1, fn, td)): i
                    for i, (fn, td) in enumerate(tasks)
                }
                for fut in concurrent.futures.as_completed(futures):
                    if self.batch_stop_requested:
                        break
                    res = fut.result()
                    if res is not None:
                        completed_count += 1
                        self.root.after(0, self._on_batch_clip_done, res, completed_count, total_items)

            self.root.after(0, self._on_batch_audit_complete, completed_count)
        except Exception as exc:
            self.root.after(0, self._on_batch_audit_error, str(exc))
        finally:
            if pipeline is not None:
                pipeline.store.close()

    def _on_batch_clip_done(
        self, item_dict: dict[str, Any], current_idx: int, total_items: int
    ) -> None:
        self.batch_results.append(item_dict)
        self.batch_progressbar.config(value=current_idx)
        self.batch_status_var.set(
            f"Auditing video {current_idx} of {total_items} ({item_dict['filename']})..."
        )
        self._update_batch_filter_buttons()
        self._render_batch_table()

    def _on_batch_audit_complete(self, count: int) -> None:
        self.batch_running = False
        self.batch_stop_requested = False
        self.batch_start_btn.config(state=tk.NORMAL)
        self.batch_stop_btn.config(state=tk.DISABLED)
        self.batch_export_all_btn.config(state=tk.NORMAL)
        self.batch_export_csv_btn.config(state=tk.NORMAL)
        if hasattr(self, "batch_progressbar") and self.batch_progressbar["maximum"] > 0:
            self.batch_progressbar.config(value=self.batch_progressbar["maximum"])
        self.batch_status_var.set(f"Batch audit completed! Scanned {count} video(s).")
        self._update_batch_filter_buttons()

        total = len(self.batch_results)
        dup = sum(1 for r in self.batch_results if r["verdict"].verdict == "duplicate")
        clean = sum(1 for r in self.batch_results if r["verdict"].verdict == "clear")
        idx = sum(1 for r in self.batch_results if r["verdict"].verdict == "already_indexed")

        messagebox.showinfo(
            "Batch Audit Finished",
            f"Batch Audit Completed!\n\n"
            f"• Total Scanned       : {total}\n"
            f"• Duplicates Caught   : {dup}\n"
            f"• Clean Originals     : {clean}\n"
            f"• Already Indexed     : {idx}\n\n"
            f"Select any file in the table to inspect forensic findings and generate its audit report.",
        )

    def _on_batch_audit_error(self, err_msg: str) -> None:
        self.batch_running = False
        self.batch_stop_requested = False
        self.batch_start_btn.config(state=tk.NORMAL)
        self.batch_stop_btn.config(state=tk.DISABLED)
        self.batch_status_var.set(f"Audit error: {err_msg}")
        messagebox.showerror("Batch Audit Error", f"An error occurred during batch audit:\n{err_msg}")

    def _set_batch_filter(self, mode: str) -> None:
        self.batch_filter_mode = mode
        self._render_batch_table()

    def _update_batch_filter_buttons(self) -> None:
        total = len(self.batch_results)
        dup = sum(1 for r in self.batch_results if r["verdict"].verdict == "duplicate")
        clean = sum(1 for r in self.batch_results if r["verdict"].verdict == "clear")
        idx = sum(1 for r in self.batch_results if r["verdict"].verdict == "already_indexed")
        rev = sum(1 for r in self.batch_results if r["verdict"].verdict == "review")

        if hasattr(self, "flt_all_btn"):
            self.flt_all_btn.config(text=f"All ({total})")
            self.flt_dup_btn.config(text=f"🔴 Duplicates ({dup})")
            self.flt_clean_btn.config(text=f"🟢 Clean ({clean})")
            self.flt_indexed_btn.config(text=f"🟣 Already Indexed ({idx})")
            self.flt_review_btn.config(text=f"🟡 Review ({rev})")

            self.card_scanned.config(text=f"📦 Scanned: {total}")
            self.card_clean.config(text=f"🟢 Originals / Clear: {clean}")
            self.card_dup.config(text=f"🔴 Duplicates: {dup}")
            self.card_indexed.config(text=f"🟣 Already Indexed: {idx}")
            self.card_review.config(text=f"🟡 Under Review: {rev}")

    def _render_batch_table(self) -> None:
        if not hasattr(self, "batch_tree"):
            return

        selected_id = None
        current_sel = self.batch_tree.selection()
        if current_sel:
            current_vals = self.batch_tree.item(current_sel[0], "values")
            if current_vals:
                selected_id = current_vals[0]

        for item in self.batch_tree.get_children():
            self.batch_tree.delete(item)

        q = self.batch_search_var.get().strip().lower()
        mode = self.batch_filter_mode

        first_item_id = None
        reselected_item_id = None

        for item in sorted(self.batch_results, key=lambda x: x.get("index", 0)):
            verd = item["verdict"].verdict
            if mode != "all" and verd != mode:
                continue

            fname = item["filename"]
            matched_name = (
                item.get("matched_info", {}).get("filename", "—")
                if item.get("matched_info")
                else "—"
            )

            if q and (q not in fname.lower() and q not in matched_name.lower()):
                continue

            tag = f"tag_{verd}"
            conf_str = f"{item['confidence'] * 100:.0f}%" if item["confidence"] > 0 else "—"
            row_id = self.batch_tree.insert(
                "",
                tk.END,
                values=(
                    item["index"],
                    fname,
                    verd.upper(),
                    conf_str,
                    item["layer"].capitalize(),
                    matched_name,
                    item["intent"],
                    f"{item['elapsed_s']:.1f}s",
                ),
                tags=(tag,),
            )
            if first_item_id is None:
                first_item_id = row_id
            if selected_id is not None and str(item["index"]) == str(selected_id):
                reselected_item_id = row_id

        target_sel = reselected_item_id or first_item_id
        if target_sel and not self.batch_tree.selection():
            self.batch_tree.selection_set(target_sel)
            self._on_batch_row_selected(None)

    def _on_batch_row_selected(self, event: Any = None) -> None:
        sel = self.batch_tree.selection()
        if not sel:
            return

        values = self.batch_tree.item(sel[0], "values")
        if not values:
            return

        target_seq = int(values[0])
        item = next((r for r in self.batch_results if r.get("index") == target_seq), None)
        if not item:
            return
        self.current_batch_selection = item

        verdict = item["verdict"]
        matched_info = item.get("matched_info")
        target_info = item["target_info"]

        # 1. Update verdict badge
        if verdict.verdict == "duplicate":
            self.batch_badge.config(
                text=f"🔴 DUPLICATE DETECTED — Matches Reference Clip ({item['confidence']*100:.0f}% confidence)",
                bg="#ffebee",
                fg="#b71c1c",
            )
        elif verdict.verdict == "already_indexed":
            self.batch_badge.config(
                text="🟣 BIT-FOR-BIT IDENTICAL FILE (Cryptographic SHA-256 Match)",
                bg="#f3e5f5",
                fg="#4a148c",
            )
        elif verdict.verdict == "clear":
            self.batch_badge.config(
                text="🟢 CLEAN ORIGINAL FOOTAGE (Approved for Training Pipeline)",
                bg="#e8f5e9",
                fg="#1b5e20",
            )
        elif verdict.verdict == "review":
            self.batch_badge.config(
                text=f"🟡 BORDERLINE SIMILARITY — Requires Human Keyframe Review ({item['confidence']*100:.0f}% confidence)",
                bg="#fffde7",
                fg="#e65100",
            )
        else:
            self.batch_badge.config(
                text="⚠️ INGESTION ANOMALY / ERROR",
                bg="#efebe9",
                fg="#3e2723",
            )

        # 2. Update labels
        self.batch_cur_lbl.config(
            text=f"{target_info.get('filename')} ({target_info.get('size_str', '')})"
        )
        if matched_info:
            orig_name = matched_info.get("filename", "Unknown")
            orig_stat = matched_info.get("status", "approved").upper()
            self.batch_matched_lbl.config(
                text=f"{orig_name} [Status: {orig_stat}]",
                foreground="#c62828"
                if verdict.verdict in ("duplicate", "already_indexed")
                else "#333",
            )
            disp_path = matched_info.get("display_path", "")
            self.batch_link_lbl.config(
                text=f"🔗 {disp_path} (Click to open)",
                fg="#1a73e8",
                cursor="hand2",
            )
            self.batch_play_btn.config(state=tk.NORMAL)
            if matched_info.get("is_local") and Path(disp_path).exists():
                self.batch_reveal_btn.config(state=tk.NORMAL)
                self.batch_folder_btn.config(state=tk.NORMAL)
            else:
                self.batch_reveal_btn.config(state=tk.DISABLED)
                self.batch_folder_btn.config(state=tk.DISABLED)
        else:
            self.batch_matched_lbl.config(
                text="None (Unique clip)",
                foreground="#2e7d32",
            )
            self.batch_link_lbl.config(
                text="Unique footage — no reference match",
                fg="#777",
                cursor="",
            )
            self.batch_play_btn.config(state=tk.DISABLED)
            self.batch_reveal_btn.config(state=tk.DISABLED)
            self.batch_folder_btn.config(state=tk.DISABLED)

        self.batch_layer_intent_lbl.config(
            text=f"Layer: {verdict.layer.capitalize()} | Intent: {verdict.intent_assessment}"
        )
        self.batch_guidance_lbl.config(text=verdict.operational_guidance)

        # Enable report & send to single check buttons
        self.batch_report_btn.config(state=tk.NORMAL)
        self.batch_send_single_btn.config(state=tk.NORMAL)

        # 3. Update evidence text
        self.batch_evidence_txt.delete("1.0", tk.END)
        self.batch_evidence_txt.insert(
            tk.END, f"Target: {item['filename']} (Latency: {item['elapsed_s']:.2f}s)\n"
        )
        self.batch_evidence_txt.insert(tk.END, f"SHA-256: {item['clip_id']}\n")
        self.batch_evidence_txt.insert(tk.END, f"Reason : {verdict.reason}\n\n")
        if verdict.matches:
            self.batch_evidence_txt.insert(tk.END, "Evidence & Candidate Matches:\n")
            for m in verdict.matches:
                self.batch_evidence_txt.insert(
                    tk.END,
                    f"  • [{m.detector}] Match: {m.matched_clip_id[:16]}... | Conf: {m.confidence:.3f}\n"
                    f"    Evidence: {json.dumps(dict(m.evidence))}\n",
                )
        else:
            self.batch_evidence_txt.insert(
                tk.END, "No candidate matches detected across active layers.\n"
            )

    def _play_batch_matched(self) -> None:
        if not self.current_batch_selection or not self.current_batch_selection.get("matched_info"):
            return
        path_str = self.current_batch_selection["matched_info"].get("display_path", "")
        self._launch_media_file(path_str)

    def _reveal_batch_matched(self) -> None:
        if not self.current_batch_selection or not self.current_batch_selection.get("matched_info"):
            return
        path_str = self.current_batch_selection["matched_info"].get("display_path", "")
        self._reveal_media_file(path_str)

    def _open_batch_matched_folder(self) -> None:
        if not self.current_batch_selection or not self.current_batch_selection.get("matched_info"):
            return
        path_str = self.current_batch_selection["matched_info"].get("display_path", "")
        self._open_media_folder(path_str)

    def _open_batch_item_report(self) -> None:
        """Open Smart Forensic Report specifically for the selected batch video."""
        if not self.current_batch_selection:
            messagebox.showinfo("Select Video", "Please select a video from the audit table first.")
            return

        item = self.current_batch_selection
        report_text = build_smart_report(
            target_path=item["path"],
            verdict=item["verdict"],
            target_info=item["target_info"],
            matched_info=item.get("matched_info"),
        )
        self._show_smart_report_dialog(
            report_text,
            f"ClipGuard — Batch Forensic Report: {item['filename']}",
        )

    def _send_batch_item_to_single_check(self) -> None:
        if not self.current_batch_selection:
            return
        p_str = self.current_batch_selection["path"]
        is_s3 = p_str.startswith("s3://")
        if is_s3 and self.conn_mode_var.get() != "s3":
            self._toggle_connection_mode()
        elif not is_s3 and self.conn_mode_var.get() != "local":
            self._toggle_connection_mode()

        self.check_file_var.set(p_str)
        self.notebook.select(self.tab_check)
        self._run_check_async()

    def _export_batch_audit_report(self) -> None:
        if not self.batch_results:
            messagebox.showinfo("Export", "No batch audit results to export.")
            return

        total = len(self.batch_results)
        dup = sum(1 for r in self.batch_results if r["verdict"].verdict == "duplicate")
        clean = sum(1 for r in self.batch_results if r["verdict"].verdict == "clear")
        idx = sum(1 for r in self.batch_results if r["verdict"].verdict == "already_indexed")
        rev = sum(1 for r in self.batch_results if r["verdict"].verdict == "review")

        lines = [
            "=" * 80,
            "CLIPGUARD COMPREHENSIVE BATCH FORENSIC AUDIT REPORT",
            "=" * 80,
            f"Generated At: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Target Source Audited: {self.batch_path_var.get()}",
            f"Active Reference Database: {self._get_db_path()}",
            "",
            "EXECUTIVE AUDIT SUMMARY METRICS:",
            f"  • Total Videos Audited : {total}",
            f"  • Clean / Novel Clips  : {clean} (Approved for Training Pipeline)",
            f"  • Duplicates Caught    : {dup} (Flagged & Rejected)",
            f"  • Already Indexed Clips: {idx} (Accidental Resubmissions)",
            f"  • Under Human Review   : {rev} (Requires Keyframe Inspection)",
            "-" * 80,
            f"{'#':<3} | {'FILENAME':<35} | {'VERDICT':<15} | {'CONF':<6} | {'MATCHED REFERENCE':<35}",
            "-" * 80,
        ]

        for item in self.batch_results:
            m_name = (
                item.get("matched_info", {}).get("filename", "—")
                if item.get("matched_info")
                else "—"
            )
            lines.append(
                f"{item['index']:<3} | {item['filename'][:35]:<35} | {item['verdict'].verdict.upper():<15} | {item['confidence']*100:>4.0f}% | {m_name[:35]:<35}"
            )

        lines.append("=" * 80)
        report_text = "\n".join(lines)

        self._show_smart_report_dialog(report_text, title="ClipGuard — Batch Audit Summary Report")

    def _export_batch_csv(self) -> None:
        if not self.batch_results:
            messagebox.showinfo("Export", "No batch audit results to export.")
            return

        save_path = filedialog.asksaveasfilename(
            title="Save Batch Audit CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="clipguard_batch_audit.csv",
        )
        if not save_path:
            return

        import csv

        with open(save_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Index",
                    "Filename",
                    "Path",
                    "SHA-256",
                    "Verdict",
                    "Confidence",
                    "Detection Layer",
                    "Matched Reference Filename",
                    "Matched Reference URI",
                    "Seller Intent",
                    "Operational Guidance",
                    "Scan Latency (s)",
                ]
            )
            for item in self.batch_results:
                m_info = item.get("matched_info") or {}
                writer.writerow(
                    [
                        item["index"],
                        item["filename"],
                        item["path"],
                        item["clip_id"],
                        item["verdict"].verdict,
                        f"{item['confidence']:.3f}",
                        item["layer"],
                        m_info.get("filename", ""),
                        m_info.get("display_path", ""),
                        item["intent"],
                        item["verdict"].operational_guidance,
                        f"{item['elapsed_s']:.2f}",
                    ]
                )
        messagebox.showinfo(
            "Exported", f"Successfully exported {len(self.batch_results)} results to:\n{save_path}"
        )

    # ------------------ TAB 2: INDEX LIBRARY ------------------
    def _build_index_tab(self) -> None:
        ctrl_frame = ttk.LabelFrame(
            self.tab_index, text="Library Location to Index (Local Disk or Amazon S3)", padding="10"
        )
        ctrl_frame.pack(fill=tk.X, pady=4)

        # Label indicating current mode
        self.index_prompt_lbl = ttk.Label(
            ctrl_frame,
            text=(
                "Amazon S3 Bucket / Prefix URI to Index:"
                if self.conn_mode_var.get() == "s3"
                else "Local Folder or File to Index:"
            ),
            font=("Arial", 9, "bold"),
        )
        self.index_prompt_lbl.pack(anchor=tk.W, pady=(0, 4))

        entry_row = ttk.Frame(ctrl_frame)
        entry_row.pack(fill=tk.X, pady=(0, 6))

        self.index_path_var = tk.StringVar(value=self.cfg.source.path)
        entry = ttk.Entry(entry_row, textvariable=self.index_path_var, font=("Arial", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        browse_dir_btn = ttk.Button(
            entry_row, text="📁 Local Folder...", command=self._browse_index_dir
        )
        browse_dir_btn.pack(side=tk.LEFT, padx=2)

        browse_f_btn = ttk.Button(
            entry_row, text="📄 Local File...", command=self._browse_index_file
        )
        browse_f_btn.pack(side=tk.LEFT, padx=2)

        browse_s3_btn = ttk.Button(
            entry_row, text="☁️ S3 Bucket/Prefix...", command=self._browse_index_s3
        )
        browse_s3_btn.pack(side=tk.LEFT, padx=2)

        # Options & action row
        opt_row = ttk.Frame(ctrl_frame)
        opt_row.pack(fill=tk.X, pady=(4, 0))

        ttk.Label(opt_row, text="Status Tag for Indexed Clips:").pack(side=tk.LEFT, padx=(0, 4))
        self.status_choice_var = tk.StringVar(value="approved")
        status_menu = ttk.Combobox(
            opt_row,
            textvariable=self.status_choice_var,
            values=["approved", "rejected", "pending"],
            width=12,
            state="readonly",
        )
        status_menu.pack(side=tk.LEFT, padx=(0, 10))

        s3_settings_btn = ttk.Button(
            opt_row,
            text="⚙️ S3 Config / Test...",
            command=self._show_s3_config_dialog,
        )
        s3_settings_btn.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(opt_row, text="Workers (Concurrency):").pack(side=tk.LEFT, padx=(0, 4))
        available_cores = os.cpu_count() or 4
        default_workers = min(8, available_cores)
        self.index_workers_var = tk.StringVar(value=str(default_workers))
        worker_vals = [str(w) for w in [2, 4, 8, 12, 16] if w <= max(4, available_cores)]
        if str(default_workers) not in worker_vals:
            worker_vals.append(str(default_workers))
            worker_vals.sort(key=int)
        worker_menu = ttk.Combobox(
            opt_row,
            textvariable=self.index_workers_var,
            values=worker_vals,
            width=4,
            state="readonly",
        )
        worker_menu.pack(side=tk.LEFT, padx=(0, 10))

        start_btn = ttk.Button(
            opt_row,
            text="📥 Start Indexing",
            command=self._run_index_async,
        )
        start_btn.pack(side=tk.RIGHT, padx=4)

        # Presets row for fast testing
        preset_row = ttk.Frame(ctrl_frame)
        preset_row.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(
            preset_row,
            text="Quick Presets:",
            font=("Arial", 8, "italic"),
            foreground="#666",
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            preset_row,
            text="☁️ Configured S3 Source",
            command=lambda: self._set_index_preset(
                f"s3://{self.cfg.source.bucket}/{self.cfg.source.prefix}"
                if self.cfg.source.bucket
                else "s3://",
                is_s3=True,
            ),
        ).pack(side=tk.LEFT, padx=2)

        # Indexing Log Text
        log_frame = ttk.LabelFrame(self.tab_index, text="Indexing Activity Log", padding="8")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=6)

        self.index_log = tk.Text(log_frame, font=("Consolas", 9), wrap=tk.WORD)
        self.index_log.pack(fill=tk.BOTH, expand=True)

    def _set_index_preset(self, path_str: str, is_s3: bool) -> None:
        """Set index path from quick preset button."""
        if is_s3:
            self.index_path_var.set(path_str)
            if self.conn_mode_var.get() != "s3":
                self._toggle_connection_mode()
        else:
            p = Path(path_str).resolve()
            self.index_path_var.set(str(p))
            if self.conn_mode_var.get() != "local":
                self._toggle_connection_mode()

    def _browse_index_dir(self) -> None:
        path = filedialog.askdirectory(title="Select Video Folder to Index")
        if path:
            self.index_path_var.set(path)

    def _browse_index_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Video File to Index",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All files", "*.*")],
        )
        if path:
            self.index_path_var.set(path)

    def _browse_index_s3(self) -> None:
        """Prompt user for S3 bucket and prefix to index."""
        default_uri = (
            f"s3://{self.cfg.source.bucket}/{self.cfg.source.prefix}"
            if self.cfg.source.bucket
            else "s3://"
        )
        cur = self.index_path_var.get().strip()
        if cur.startswith("s3://"):
            default_uri = cur

        uri = simpledialog.askstring(
            "Amazon S3 Source to Index",
            "Enter Amazon S3 Bucket & Prefix to Index:\n(Example: s3://my-bucket/approved-clips/ or s3://my-bucket)",
            initialvalue=default_uri,
        )
        if uri:
            cleaned = uri.strip()
            if not cleaned.startswith("s3://"):
                cleaned = f"s3://{cleaned}"
            self.index_path_var.set(cleaned)
            if self.conn_mode_var.get() != "s3":
                self._toggle_connection_mode()

    def _show_s3_config_dialog(self) -> None:
        """Modal dialog to view/edit S3 credentials, region, endpoint, and test connection."""
        win = tk.Toplevel(self.root)
        win.title("Amazon S3 Cloud Configuration & Testing")
        win.geometry("580x450")
        win.minsize(500, 380)

        main_f = ttk.Frame(win, padding="14")
        main_f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            main_f,
            text="Amazon S3 Cloud Storage Configuration",
            font=("Arial", 11, "bold"),
        ).pack(anchor=tk.W, pady=(0, 10))

        form = ttk.Frame(main_f)
        form.pack(fill=tk.X, pady=4)

        # S3 Bucket
        ttk.Label(form, text="S3 Bucket:").grid(row=0, column=0, sticky=tk.W, pady=4)
        bucket_var = tk.StringVar(value=self.cfg.source.bucket)
        ttk.Entry(form, textvariable=bucket_var, width=35).grid(
            row=0, column=1, sticky=tk.W, padx=8, pady=4
        )

        # Prefix
        ttk.Label(form, text="Default Prefix / Folder:").grid(row=1, column=0, sticky=tk.W, pady=4)
        prefix_var = tk.StringVar(value=self.cfg.source.prefix)
        ttk.Entry(form, textvariable=prefix_var, width=35).grid(
            row=1, column=1, sticky=tk.W, padx=8, pady=4
        )

        # Region
        ttk.Label(form, text="AWS Region:").grid(row=2, column=0, sticky=tk.W, pady=4)
        region_var = tk.StringVar(value=self.cfg.source.region or "us-east-1")
        region_cb = ttk.Combobox(
            form,
            textvariable=region_var,
            values=[
                "us-east-1",
                "us-east-2",
                "us-west-1",
                "us-west-2",
                "ap-south-1",
                "eu-west-1",
                "eu-central-1",
                "ap-southeast-1",
            ],
            width=20,
        )
        region_cb.grid(row=2, column=1, sticky=tk.W, padx=8, pady=4)

        # Custom Endpoint URL
        ttk.Label(form, text="Custom Endpoint URL:").grid(row=3, column=0, sticky=tk.W, pady=4)
        endpoint_var = tk.StringVar(value=self.cfg.source.endpoint_url or "")
        ttk.Entry(form, textvariable=endpoint_var, width=35).grid(
            row=3, column=1, sticky=tk.W, padx=8, pady=4
        )
        ttk.Label(
            form,
            text="(Leave empty for AWS. Use for MinIO / LocalStack)",
            font=("Arial", 8, "italic"),
            foreground="#666",
        ).grid(row=4, column=1, sticky=tk.W, padx=8, pady=(0, 4))

        # Credentials check
        import boto3

        sess = boto3.Session()
        creds = sess.get_credentials()
        cred_status = (
            "✅ Found AWS Credentials in environment/profile"
            if creds
            else "⚠️ No AWS credentials detected (~/.aws/credentials or AWS_ACCESS_KEY_ID)"
        )
        cred_color = "#2e7d32" if creds else "#c62828"

        cred_lbl = tk.Label(
            main_f,
            text=cred_status,
            fg=cred_color,
            font=("Arial", 9, "bold"),
            wraplength=520,
            justify=tk.LEFT,
        )
        cred_lbl.pack(anchor=tk.W, pady=(6, 8))

        # Test output area
        res_txt = tk.Text(main_f, height=5, font=("Consolas", 9), wrap=tk.WORD)
        res_txt.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        def _test_s3_connection() -> None:
            res_txt.delete("1.0", tk.END)
            b = bucket_var.get().strip()
            r = region_var.get().strip() or "us-east-1"
            ep = endpoint_var.get().strip() or None
            if not b:
                res_txt.insert(tk.END, "Please enter an S3 Bucket name to test.\n")
                return
            res_txt.insert(tk.END, f"Testing connection to bucket '{b}' in region '{r}'...\n")
            try:
                kwargs: dict[str, Any] = {"region_name": r}
                if ep:
                    kwargs["endpoint_url"] = ep
                client = boto3.client("s3", **kwargs)
                pfx = prefix_var.get().strip()
                resp = client.list_objects_v2(Bucket=b, Prefix=pfx, MaxKeys=5)
                keys = [o["Key"] for o in resp.get("Contents", [])]
                res_txt.insert(tk.END, f"✅ SUCCESS: Successfully reached bucket '{b}'!\n")
                res_txt.insert(tk.END, f"Found {len(keys)} object(s) with prefix '{pfx}':\n")
                for k in keys:
                    res_txt.insert(tk.END, f"  • {k}\n")
            except Exception as exc:
                res_txt.insert(tk.END, f"❌ ERROR: Failed to connect:\n{exc}\n")

        def _apply_s3_config() -> None:
            b = bucket_var.get().strip()
            p = prefix_var.get().strip()
            r = region_var.get().strip() or "us-east-1"
            ep = endpoint_var.get().strip() or None
            from dedupe.config import SourceConfig

            self.cfg = Config(
                pipeline=self.cfg.pipeline,
                source=SourceConfig(
                    type="s3",
                    path=self.cfg.source.path,
                    bucket=b,
                    prefix=p,
                    region=r,
                    endpoint_url=ep,
                ),
                store=self.cfg.store,
                detectors=self.cfg.detectors,
            )
            s3_uri = f"s3://{b}/{p}" if b else "s3://"
            self.index_path_var.set(s3_uri)
            if self.conn_mode_var.get() != "s3":
                self._toggle_connection_mode()
            messagebox.showinfo("Applied", f"S3 settings applied! Index path set to:\n{s3_uri}")
            win.destroy()

        btn_row = ttk.Frame(main_f)
        btn_row.pack(fill=tk.X)

        ttk.Button(btn_row, text="🔌 Test S3 Connection", command=_test_s3_connection).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_row, text="💾 Save & Use for Indexing", command=_apply_s3_config).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=4)

    def _run_index_async(self) -> None:
        target_path = self.index_path_var.get().strip()
        if not target_path:
            messagebox.showwarning(
                "Input Needed", "Please select a directory, file, or S3 URI to index."
            )
            return

        is_s3 = target_path.startswith("s3://")
        if not is_s3 and not Path(target_path).exists():
            messagebox.showerror("Error", f"Local path does not exist:\n{target_path}")
            return

        self.status_var.set(f"Indexing {target_path}...")
        self.index_log.insert(
            tk.END,
            f"\n--- Starting indexing for {target_path} (status: {self.status_choice_var.get()}, audio: {'ON' if self.audio_enabled_var.get() else 'OFF'}) ---\n",
        )
        threading.Thread(
            target=self._run_index_worker, args=(target_path, is_s3), daemon=True
        ).start()

    def _run_index_worker(self, target_path: str, is_s3: bool) -> None:
        pipeline = None
        count = 0
        try:
            pipeline = self._build_pipeline()
            if is_s3:
                p = urlparse(target_path)
                bucket = p.netloc
                prefix = p.path.lstrip("/")
                if not bucket and target_path.startswith("s3://"):
                    raw = target_path[5:]
                    if "/" in raw:
                        bucket, prefix = raw.split("/", 1)
                    else:
                        bucket, prefix = raw, ""

                if not bucket:
                    raise ValueError(
                        f"Invalid S3 URI '{target_path}'. Format must be: s3://bucket-name/folder/"
                    )

                self.root.after(
                    0,
                    lambda: self.index_log.insert(
                        tk.END,
                        f"Connecting to Amazon S3...\n  Bucket: {bucket}\n  Prefix: {prefix or '(root)'}\n  Region: {self.cfg.source.region}\n\n",
                    ),
                )

                s3_src = S3Source(
                    bucket=bucket,
                    prefix=prefix,
                    status=self.status_choice_var.get(),
                    region=self.cfg.source.region,
                    endpoint_url=self.cfg.source.endpoint_url,
                )
                s3_clips = list(s3_src.iter_clips())
                if not s3_clips:
                    self.root.after(
                        0,
                        lambda: self.index_log.insert(
                            tk.END,
                            f"Note: No matching video files found in s3://{bucket}/{prefix}\n",
                        ),
                    )
                else:
                    self.root.after(
                        0,
                        lambda n=len(s3_clips): self.index_log.insert(
                            tk.END,
                            f"Discovered {n} S3 video(s). Streaming and extracting fingerprints...\n",
                        ),
                    )

                    def _s3_worker(c: ClipRef) -> tuple[str, str, list[tuple[Any, Any]], ClipRef]:
                        fname = Path(c.uri).name or c.uri
                        with s3_src.materialize(c.uri) as temp_local:
                            local_clip = ClipRef(
                                clip_id=c.clip_id,
                                uri=c.uri,
                                local_path=temp_local,
                                status=c.status,
                            )
                            fps = pipeline.extract_fingerprints(local_clip)
                            return fname, c.clip_id, fps, local_clip

                    try:
                        concurrency = int(self.index_workers_var.get())
                    except (ValueError, TypeError, AttributeError):
                        concurrency = 8

                    num_s3_workers = min(concurrency, max(1, len(s3_clips)))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=num_s3_workers) as executor:
                        futures = [executor.submit(_s3_worker, c) for c in s3_clips]
                        for fut in concurrent.futures.as_completed(futures):
                            fname, cid, fps, local_clip = fut.result()
                            pipeline.register_extracted(local_clip, fps)
                            count += 1
                            self.root.after(0, self._log_indexed_clip, fname, cid)
            else:
                source = LocalSource(Path(target_path), status=self.status_choice_var.get())
                paths = list(source.iter_paths())
                if not paths:
                    self.root.after(
                        0,
                        lambda: self.index_log.insert(
                            tk.END, f"Note: No supported video files found in {target_path}\n"
                        ),
                    )
                else:
                    self.root.after(
                        0,
                        lambda n=len(paths): self.index_log.insert(
                            tk.END,
                            f"Discovered {n} candidate video(s). Extracting fingerprints in parallel across multi-core workers...\n",
                        ),
                    )
                    status_choice = self.status_choice_var.get()

                    def _extract_worker(path: Path) -> tuple[ClipRef, list[tuple[Any, Any]]]:
                        clip = clip_from_path(path, status=status_choice)
                        fps = pipeline.extract_fingerprints(clip)
                        return clip, fps

                    try:
                        concurrency = int(self.index_workers_var.get())
                    except (ValueError, TypeError, AttributeError):
                        concurrency = 8

                    num_workers = min(concurrency, max(1, len(paths)))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                        futures = {executor.submit(_extract_worker, p): p for p in paths}
                        for fut in concurrent.futures.as_completed(futures):
                            clip, fps = fut.result()
                            pipeline.register_extracted(clip, fps)
                            count += 1
                            self.root.after(
                                0, self._log_indexed_clip, clip.local_path.name, clip.clip_id
                            )
            self.root.after(0, self._log_indexing_complete, count)
        except Exception as exc:
            self.root.after(0, self._log_indexing_error, str(exc))
        finally:
            if pipeline is not None:
                pipeline.store.close()

    def _log_indexed_clip(self, name: str, clip_id: str) -> None:
        self.index_log.insert(tk.END, f"  Indexed: {name} (SHA: {clip_id[:16]}...)\n")
        self.index_log.see(tk.END)

    def _log_indexing_complete(self, count: int) -> None:
        self.index_log.insert(tk.END, f"SUCCESS: Indexed {count} clip(s).\n")
        self.index_log.see(tk.END)
        self.status_var.set(f"Indexing finished. Total clips added: {count}")
        self._refresh_stats()

    def _log_indexing_error(self, err_msg: str) -> None:
        self.index_log.insert(tk.END, f"ERROR: {err_msg}\n")
        self.index_log.see(tk.END)
        self.status_var.set("Indexing error.")

    # ------------------ TAB 3: STATS ------------------
    def _get_db_path(self) -> Path:
        """Resolve the active SQLite database path unambiguously."""
        raw = Path(self.cfg.store.path)
        if raw.is_absolute():
            return raw
        if self.config_path and self.config_path.parent:
            return (self.config_path.parent / raw).resolve()
        return raw.resolve()

    def _build_stats_tab(self) -> None:
        # 1. Top Health & Database Status Frame
        top_frame = ttk.LabelFrame(
            self.tab_stats, text="Database Health & Storage Status", padding="10"
        )
        top_frame.pack(fill=tk.X, pady=(0, 6))

        self.stats_health_var = tk.StringVar(value="🟢 Database Connected & Healthy")
        health_lbl = tk.Label(
            top_frame,
            textvariable=self.stats_health_var,
            font=("Arial", 10, "bold"),
            fg="#2e7d32",
        )
        health_lbl.pack(anchor=tk.W, pady=(0, 2))

        self.stats_file_var = tk.StringVar(value=f"Database File: {self._get_db_path()}")
        ttk.Label(
            top_frame,
            textvariable=self.stats_file_var,
            font=("Consolas", 9),
            foreground="#555",
        ).pack(anchor=tk.W, pady=(0, 2))

        self.stats_time_var = tk.StringVar(value="Last updated: —")
        ttk.Label(
            top_frame,
            textvariable=self.stats_time_var,
            font=("Arial", 9, "italic"),
            foreground="#777",
        ).pack(anchor=tk.W)

        # 2. Metrics Card Frame (Two columns for counts & layer fingerprints)
        metrics_frame = ttk.LabelFrame(
            self.tab_stats, text="Indexing Metrics & Fingerprint Counts", padding="10"
        )
        metrics_frame.pack(fill=tk.X, pady=(0, 6))

        col1 = ttk.Frame(metrics_frame)
        col1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        self.stats_total_var = tk.StringVar(value="Total Reference Clips: —")
        ttk.Label(col1, textvariable=self.stats_total_var, font=("Arial", 11, "bold")).pack(
            anchor=tk.W, pady=2
        )

        self.stats_app_var = tk.StringVar(value="  • Approved Clips: —")
        ttk.Label(
            col1, textvariable=self.stats_app_var, font=("Arial", 10), foreground="#2e7d32"
        ).pack(anchor=tk.W, pady=1)

        self.stats_rej_var = tk.StringVar(value="  • Rejected Clips: —")
        ttk.Label(
            col1, textvariable=self.stats_rej_var, font=("Arial", 10), foreground="#c62828"
        ).pack(anchor=tk.W, pady=1)

        self.stats_pend_var = tk.StringVar(value="  • Pending Clips: —")
        ttk.Label(
            col1, textvariable=self.stats_pend_var, font=("Arial", 10), foreground="#e65100"
        ).pack(anchor=tk.W, pady=1)

        col2 = ttk.Frame(metrics_frame)
        col2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)

        ttk.Label(col2, text="Multilayer Fingerprints Indexed:", font=("Arial", 11, "bold")).pack(
            anchor=tk.W, pady=2
        )

        self.stats_frames_var = tk.StringVar(value="Frame Perceptual Hashes (pHash)  : —")
        ttk.Label(col2, textvariable=self.stats_frames_var, font=("Consolas", 9)).pack(
            anchor=tk.W, pady=2
        )

        self.stats_audio_var = tk.StringVar(value="Acoustic Fingerprints (Chromaprint): —")
        ttk.Label(col2, textvariable=self.stats_audio_var, font=("Consolas", 9)).pack(
            anchor=tk.W, pady=2
        )

        # 3. Catalog Table Frame
        cat_frame = ttk.LabelFrame(
            self.tab_stats, text="Indexed Clips Catalog (Library Reference)", padding="8"
        )
        cat_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        tree_frame = ttk.Frame(cat_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("cid", "filename", "status", "date", "path")
        self.clips_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.clips_tree.heading("cid", text="Clip SHA-256")
        self.clips_tree.heading("filename", text="Filename")
        self.clips_tree.heading("status", text="Status")
        self.clips_tree.heading("date", text="Indexed At")
        self.clips_tree.heading("path", text="Storage Location / URI")

        self.clips_tree.column("cid", width=120, anchor=tk.W)
        self.clips_tree.column("filename", width=220, anchor=tk.W)
        self.clips_tree.column("status", width=80, anchor=tk.CENTER)
        self.clips_tree.column("date", width=140, anchor=tk.CENTER)
        self.clips_tree.column("path", width=300, anchor=tk.W)

        tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.clips_tree.yview)
        tree_scroll_x = ttk.Scrollbar(
            tree_frame, orient=tk.HORIZONTAL, command=self.clips_tree.xview
        )
        self.clips_tree.configure(
            yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set
        )

        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.clips_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Style tags in treeview
        self.clips_tree.tag_configure("approved", foreground="#2e7d32")
        self.clips_tree.tag_configure("rejected", foreground="#c62828")
        self.clips_tree.tag_configure("pending", foreground="#e65100")

        # Keyboard shortcut for select all
        self.clips_tree.bind("<Control-a>", lambda e: self._select_all_clips())
        self.clips_tree.bind("<Control-A>", lambda e: self._select_all_clips())

        # 4. Action Buttons row below catalog
        btn_bar = ttk.Frame(self.tab_stats)
        btn_bar.pack(fill=tk.X, pady=(4, 0))

        self.refresh_btn = tk.Button(
            btn_bar,
            text="🔄 Refresh Statistics",
            font=("Arial", 9, "bold"),
            bg="#e8f5e9",
            fg="#2e7d32",
            padx=10,
            pady=3,
            command=lambda: self._refresh_stats(manual=True),
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            btn_bar,
            text="📂 Reveal DB File",
            command=self._reveal_db_file,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            btn_bar,
            text="☑️ Select All",
            command=self._select_all_clips,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            btn_bar,
            text="🗑️ Remove Selected...",
            command=self._delete_selected_clips,
        ).pack(side=tk.LEFT, padx=3)

    def _select_all_clips(self) -> None:
        """Select all items in the clips treeview catalog."""
        children = self.clips_tree.get_children()
        if children:
            self.clips_tree.selection_set(children)

    def _reveal_db_file(self) -> None:
        """Reveal the SQLite database file in Windows File Explorer."""
        db_path = self._get_db_path()
        if db_path.exists():
            try:
                cmd = f'explorer.exe /select,"{db_path}"'
                subprocess.Popen(cmd)
            except Exception:
                os.startfile(str(db_path.parent))
        else:
            messagebox.showwarning("File Not Found", f"Database file does not exist:\n{db_path}")

    def _delete_selected_clips(self) -> None:
        """Delete one or more selected clips from the database with confirmation prompt."""
        sel = self.clips_tree.selection()
        if not sel:
            messagebox.showinfo(
                "Select Clips", "Please select one or more clips from the catalog table to delete."
            )
            return

        db_path = self._get_db_path()
        targets: list[tuple[str, str, str]] = []  # (clip_id, filename, display_path)

        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                cur = conn.cursor()
                for item_id in sel:
                    vals = self.clips_tree.item(item_id).get("values", [])
                    if not vals:
                        continue
                    fname = str(vals[1])
                    fpath = str(vals[4])
                    row = cur.execute(
                        "SELECT clip_id FROM clips WHERE uri = ? OR local_path LIKE ? LIMIT 1",
                        (fpath, f"%{fname}%"),
                    ).fetchone()
                    if row:
                        targets.append((row[0], fname, fpath))
            finally:
                conn.close()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to query database:\n{exc}")
            return

        if not targets:
            messagebox.showwarning("Not Found", "Could not locate selected clip(s) in database.")
            return

        # Explicit confirmation prompt
        if len(targets) == 1:
            cid, fname, fpath = targets[0]
            msg = (
                f"Are you sure you want to remove this clip from the database?\n\n"
                f"Filename: {fname}\n"
                f"Location: {fpath}\n\n"
                "This will delete its exact hash, frame pHash index, and audio fingerprints from the database."
            )
        else:
            sample_names = "\n".join(f"  • {t[1]}" for t in targets[:5])
            if len(targets) > 5:
                sample_names += f"\n  ... and {len(targets) - 5} more clips"
            msg = (
                f"Are you sure you want to remove {len(targets)} selected clip(s) from the database?\n\n"
                f"Selected clips:\n{sample_names}\n\n"
                "This will delete their exact hashes, frame pHash indices, and audio fingerprints from the database."
            )

        confirm = messagebox.askyesno(
            "Confirm Removal",
            msg,
            icon=messagebox.WARNING,
        )
        if not confirm:
            return

        # Execute removal in a single atomic transaction
        try:
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            try:
                with conn:
                    for cid, fname, fpath in targets:
                        conn.execute("DELETE FROM clips WHERE clip_id = ?", (cid,))
                        conn.execute("DELETE FROM exact_index WHERE clip_id = ?", (cid,))
                        conn.execute("DELETE FROM frame_index WHERE clip_id = ?", (cid,))
                        conn.execute("DELETE FROM audio_index WHERE clip_id = ?", (cid,))
                messagebox.showinfo(
                    "Removed", f"Successfully removed {len(targets)} clip(s) from the database."
                )
            finally:
                conn.close()

            self._refresh_stats(manual=True)
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to delete clips:\n{exc}")


    def _refresh_stats(self, manual: bool = False) -> None:
        """Query SQLite database directly and refresh all statistics, metrics, and catalog table."""
        try:
            db_path = self._get_db_path()
            if not db_path.exists():
                self.stats_total_var.set("Total Reference Clips: 0 (Database file not found)")
                self.stats_app_var.set("  • Approved Clips: 0")
                self.stats_rej_var.set("  • Rejected Clips: 0")
                self.stats_pend_var.set("  • Pending Clips:  0")
                if hasattr(self, "stats_health_var"):
                    self.stats_health_var.set(f"⚠️ Database file not found on disk at:\n{db_path}")
                return

            size_str = format_file_size(db_path.stat().st_size)
            if hasattr(self, "stats_file_var"):
                self.stats_file_var.set(f"Database File: {db_path} ({size_str})")

            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                cur = conn.cursor()
                tot = cur.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
                app = (
                    cur.execute("SELECT COUNT(*) FROM clips WHERE status = 'approved'").fetchone()[
                        0
                    ]
                )
                rej = (
                    cur.execute("SELECT COUNT(*) FROM clips WHERE status = 'rejected'").fetchone()[
                        0
                    ]
                )
                pend = cur.execute(
                    "SELECT COUNT(*) FROM clips WHERE status = 'pending'"
                ).fetchone()[0]

                frame_cnt = cur.execute("SELECT COUNT(*) FROM frame_index").fetchone()[0]
                audio_cnt = cur.execute("SELECT COUNT(*) FROM audio_index").fetchone()[0]

                self.stats_total_var.set(f"Total Reference Clips Indexed: {tot}")
                self.stats_app_var.set(f"  • Approved Clips : {app}")
                self.stats_rej_var.set(f"  • Rejected Clips : {rej}")
                self.stats_pend_var.set(f"  • Pending Clips  : {pend}")
                if hasattr(self, "stats_frames_var"):
                    self.stats_frames_var.set(
                        f"Frame Perceptual Hashes (pHash)  : {frame_cnt:,} frames"
                    )
                if hasattr(self, "stats_audio_var"):
                    self.stats_audio_var.set(
                        f"Acoustic Fingerprints (Chromaprint): {audio_cnt:,} sub-fingerprints"
                    )

                now_str = time.strftime("%H:%M:%S")
                if hasattr(self, "stats_time_var"):
                    self.stats_time_var.set(
                        f"Last updated: Today at {now_str} (Synced with {db_path.name})"
                    )
                if hasattr(self, "stats_health_var"):
                    self.stats_health_var.set("🟢 Database Connected & Healthy")

                # Populate Treeview Catalog
                if hasattr(self, "clips_tree"):
                    for item in self.clips_tree.get_children():
                        self.clips_tree.delete(item)

                    rows = cur.execute(
                        "SELECT clip_id, uri, status, created_at FROM clips ORDER BY created_at DESC"
                    ).fetchall()
                    for r in rows:
                        cid = r[0][:14] + "..." if len(r[0]) > 14 else r[0]
                        uri_val = r[1]
                        name = clean_uri_to_display_path(uri_val)
                        filename = Path(name).name or uri_val
                        stat = r[2].upper()
                        date_s = (
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[3]))
                            if r[3]
                            else "—"
                        )
                        self.clips_tree.insert(
                            "",
                            tk.END,
                            values=(cid, filename, stat, date_s, name),
                            tags=(r[2],),
                        )
            finally:
                conn.close()

            # Status bar feedback
            self.status_var.set(
                f"Database statistics refreshed at {now_str}: {tot} clip(s), {frame_cnt:,} frames, {audio_cnt:,} audio fingerprints."
            )

            # Button feedback animation if manual click
            if manual and hasattr(self, "refresh_btn"):
                self.refresh_btn.config(text="✅ Refreshed!")
                self.root.after(
                    1200, lambda: self.refresh_btn.config(text="🔄 Refresh Statistics")
                )

        except Exception as exc:
            err_msg = f"Failed to refresh database stats:\n{exc}"
            self.status_var.set(f"Stats Error: {exc}")
            if hasattr(self, "stats_health_var"):
                self.stats_health_var.set(f"❌ Error: {exc}")
            if manual:
                messagebox.showerror("Refresh Error", err_msg)


def launch_gui(config_path: Optional[Path] = None) -> None:
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    ClipGuardGUI(root, config_path=config_path, show_prompt=True)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
