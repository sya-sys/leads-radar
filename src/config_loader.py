"""
src/config_loader.py
─────────────────────
Single source of truth for all pipeline settings.
Everything reads from config.json — no hardcoded constants elsewhere.
"""

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")
_config: dict | None = None


def load_config() -> dict:
    """Load and cache config.json from repo root."""
    global _config
    if _config is None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _config = json.load(f)
    return _config


def get_enabled_sources(config: dict) -> list[dict]:
    """Return only sources with enabled=true."""
    return [s for s in config["sources"] if s.get("enabled", True)]


def get_columns(config: dict) -> list[str]:
    return config["output"]["columns"]


def get_crm_columns(config: dict) -> list[str]:
    return config["output"].get("crm_columns", [])


def get_extraction_config(config: dict) -> dict:
    return config["extraction"]
