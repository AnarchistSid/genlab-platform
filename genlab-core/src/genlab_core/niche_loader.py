"""Niche configuration loader.

Loads niche-specific YAML configs from the calling agent's project tree.
genlab-core never hardcodes paths — the agent passes its own project_root.

Usage:
    from genlab_core.niche_loader import load_niche_config, get_feature_flags
    from genlab_core.niche_loader import load_yaml_config

    config = load_niche_config("gaming", Path("/path/to/CriticalRush"))
    flags = get_feature_flags("gaming", Path("/path/to/CriticalRush"))
    pub = load_yaml_config(Path("/path/to/project"), "config/publishing.yaml")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

# Module-level cache for load_yaml_config — keyed by resolved path.
_yaml_config_cache: Dict[str, dict] = {}


def load_niche_config(niche_id: str, project_root: Path) -> dict:
    """Load a niche's config.

    Tries two locations in order:
      1. {project_root}/config/niche.yaml       — standalone channel folders
      2. {project_root}/niches/{niche_id}/config/niche.yaml — CriticalRush nested

    Args:
        niche_id: The niche identifier (e.g. "gaming", "ai_news").
        project_root: Absolute path to the calling agent's project root.

    Returns:
        The parsed YAML as a dict. Returns an empty dict if the file
        does not exist.
    """
    # Standalone channel folder: config/ is directly under project_root
    standalone_path = project_root / "config" / "niche.yaml"
    if standalone_path.exists():
        with open(standalone_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # CriticalRush nested pattern: niches/{id}/config/
    nested_path = project_root / "niches" / niche_id / "config" / "niche.yaml"
    if nested_path.exists():
        with open(nested_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    return {}


def get_feature_flags(niche_id: str, project_root: Path) -> Dict[str, Any]:
    """Return the feature_flags block from a niche config.

    Args:
        niche_id: The niche identifier.
        project_root: Absolute path to the calling agent's project root.

    Returns:
        The feature_flags dict, or {} if not defined.
    """
    config = load_niche_config(niche_id, project_root)
    return config.get("feature_flags", {})


def load_yaml_config(project_root: Path, relative_path: str) -> dict:
    """Load and cache a YAML config file relative to a project root.

    Args:
        project_root: Absolute path to the calling agent's project root.
        relative_path: Path relative to project_root (e.g. "config/publishing.yaml").

    Returns:
        Parsed YAML as a dict. Returns {} if file does not exist.
        Results are cached per resolved path for the process lifetime.
    """
    full_path = project_root / relative_path
    key = str(full_path.resolve())
    if key in _yaml_config_cache:
        return _yaml_config_cache[key]

    if not full_path.exists():
        _yaml_config_cache[key] = {}
        return {}

    with open(full_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _yaml_config_cache[key] = data
    return data
