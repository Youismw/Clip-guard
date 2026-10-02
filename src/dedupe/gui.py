"""Functional desktop GUI for ClipGuard duplicate video detection."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from dedupe.config import load_config
from dedupe.models import Verdict
from dedupe.pipeline import load_pipeline
from dedupe.sources.local import LocalSource, clip_from_path
from dedupe.stores.sqlite import SqliteStore


class ClipGuardGUI:
    """Desktop interface focused on operational functionality and duplicate inspection."""

    def __init__(self, root: tk.Tk, config_path: Optional[Path] = None) -> None:
        self.root = root
        self.root.title("ClipGuard — Duplicate Video Detection")
        self.root.geometry("860x680")
        self.root.minsize(750, 550)

        self.config_path = config_path
        self.cfg = load_config(self.config_path)

        self._build_ui()
        self._refresh_stats()

    def _build_ui(self) -> None:
        # Top banner with store path
        banner_frame = ttk.Frame(self.root, padding="8")
        banner_frame.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(
            banner_frame,
            text="ClipGuard Detection Console",
            font=("Arial", 14, "bold"),
        ).pack(side=tk.LEFT)

        db_label = f"Store: {self.cfg.store.path}"
        ttk.Label(banner_frame, text=db_label, font=("Arial", 9), foreground="#555").pack(
            side=tk.RIGHT
        )

        # Tabbed Notebook
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Tab 1: Check Video
        self.tab_check = ttk.Frame(notebook, padding="10")
        notebook.add(self.tab_check, text=" Check Video ")
        self._build_check_tab()

        # Tab 2: Index Library
        self.tab_index = ttk.Frame(notebook, padding="10")
        notebook.add(self.tab_index, text=" Index Library ")
        self._build_index_tab()

        # Tab 3: Store Stats
        self.tab_stats = ttk.Frame(notebook, padding="10")
        notebook.add(self.tab_stats, text=" Database Stats ")
        self._build_stats_tab()

        # Bottom status bar
        self.status_var = tk.StringVar(value="Ready.")
        status_bar = ttk.Label(
            self.root,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding="4",
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------ TAB 1: CHECK VIDEO ------------------
    def _build_check_tab(self) -> None:
        # File selector row
        sel_frame = ttk.LabelFrame(self.tab_check, text="Incoming Video", padding="8")
        sel_frame.pack(fill=tk.X, pady=4)

        self.check_file_var = tk.StringVar()
        entry = ttk.Entry(sel_frame, textvariable=self.check_file_var, font=("Arial", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        browse_btn = ttk.Button(sel_frame, text="Browse...", command=self._browse_check_file)
        browse_btn.pack(side=tk.LEFT, padx=2)

        self.auto_reg_var = tk.BooleanVar(value=True)
        auto_reg_cb = ttk.Checkbutton(
            sel_frame, text="Register if clear", variable=self.auto_reg_var
        )
        auto_reg_cb.pack(side=tk.LEFT, padx=6)

        check_btn = ttk.Button(
            sel_frame,
            text="▶ Check Video",
            command=self._run_check_async,
        )
        check_btn.pack(side=tk.LEFT, padx=4)

        # Results area
        res_frame = ttk.LabelFrame(self.tab_check, text="Verdict & Triage Analysis", padding="10")
        res_frame.pack(fill=tk.BOTH, expand=True, pady=6)

        # Big verdict banner
        self.verdict_badge = tk.Label(
            res_frame,
            text="NO VIDEO CHECKED",
            font=("Arial", 16, "bold"),
            bg="#e0e0e0",
            fg="#333",
            padx=12,
            pady=6,
            relief=tk.GROOVE,
        )
        self.verdict_badge.pack(fill=tk.X, pady=(0, 8))

        # Two-column grid for Layer & Seller Intent
        grid_frame = ttk.Frame(res_frame)
        grid_frame.pack(fill=tk.X, pady=4)

        ttk.Label(grid_frame, text="Detection Layer:", font=("Arial", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=2
        )
        self.layer_val = ttk.Label(grid_frame, text="—", font=("Arial", 10))
        self.layer_val.grid(row=0, column=1, sticky=tk.W, padx=8, pady=2)

        ttk.Label(grid_frame, text="Seller Intent Assessment:", font=("Arial", 10, "bold")).grid(
            row=1, column=0, sticky=tk.W, pady=2
        )
        self.intent_val = ttk.Label(grid_frame, text="—", font=("Arial", 10))
        self.intent_val.grid(row=1, column=1, sticky=tk.W, padx=8, pady=2)

        ttk.Label(grid_frame, text="Operational Guidance:", font=("Arial", 10, "bold")).grid(
            row=2, column=0, sticky=tk.W, pady=2
        )
        self.guidance_val = ttk.Label(
            grid_frame, text="—", font=("Arial", 9, "italic"), wraplength=520
        )
        self.guidance_val.grid(row=2, column=1, sticky=tk.W, padx=8, pady=2)

        # Details Text
        ttk.Label(res_frame, text="Evidence & Match Details:", font=("Arial", 10, "bold")).pack(
            anchor=tk.W, pady=(8, 2)
        )
        self.details_txt = tk.Text(res_frame, height=9, font=("Consolas", 9), wrap=tk.WORD)
        self.details_txt.pack(fill=tk.BOTH, expand=True)

    def _browse_check_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Video to Check",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All files", "*.*")],
        )
        if path:
            self.check_file_var.set(path)

    def _run_check_async(self) -> None:
        file_path = self.check_file_var.get().strip()
        if not file_path:
            messagebox.showwarning("Input Needed", "Please select a video file first.")
            return

        p = Path(file_path)
        if not p.exists():
            messagebox.showerror("Error", f"File does not exist: {p}")
            return

        self.status_var.set(f"Checking {p.name}...")
        self.verdict_badge.config(text="PROCESSING...", bg="#ffd54f", fg="#333")
        threading.Thread(target=self._run_check_worker, args=(p,), daemon=True).start()

    def _run_check_worker(self, path: Path) -> None:
        pipeline = None
        try:
            pipeline = load_pipeline(config_path=self.config_path)
            clip = clip_from_path(path)
            verdict = pipeline.check(clip, register=self.auto_reg_var.get())
            self.root.after(0, self._display_check_result, verdict)
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

    def _display_check_result(self, verdict: Verdict) -> None:
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
                    "The file is identical byte-for-byte. The vendor likely resubmitted the clip "
                    "accidentally without knowing we already possessed it. Standard procedure: "
                    "politely notify the vendor without contractual penalties."
                ),
                foreground="#1565c0",
            )
        elif verdict.intent_assessment == "edited_likely_intentional":
            self.intent_val.config(
                text="EDITED DERIVATIVE (Intentional Alteration / Evasion)", foreground="#c62828"
            )
            self.guidance_val.config(
                text=(
                    "Visual or audio content matches, but bytes were altered (re-encoded, "
                    "trimmed, or resized). This indicates intentional alteration or middleman "
                    "relabeling. Route to management/operations for fraud review."
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

        # Details
        self.details_txt.delete("1.0", tk.END)
        self.details_txt.insert(tk.END, f"Reason: {verdict.reason}\n")
        self.details_txt.insert(tk.END, f"Clip SHA-256: {verdict.clip_id}\n\n")

        if verdict.matches:
            self.details_txt.insert(tk.END, "Matched Clip Evidence:\n")
            for m in verdict.matches:
                st = f" [Status: {m.matched_status}]" if m.matched_status else ""
                self.details_txt.insert(
                    tk.END,
                    f"  - Detector: {m.detector}\n"
                    f"    Matched ID: {m.matched_clip_id}\n"
                    f"    Confidence: {m.confidence:.2f}{st}\n"
                    f"    Evidence:   {json.dumps(dict(m.evidence))}\n",
                )
        else:
            self.details_txt.insert(tk.END, "No individual candidate matches found.\n")

        self._refresh_stats()

    # ------------------ TAB 2: INDEX LIBRARY ------------------
    def _build_index_tab(self) -> None:
        ctrl_frame = ttk.LabelFrame(self.tab_index, text="Library Folder or File", padding="8")
        ctrl_frame.pack(fill=tk.X, pady=4)

        self.index_path_var = tk.StringVar(value=self.cfg.source.path)
        entry = ttk.Entry(ctrl_frame, textvariable=self.index_path_var, font=("Arial", 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        browse_dir_btn = ttk.Button(ctrl_frame, text="Folder...", command=self._browse_index_dir)
        browse_dir_btn.pack(side=tk.LEFT, padx=2)

        browse_f_btn = ttk.Button(ctrl_frame, text="File...", command=self._browse_index_file)
        browse_f_btn.pack(side=tk.LEFT, padx=2)

        ttk.Label(ctrl_frame, text="Status:").pack(side=tk.LEFT, padx=(6, 2))
        self.status_choice_var = tk.StringVar(value="approved")
        status_menu = ttk.Combobox(
            ctrl_frame,
            textvariable=self.status_choice_var,
            values=["approved", "rejected", "pending"],
            width=10,
            state="readonly",
        )
        status_menu.pack(side=tk.LEFT, padx=2)

        start_btn = ttk.Button(ctrl_frame, text="📥 Start Indexing", command=self._run_index_async)
        start_btn.pack(side=tk.LEFT, padx=6)

        # Indexing Log Text
        log_frame = ttk.LabelFrame(self.tab_index, text="Indexing Activity Log", padding="8")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=6)

        self.index_log = tk.Text(log_frame, font=("Consolas", 9), wrap=tk.WORD)
        self.index_log.pack(fill=tk.BOTH, expand=True)

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

    def _run_index_async(self) -> None:
        target_path = self.index_path_var.get().strip()
        if not target_path:
            messagebox.showwarning("Input Needed", "Please select a directory or file to index.")
            return

        p = Path(target_path)
        if not p.exists():
            messagebox.showerror("Error", f"Path does not exist: {p}")
            return

        self.status_var.set(f"Indexing {p}...")
        self.index_log.insert(
            tk.END,
            f"\n--- Starting indexing for {p} (status: {self.status_choice_var.get()}) ---\n",
        )
        threading.Thread(target=self._run_index_worker, args=(p,), daemon=True).start()

    def _run_index_worker(self, path: Path) -> None:
        pipeline = None
        count = 0
        try:
            pipeline = load_pipeline(config_path=self.config_path)
            source = LocalSource(path, status=self.status_choice_var.get())
            for clip in source.iter_clips():
                pipeline.index(clip)
                count += 1
                self.root.after(0, self._log_indexed_clip, clip.local_path.name, clip.clip_id)
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
    def _build_stats_tab(self) -> None:
        box = ttk.LabelFrame(self.tab_stats, text="Database Counts", padding="16")
        box.pack(fill=tk.BOTH, expand=True)

        self.stats_total_var = tk.StringVar(value="Total clips: —")
        self.stats_app_var = tk.StringVar(value="Approved clips: —")
        self.stats_rej_var = tk.StringVar(value="Rejected clips: —")
        self.stats_pend_var = tk.StringVar(value="Pending clips: —")

        ttk.Label(box, textvariable=self.stats_total_var, font=("Arial", 12, "bold")).pack(
            anchor=tk.W, pady=4
        )
        ttk.Label(
            box, textvariable=self.stats_app_var, font=("Arial", 11), foreground="#2e7d32"
        ).pack(anchor=tk.W, pady=3)
        ttk.Label(
            box, textvariable=self.stats_rej_var, font=("Arial", 11), foreground="#c62828"
        ).pack(anchor=tk.W, pady=3)
        ttk.Label(
            box, textvariable=self.stats_pend_var, font=("Arial", 11), foreground="#e65100"
        ).pack(anchor=tk.W, pady=3)

        ttk.Button(box, text="🔄 Refresh Statistics", command=self._refresh_stats).pack(
            anchor=tk.W, pady=16
        )

    def _refresh_stats(self) -> None:
        try:
            if self.cfg.store.type != "sqlite":
                return
            store = SqliteStore(self.cfg.store.path)
            try:
                tot = store.clips.count()
                app = store.clips.count(status="approved")
                rej = store.clips.count(status="rejected")
                pend = store.clips.count(status="pending")

                self.stats_total_var.set(f"Total clips indexed: {tot}")
                self.stats_app_var.set(f"  - Approved:   {app}")
                self.stats_rej_var.set(f"  - Rejected:   {rej}")
                self.stats_pend_var.set(f"  - Pending:    {pend}")
            finally:
                store.close()
        except Exception:
            pass


def launch_gui(config_path: Optional[Path] = None) -> None:
    root = tk.Tk()
    # Configure basic clean ttk styling
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    ClipGuardGUI(root, config_path=config_path)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
