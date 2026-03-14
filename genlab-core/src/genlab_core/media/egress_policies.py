"""Per-stage egress policy definitions for sandboxed pipeline execution.

Each stage type maps to a list of allowed domains.  An empty list means
deny-all outbound network (the default for render stages).

Usage:
    from genlab_core.media.egress_policies import get_egress_allow

    allowed = get_egress_allow("render")       # [] — no network
    allowed = get_egress_allow("fetch_gaming")  # ["api.twitch.tv", ...]
    allowed = get_egress_allow("unknown")       # [] — unknown stages are locked

Override via ``config/egress_policies.yaml`` in the GenLab root to add
custom domains without touching code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Built-in policies (code-level defaults) ──────────────────────────────────
# These are the minimum required domains per stage.  The YAML overlay can only
# ADD domains, never remove built-in ones.

_BUILTIN_POLICIES: dict[str, list[str]] = {
    # Render stages: pure file I/O, zero network.
    "render": [],
    # Gaming fetch: Twitch, Steam, RSS feeds
    "fetch_gaming": [
        "api.twitch.tv",
        "store.steampowered.com",
        "api.steampowered.com",
    ],
    # Gaming enrich: IGDB + Claude for fallback extraction
    "enrich_gaming": [
        "api.igdb.com",
        "api.anthropic.com",
    ],
    # BB fetch: RSS sources + scraping (broad, many domains)
    "fetch_ai_creators": [
        "*.openai.com",
        "*.anthropic.com",
        "*.theverge.com",
        "civitai.com",
        "huggingface.co",
        "*.reddit.com",
    ],
    # Publish: platform APIs + OAuth token refresh
    "publish": [
        "graph.facebook.com",
        "graph.threads.net",
        "oauth2.googleapis.com",
        "*.googleapis.com",
        "api.twitter.com",
        "upload.twitter.com",
        "open.tiktokapis.com",
    ],
    # UGC media fetch: stock media providers
    "fetch_ugc": [
        "api.pexels.com",
        "*.pexels.com",
        "pixabay.com",
        "api.unsplash.com",
        "*.unsplash.com",
    ],
    # TTS: voice synthesis providers
    "tts": [
        "api.elevenlabs.io",
        "api.openai.com",
    ],
    # Engagement: reply/like on platforms
    "engagement": [
        "graph.facebook.com",
        "graph.threads.net",
        "*.googleapis.com",
        "api.twitter.com",
    ],
    # Metrics collection: analytics APIs
    "metrics": [
        "graph.facebook.com",
        "*.googleapis.com",
        "api.twitter.com",
        "open.tiktokapis.com",
    ],
}

_yaml_overlay: dict[str, list[str]] | None = None


def _load_yaml_overlay(config_path: Path | None = None) -> dict[str, list[str]]:
    """Load optional YAML overlay for egress policies."""
    global _yaml_overlay
    if _yaml_overlay is not None:
        return _yaml_overlay

    if config_path is None:
        # Try GenLab root / config / egress_policies.yaml
        candidates = [
            Path(__file__).resolve().parents[4] / "config" / "egress_policies.yaml",
        ]
    else:
        candidates = [config_path]

    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    data: Any = yaml.safe_load(f)
                if isinstance(data, dict):
                    _yaml_overlay = {
                        k: v for k, v in data.items() if isinstance(v, list)
                    }
                    logger.info("[EGRESS] Loaded overlay from %s", path)
                    return _yaml_overlay
            except Exception as e:
                logger.warning("[EGRESS] Failed to load %s: %s", path, e)

    _yaml_overlay = {}
    return _yaml_overlay


def get_egress_allow(stage: str, *, config_path: Path | None = None) -> list[str]:
    """Return the allowed domain list for a pipeline stage.

    Merges built-in defaults with any YAML overlay.  Unknown stages
    get an empty list (deny-all).
    """
    builtin = list(_BUILTIN_POLICIES.get(stage, []))
    overlay = _load_yaml_overlay(config_path).get(stage, [])
    # Merge (dedup, preserve order)
    seen: set[str] = set(builtin)
    for domain in overlay:
        if domain not in seen:
            builtin.append(domain)
            seen.add(domain)
    return builtin
