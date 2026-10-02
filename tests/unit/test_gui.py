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
    finally:
        root.destroy()
