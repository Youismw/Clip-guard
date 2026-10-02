"""Configuration loading, validation, and environment variable overrides."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]


class ConfigError(ValueError):
    """Raised when configuration is invalid, missing, or malformed."""


@dataclass(frozen=True)
class PipelineConfig:
    combiner: str = "any"
    register_after_check: bool = True
    weights: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceConfig:
    type: str = "local"
    path: str = "./data/new"
    bucket: str = ""
    approved_bucket: str = ""
    prefix: str = ""
    region: str = "us-east-1"
    endpoint_url: Optional[str] = None


@dataclass(frozen=True)
class StoreConfig:
    type: str = "sqlite"
    path: str = "./dedupe.db"
    host: str = "localhost"
    port: int = 5432
    database: str = "clipguard"
    user: str = "postgres"
    password: str = ""
    sslmode: str = "prefer"


@dataclass(frozen=True)
class ExactSha256Config:
    enabled: bool = True
    flag_threshold: float = 1.0
    review_threshold: float = 1.0


@dataclass(frozen=True)
class FramePhashConfig:
    enabled: bool = True
    fps: int = 1
    query_fps: int = 4
    radius: int = 3
    num_chunks: int = 4
    offset_bin_s: int = 2
    min_frame_std: float = 8.0
    flag_threshold: float = 0.80
    review_threshold: float = 0.40
    review_min_seconds: int = 8


@dataclass(frozen=True)
class AudioChromaprintConfig:
    enabled: bool = False
    ber_threshold: float = 0.35
    flag_threshold: float = 0.80
    review_threshold: float = 0.50


@dataclass(frozen=True)
class Config:
    pipeline: PipelineConfig
    source: SourceConfig
    store: StoreConfig
    detectors: Mapping[str, Any]

    @property
    def exact_sha256(self) -> ExactSha256Config:
        cfg = self.detectors.get("exact_sha256", ExactSha256Config())
        return cfg if isinstance(cfg, ExactSha256Config) else ExactSha256Config(**cfg)

    @property
    def frame_phash(self) -> FramePhashConfig:
        cfg = self.detectors.get("frame_phash", FramePhashConfig())
        return cfg if isinstance(cfg, FramePhashConfig) else FramePhashConfig(**cfg)

    @property
    def audio_chromaprint(self) -> AudioChromaprintConfig:
        cfg = self.detectors.get("audio_chromaprint", AudioChromaprintConfig())
        return cfg if isinstance(cfg, AudioChromaprintConfig) else AudioChromaprintConfig(**cfg)


_KNOWN_TOP_LEVEL = {"pipeline", "source", "store", "detectors"}
_KNOWN_PIPELINE = {"combiner", "register_after_check", "weights"}
_KNOWN_SOURCE = {
    "type",
    "path",
    "bucket",
    "approved_bucket",
    "prefix",
    "region",
    "endpoint_url",
}
_KNOWN_STORE = {
    "type",
    "path",
    "host",
    "port",
    "database",
    "user",
    "password",
    "sslmode",
}
_KNOWN_DETECTORS = {"exact_sha256", "frame_phash", "audio_chromaprint"}

_KNOWN_EXACT = {"enabled", "flag_threshold", "review_threshold"}
_KNOWN_FRAME = {
    "enabled",
    "fps",
    "query_fps",
    "radius",
    "num_chunks",
    "offset_bin_s",
    "min_frame_std",
    "flag_threshold",
    "review_threshold",
    "review_min_seconds",
}
_KNOWN_AUDIO = {"enabled", "ber_threshold", "flag_threshold", "review_threshold"}


def _cast_env_val(val: str, target_type: type) -> Any:
    if target_type is bool:
        low = val.lower().strip()
        if low in {"1", "true", "yes", "on"}:
            return True
        elif low in {"0", "false", "no", "off"}:
            return False
        raise ConfigError(f"Cannot cast '{val}' to boolean")
    elif target_type is int:
        return int(val)
    elif target_type is float:
        return float(val)
    return val


def _apply_env_overrides(data: dict[str, Any], env: Mapping[str, str]) -> None:
    prefix = "DEDUPE_"
    for env_key, env_val in env.items():
        if not env_key.startswith(prefix):
            continue
        parts = [p.lower() for p in env_key[len(prefix) :].split("__")]
        if not parts:
            continue

        curr = data
        for part in parts[:-1]:
            if part not in curr or not isinstance(curr[part], dict):
                curr[part] = {}
            curr = curr[part]

        leaf = parts[-1]
        # Attempt smart typing
        low = env_val.lower().strip()
        if low in {"true", "false", "yes", "no", "1", "0"}:
            curr[leaf] = low in {"true", "yes", "1"}
        else:
            try:
                curr[leaf] = int(env_val)
            except ValueError:
                try:
                    curr[leaf] = float(env_val)
                except ValueError:
                    curr[leaf] = env_val


def validate_raw_config(raw: Mapping[str, Any]) -> None:
    for key in raw:
        if key not in _KNOWN_TOP_LEVEL:
            raise ConfigError(f"Unknown top-level configuration section: '{key}'")

    pipeline_raw = raw.get("pipeline", {})
    if not isinstance(pipeline_raw, dict):
        raise ConfigError("'pipeline' section must be a table")
    for k in pipeline_raw:
        if k not in _KNOWN_PIPELINE:
            raise ConfigError(f"Unknown key in [pipeline]: '{k}'")

    combiner = pipeline_raw.get("combiner", "any")
    if combiner not in {"any", "corroborate", "weighted"}:
        raise ConfigError(
            f"Invalid combiner policy: '{combiner}'. Must be 'any', 'corroborate', or 'weighted'"
        )

    source_raw = raw.get("source", {})
    if not isinstance(source_raw, dict):
        raise ConfigError("'source' section must be a table")
    for k in source_raw:
        if k not in _KNOWN_SOURCE:
            raise ConfigError(f"Unknown key in [source]: '{k}'")

    store_raw = raw.get("store", {})
    if not isinstance(store_raw, dict):
        raise ConfigError("'store' section must be a table")
    for k in store_raw:
        if k not in _KNOWN_STORE:
            raise ConfigError(f"Unknown key in [store]: '{k}'")

    detectors_raw = raw.get("detectors", {})
    if not isinstance(detectors_raw, dict):
        raise ConfigError("'detectors' section must be a table")
    for d_name in detectors_raw:
        if d_name not in _KNOWN_DETECTORS:
            raise ConfigError(f"Unknown detector in [detectors]: '{d_name}'")

    # Validate exact_sha256
    exact_raw = detectors_raw.get("exact_sha256", {})
    for k in exact_raw:
        if k not in _KNOWN_EXACT:
            raise ConfigError(f"Unknown key in [detectors.exact_sha256]: '{k}'")

    # Validate frame_phash
    frame_raw = detectors_raw.get("frame_phash", {})
    for k in frame_raw:
        if k not in _KNOWN_FRAME:
            raise ConfigError(f"Unknown key in [detectors.frame_phash]: '{k}'")

    if frame_raw.get("enabled", True):
        radius = int(frame_raw.get("radius", 3))
        num_chunks = int(frame_raw.get("num_chunks", 4))
        if num_chunks < radius + 1:
            err_msg = (
                f"Pigeonhole violation: num_chunks ({num_chunks}) "
                f"must be >= radius + 1 ({radius + 1})"
            )
            raise ConfigError(err_msg)

    # Validate audio_chromaprint
    audio_raw = detectors_raw.get("audio_chromaprint", {})
    for k in audio_raw:
        if k not in _KNOWN_AUDIO:
            raise ConfigError(f"Unknown key in [detectors.audio_chromaprint]: '{k}'")


def load_config(
    path: Optional[Union[Path, str]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Config:
    raw: dict[str, Any] = {}
    config_file: Optional[Path] = None

    if path is not None:
        config_file = Path(path)
        if not config_file.exists():
            raise ConfigError(f"Configuration file not found: {config_file}")
    elif Path("dedupe.toml").exists():
        config_file = Path("dedupe.toml")

    if config_file is not None:
        try:
            with config_file.open("rb") as f:
                loaded = tomllib.load(f)
                if not isinstance(loaded, dict):
                    raise ConfigError(f"Config file '{config_file}' did not parse to a table")
                raw = loaded
        except Exception as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"Error parsing TOML config '{config_file}': {exc}") from exc

    _apply_env_overrides(raw, os.environ if env is None else env)
    validate_raw_config(raw)

    p_raw = raw.get("pipeline", {})
    pipeline_cfg = PipelineConfig(
        combiner=p_raw.get("combiner", "any"),
        register_after_check=p_raw.get("register_after_check", True),
        weights=p_raw.get("weights", {}),
    )

    s_raw = raw.get("source", {})
    source_cfg = SourceConfig(
        type=s_raw.get("type", "local"),
        path=s_raw.get("path", "./data/new"),
        bucket=s_raw.get("bucket", ""),
        approved_bucket=s_raw.get("approved_bucket", ""),
        prefix=s_raw.get("prefix", ""),
        region=s_raw.get("region", "us-east-1"),
        endpoint_url=s_raw.get("endpoint_url"),
    )

    st_raw = raw.get("store", {})
    store_cfg = StoreConfig(
        type=st_raw.get("type", "sqlite"),
        path=st_raw.get("path", "./dedupe.db"),
        host=st_raw.get("host", "localhost"),
        port=int(st_raw.get("port", 5432)),
        database=st_raw.get("database", "clipguard"),
        user=st_raw.get("user", "postgres"),
        password=st_raw.get("password", ""),
        sslmode=st_raw.get("sslmode", "prefer"),
    )

    d_raw = raw.get("detectors", {})
    detectors_dict: dict[str, Any] = {}

    if "exact_sha256" in d_raw:
        exact_raw = d_raw["exact_sha256"]
        detectors_dict["exact_sha256"] = ExactSha256Config(
            enabled=exact_raw.get("enabled", True),
            flag_threshold=float(exact_raw.get("flag_threshold", 1.0)),
            review_threshold=float(exact_raw.get("review_threshold", 1.0)),
        )
    else:
        detectors_dict["exact_sha256"] = ExactSha256Config(enabled=True)

    if "frame_phash" in d_raw:
        frame_raw = d_raw["frame_phash"]
        detectors_dict["frame_phash"] = FramePhashConfig(
            enabled=frame_raw.get("enabled", True),
            fps=int(frame_raw.get("fps", 1)),
            query_fps=int(frame_raw.get("query_fps", 4)),
            radius=int(frame_raw.get("radius", 3)),
            num_chunks=int(frame_raw.get("num_chunks", 4)),
            offset_bin_s=int(frame_raw.get("offset_bin_s", 2)),
            min_frame_std=float(frame_raw.get("min_frame_std", 8.0)),
            flag_threshold=float(frame_raw.get("flag_threshold", 0.80)),
            review_threshold=float(frame_raw.get("review_threshold", 0.40)),
            review_min_seconds=int(frame_raw.get("review_min_seconds", 8)),
        )
    else:
        detectors_dict["frame_phash"] = FramePhashConfig(enabled=False)

    if "audio_chromaprint" in d_raw:
        audio_raw = d_raw["audio_chromaprint"]
        detectors_dict["audio_chromaprint"] = AudioChromaprintConfig(
            enabled=audio_raw.get("enabled", False),
            ber_threshold=float(audio_raw.get("ber_threshold", 0.35)),
            flag_threshold=float(audio_raw.get("flag_threshold", 0.80)),
            review_threshold=float(audio_raw.get("review_threshold", 0.50)),
        )
    else:
        detectors_dict["audio_chromaprint"] = AudioChromaprintConfig(enabled=False)

    return Config(
        pipeline=pipeline_cfg,
        source=source_cfg,
        store=store_cfg,
        detectors=detectors_dict,
    )
