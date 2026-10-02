"""Unit tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from dedupe.config import ConfigError, load_config


def test_load_default_config_fallback() -> None:
    cfg = load_config(path=None)
    assert cfg.pipeline.combiner == "any"
    assert cfg.store.type == "sqlite"
    assert cfg.exact_sha256.enabled is True


def test_load_valid_custom_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "custom.toml"
    cfg_file.write_text(
        """
[pipeline]
combiner = "corroborate"
register_after_check = false

[source]
type = "local"
path = "./test_data"

[store]
type = "sqlite"
path = "./test.db"

[detectors.exact_sha256]
enabled = false

[detectors.frame_phash]
enabled = true
fps = 2
radius = 2
num_chunks = 4
""",
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file)
    assert cfg.pipeline.combiner == "corroborate"
    assert cfg.pipeline.register_after_check is False
    assert cfg.source.path == "./test_data"
    assert cfg.store.path == "./test.db"
    assert cfg.exact_sha256.enabled is False
    assert cfg.frame_phash.fps == 2


def test_unknown_top_level_section(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad.toml"
    cfg_file.write_text("[unknown_section]\nfoo = 'bar'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown top-level configuration section"):
        load_config(path=cfg_file)


def test_unknown_pipeline_key(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad_key.toml"
    cfg_file.write_text("[pipeline]\ninvalid_opt = 123\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key in \\[pipeline\\]"):
        load_config(path=cfg_file)


def test_invalid_combiner_policy(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad_policy.toml"
    cfg_file.write_text("[pipeline]\ncombiner = 'magic'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid combiner policy"):
        load_config(path=cfg_file)


def test_pigeonhole_constraint_violation(tmp_path: Path) -> None:
    cfg_file = tmp_path / "pigeonhole.toml"
    cfg_file.write_text(
        """
[detectors.frame_phash]
enabled = true
radius = 4
num_chunks = 4
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Pigeonhole violation"):
        load_config(path=cfg_file)


def test_environment_variable_overrides() -> None:
    env = {
        "DEDUPE_STORE__PATH": "./override.db",
        "DEDUPE_PIPELINE__COMBINER": "weighted",
        "DEDUPE_PIPELINE__REGISTER_AFTER_CHECK": "false",
        "DEDUPE_DETECTORS__EXACT_SHA256__ENABLED": "0",
    }
    cfg = load_config(path=None, env=env)
    assert cfg.store.path == "./override.db"
    assert cfg.pipeline.combiner == "weighted"
    assert cfg.pipeline.register_after_check is False
    assert cfg.exact_sha256.enabled is False
