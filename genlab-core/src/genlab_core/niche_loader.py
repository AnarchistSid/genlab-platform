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
from typing import Any

import yaml

# Module-level cache for load_yaml_config — keyed by resolved path.
_yaml_config_cache: dict[str, dict] = {}


def load_niche_config(niche_id: str, project_root: Path) -> dict:
    """Load a niche's config.

    Tries two locations in order:
      1. {project_root}/config/niche.yaml       — standalone channel folders
      2. {project_root}/niches/{niche_id}/config/niche.yaml — CriticalRush nested

    Additionally merges ``scoring_weights.yaml`` from the same config dir
    under the ``scoring_weights`` key on the returned config (if present).
    This means consumers like ``QCGates`` and ``base_content_research`` can
    read ``niche_config["scoring_weights"]["dedup"]`` without having to load
    a second file themselves.

    If ``scoring_weights.yaml`` defines a top-level ``dedup:`` block AND the
    niche config doesn't already have one, it is also promoted to
    ``niche_config["dedup"]`` so existing callers that read the top-level
    ``dedup`` block (like ``QCGates`` did with ``jaccard_threshold``) finally
    pick up the intended thresholds. Also normalizes the legacy
    ``similarity_threshold`` key to ``jaccard_threshold`` for consumers.

    Args:
        niche_id: The niche identifier (e.g. "gaming", "ai_creators").
        project_root: Absolute path to the calling agent's project root.

    Returns:
        The parsed YAML as a dict. Returns an empty dict if the file
        does not exist.
    """
    niche_config: dict = {}
    config_dir: Path | None = None

    # Standalone channel folder: config/ is directly under project_root
    standalone_path = project_root / "config" / "niche.yaml"
    if standalone_path.exists():
        with open(standalone_path, encoding="utf-8") as f:
            niche_config = yaml.safe_load(f) or {}
        config_dir = standalone_path.parent
    else:
        # CriticalRush nested pattern: niches/{id}/config/
        nested_path = project_root / "niches" / niche_id / "config" / "niche.yaml"
        if nested_path.exists():
            with open(nested_path, encoding="utf-8") as f:
                niche_config = yaml.safe_load(f) or {}
            config_dir = nested_path.parent

    if config_dir is None:
        return niche_config

    # Merge scoring_weights.yaml so consumers can read thresholds without
    # reaching outside niche_config. See issue M: four niches had dedup
    # blocks in scoring_weights.yaml that were never loaded.
    sw_path = config_dir / "scoring_weights.yaml"
    if sw_path.exists():
        try:
            with open(sw_path, encoding="utf-8") as f:
                sw_data = yaml.safe_load(f) or {}
            if isinstance(sw_data, dict):
                niche_config.setdefault("scoring_weights", sw_data)
                # Promote the dedup block to the top-level if the niche
                # config doesn't already have one. Normalize the legacy
                # similarity_threshold → jaccard_threshold key name.
                dedup_block = sw_data.get("dedup") or {}
                if dedup_block and "dedup" not in niche_config:
                    normalized = dict(dedup_block)
                    if (
                        "jaccard_threshold" not in normalized
                        and "similarity_threshold" in normalized
                    ):
                        normalized["jaccard_threshold"] = normalized["similarity_threshold"]
                    niche_config["dedup"] = normalized
        except yaml.YAMLError:
            # Bad YAML in scoring_weights should NOT break niche loading.
            pass

    # Merge visuals.yaml under the ``visuals`` key so render stages can read
    # the animation / caption config without reaching outside niche_config.
    # RenderWhisperCaptions reads visuals.animation.word_by_word.whisper_sync —
    # previously visuals.yaml was never loaded, so word-by-word captions
    # silently no-op'd on every run despite whisper_sync.enabled: true.
    vis_path = config_dir / "visuals.yaml"
    if vis_path.exists():
        try:
            with open(vis_path, encoding="utf-8") as f:
                vis_data = yaml.safe_load(f) or {}
            if isinstance(vis_data, dict):
                niche_config.setdefault("visuals", vis_data)
        except yaml.YAMLError:
            # Bad YAML in visuals.yaml should NOT break niche loading.
            pass

    return niche_config


def get_feature_flags(niche_id: str, project_root: Path) -> dict[str, Any]:
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

    with open(full_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _yaml_config_cache[key] = data
    return data
