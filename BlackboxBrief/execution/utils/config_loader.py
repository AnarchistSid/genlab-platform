"""Centralized YAML config loader with caching.

Provides a single function to load any config file from config/ directory.
Caches loaded configs in memory to avoid repeated disk reads within a run.
"""

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

# Project root: two levels up from this file (execution/utils/ -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# In-memory cache for loaded configs (survives for the process lifetime)
_config_cache: Dict[str, Any] = {}


def load_config(
    name: str,
    *,
    required: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Load a YAML config file from the config/ directory.

    Args:
        name: Config filename (e.g., "sources.yaml", "publishing.yaml").
              Can also be a full path for configs outside config/.
        required: If True (default), raise FileNotFoundError when file missing.
                  If False, return empty dict and log warning.
        use_cache: If True (default), return cached version if already loaded.

    Returns:
        Parsed YAML as a dict.

    Raises:
        FileNotFoundError: If required=True and file doesn't exist.
    """
    # Resolve path
    config_path = Path(name)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / "config" / name

    cache_key = str(config_path)

    if use_cache and cache_key in _config_cache:
        return _config_cache[cache_key]

    if not config_path.exists():
        if required:
            raise FileNotFoundError(f"Required config not found: {config_path}")
        logger.warning("Optional config not found: %s — using empty dict", config_path)
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if use_cache:
        _config_cache[cache_key] = data

    logger.debug("Loaded config: %s (%d keys)", config_path.name, len(data))
    return data


def clear_cache() -> None:
    """Clear the in-memory config cache (useful for testing)."""
    _config_cache.clear()
