"""Centralized YAML config loader with caching.

Provides a single function to load any config file from config/ directory.
Caches loaded configs in memory to avoid repeated disk reads within a run.

Shared-config routing
---------------------

A handful of yamls are owned by ``genlab-core`` and read by every niche
(SharePoint list IDs, daily post caps, disk quotas, etc.). Until this
file, BB resolved them through filesystem symlinks at
``BlackboxBrief/config/<name>.yaml -> ../../genlab-core/config/<name>.yaml``.

The symlinks were the last "BB-as-special-case" architectural debt: they
made ``git status`` report shared files as BB-owned, broke cross-platform
checkouts (Windows, archive extracts), and required every reader to know
about the link hop. The set below makes the dependency declarative — these
seven yamls always resolve directly to ``genlab-core/config/`` and the
symlinks are gone. Anything not on this list still resolves under BB's own
``config/`` directory, byte-stable.
"""

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

# Project root: two levels up from this file (execution/utils/ -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Source of truth for the seven shared yamls (previously symlinked into
# BB/config/). Adding a new name here is the one and only step required to
# include a future genlab-core config under BB's load_config().
SHARED_CONFIG_NAMES: frozenset[str] = frozenset(
    {
        "disk_quota.yaml",
        "error_budgets.yaml",
        "lists_config.yaml",
        "platform_caps.yaml",
        "publishing.yaml",
        "rate_limits.yaml",
        "risk_rules.yaml",
    }
)

# genlab-core/config/ relative to BB's project root.
_GENLAB_CORE_CONFIG_DIR: Path = PROJECT_ROOT.parent / "genlab-core" / "config"

# In-memory cache for loaded configs (survives for the process lifetime)
_config_cache: Dict[str, Any] = {}


def _resolve_config_path(name: str) -> Path:
    """Resolve a bare config name to its on-disk path.

    * Absolute paths pass through unchanged.
    * Names in :data:`SHARED_CONFIG_NAMES` resolve under
      ``genlab-core/config/`` (the canonical source).
    * Everything else stays under ``BlackboxBrief/config/`` — that's where
      BB-specific yamls live (``sources.yaml``, ``persona.yaml``, …).

    The split is intentionally declarative rather than "fall back to
    genlab-core on miss": a typo like ``publishng.yaml`` should fail loudly
    against BB's own config directory, not silently re-route.
    """
    path = Path(name)
    if path.is_absolute():
        return path
    if name in SHARED_CONFIG_NAMES:
        return _GENLAB_CORE_CONFIG_DIR / name
    return PROJECT_ROOT / "config" / name


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
    # Resolve path (shared names → genlab-core/config/, others → BB/config/).
    config_path = _resolve_config_path(name)

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
