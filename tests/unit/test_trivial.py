"""Trivial sanity test to verify pytest framework operates properly."""

from __future__ import annotations

import dedupe


def test_package_version() -> None:
    assert dedupe.__version__ == "0.1.0"
