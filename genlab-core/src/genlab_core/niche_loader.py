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

# Shared pipeline backbone (P4, 2026-06-20). Niches that set
# ``pipeline_template: backbone`` at the top level have their
# ``pipeline.stages`` expanded by merging this template with the
# niche's ``pipeline.inject:`` block. See module ``pipeline_template.yaml``
# for the inject point inventory.
_PIPELINE_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "config" / "pipeline_template.yaml"
_pipeline_template_cache: dict[str, dict] | None = None


def _load_pipeline_template(name: str) -> dict:
    """Return the named pipeline template, cached for the process lifetime.

    Currently only ``"backbone"`` is recognised — it resolves to
    ``genlab-core/config/pipeline_template.yaml``. Unknown names raise
    ``ValueError`` so a typo in ``pipeline_template:`` surfaces loudly
    at load time, not at pipeline runtime.
    """
    global _pipeline_template_cache
    if _pipeline_template_cache is None:
        _pipeline_template_cache = {}
        if _PIPELINE_TEMPLATE_PATH.exists():
            with open(_PIPELINE_TEMPLATE_PATH, encoding="utf-8") as f:
                _pipeline_template_cache["backbone"] = yaml.safe_load(f) or {}
    if name not in _pipeline_template_cache:
        raise ValueError(
            f"Unknown pipeline_template '{name}' — only 'backbone' is currently "
            f"defined (in {_PIPELINE_TEMPLATE_PATH}). Remove the directive or "
            f"fix the typo."
        )
    return _pipeline_template_cache[name]


def merge_pipeline_template(niche_config: dict) -> dict:
    """Expand ``pipeline_template:`` directive in a niche config.

    When ``niche_config`` has a top-level ``pipeline_template: <name>``
    key, this function merges that template's ``pipeline.stages`` with
    the niche's ``pipeline.inject:`` block: every ``- inject: <point>``
    entry in the template is replaced with the corresponding list from
    the niche's inject block (empty list when the niche didn't define
    that inject point — supports optional inject slots).

    Returns ``niche_config`` with ``pipeline.stages`` set to the merged
    list. The niche's other ``pipeline.*`` keys
    (``max_items_per_run``, ``dedup_window_days``, etc.) are preserved.

    If ``pipeline_template:`` is absent, returns ``niche_config``
    unchanged — niches keep their legacy explicit ``pipeline.stages``.

    Raises ``ValueError`` on:
      * unknown template name (typo guard)
      * unfilled required inject point (template declares it, niche
        forgot to supply it AND it isn't in the optional set)
      * extra inject keys the niche declared that the template doesn't
        reference (drift guard — silent typo would mean the stage
        never runs)
    """
    template_name = niche_config.get("pipeline_template")
    if not template_name:
        return niche_config

    template = _load_pipeline_template(str(template_name))
    template_stages = (template.get("pipeline") or {}).get("stages") or []
    niche_inject = (niche_config.get("pipeline") or {}).get("inject") or {}

    # Collect the inject points the template references — these are the
    # "named slots" the niche can fill. Anything else in inject is an
    # error (typo or stale config).
    template_points: list[str] = [
        entry["inject"]
        for entry in template_stages
        if isinstance(entry, dict) and "inject" in entry
    ]
    template_point_set = set(template_points)

    # Drift guard: niche declares an inject the template doesn't use.
    extra = set(niche_inject) - template_point_set
    if extra:
        raise ValueError(
            f"Niche '{niche_config.get('niche_id', '?')}' declares inject points "
            f"the '{template_name}' template doesn't reference: {sorted(extra)}. "
            f"Valid points: {sorted(template_point_set)}. Remove the extras or "
            f"fix the typo."
        )

    # Optional inject points (template tolerates these being omitted).
    # Currently every point is optional — niches that don't need a
    # phase1_relevance_gate or phase1_fetchers_extra just leave it out.
    # If you want a point to be MANDATORY, list it here and the loader
    # will raise on missing entries.
    optional_points = template_point_set  # all-optional for v1

    merged_stages: list[dict] = []
    for entry in template_stages:
        if isinstance(entry, dict) and "inject" in entry:
            point = entry["inject"]
            if point in niche_inject:
                injected = niche_inject[point] or []
                if not isinstance(injected, list):
                    raise ValueError(
                        f"Niche '{niche_config.get('niche_id', '?')}' inject "
                        f"point '{point}' must be a list of stage entries, "
                        f"got {type(injected).__name__}"
                    )
                merged_stages.extend(injected)
            elif point not in optional_points:
                raise ValueError(
                    f"Niche '{niche_config.get('niche_id', '?')}' is missing "
                    f"required inject point '{point}' for template "
                    f"'{template_name}'. Add it under pipeline.inject."
                )
            # else: optional point omitted — just splice in nothing
        else:
            merged_stages.append(entry)

    # Splice merged stages back into the niche config.
    niche_config.setdefault("pipeline", {})
    niche_config["pipeline"]["stages"] = merged_stages
    return niche_config


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

    # P4: expand ``pipeline_template:`` directive if the niche opts in.
    # No-op when the directive is absent — niches keep their legacy
    # explicit ``pipeline.stages`` list. Gaming stays on this path.
    niche_config = merge_pipeline_template(niche_config)

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
