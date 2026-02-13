"""Configuration management with YAML loading and env var expansion."""

import os
import yaml
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "default.yaml"


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand environment variables in string values."""
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    elif isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def load_config(path: str | Path | None = None) -> dict:
    """Load configuration from YAML file with env var expansion."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    return _expand_env_vars(raw)


def get_nested(config: dict, key_path: str, default: Any = None) -> Any:
    """Get a nested config value using dot notation. e.g. 'risk.max_daily_loss_pct'"""
    keys = key_path.split(".")
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
