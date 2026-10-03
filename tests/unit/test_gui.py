"""Unit test for GUI initialization."""

from __future__ import annotations

import tkinter as tk

from dedupe.gui import ClipGuardGUI


def test_gui_initialization() -> None:
    root = tk.Tk()
    root.withdraw()  # Hide window during test
    try:
        app = ClipGuardGUI(root)
        assert app is not None
        assert "ClipGuard" in root.title()

        # Test mode toggle affects both tabs
        assert app.conn_mode_var.get() == "local"
        assert "Local" in app.index_prompt_lbl.cget("text")

        app._toggle_connection_mode()
        assert app.conn_mode_var.get() == "s3"
        assert "Amazon S3" in app.index_prompt_lbl.cget("text")
        assert app.index_path_var.get().startswith("s3://")

        # Toggle back to local
        app._toggle_connection_mode()
        assert app.conn_mode_var.get() == "local"
        assert "Local" in app.index_prompt_lbl.cget("text")

        # Test index preset
        app._set_index_preset("s3://my-test-bucket/clips/", is_s3=True)
        assert app.conn_mode_var.get() == "s3"
        assert app.index_path_var.get() == "s3://my-test-bucket/clips/"

        # Test refresh stats functionality
        app._refresh_stats(manual=False)
        assert "Total Reference Clips" in app.stats_total_var.get()
        assert "Frame Perceptual Hashes" in app.stats_frames_var.get()
        assert "Acoustic Fingerprints" in app.stats_audio_var.get()
        assert "Last updated" in app.stats_time_var.get()
        assert hasattr(app, "clips_tree")

        # Test select all
        app._select_all_clips()
        assert len(app.clips_tree.selection()) == len(app.clips_tree.get_children())
    finally:
        root.destroy()
