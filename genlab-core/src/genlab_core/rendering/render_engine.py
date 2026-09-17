"""Which renderer a niche uses, and the guarantee that protects the old one.

RENDER-01 §3. Two engines live side by side:

  legacy  the FFmpeg path that publishes today
  craft   the approved TALK/ACTION treatments

``legacy`` is not a fallback that craft may lean on -- it is the standing
guarantee that FFmpeg produces a publishable reel standalone at every stage
(CLAUDE.md render-path rule). Craft has to earn the switch; until it does,
legacy publishes and craft is written beside it and looked at.

Resolution order, most specific first:
  1. GENLAB_RENDER_ENGINE_<NICHE>   per-niche override, for a single fire
  2. GENLAB_RENDER_ENGINE           global override, for a bad afternoon
  3. render.engine in the niche's config
  4. legacy

The dual-render mode is deliberately NOT a third engine value. It is a separate
question -- "also render craft and keep it" -- because conflating "what
publishes" with "what gets rendered" is how a shadow renderer ends up
publishing by accident.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum

logger = logging.getLogger(__name__)


class Engine(StrEnum):
    LEGACY = "legacy"
    CRAFT = "craft"


DEFAULT_ENGINE = Engine.LEGACY


def _from_env(niche_id: str) -> str | None:
    per_niche = os.environ.get(f"GENLAB_RENDER_ENGINE_{niche_id.upper()}", "").strip()
    if per_niche:
        return per_niche
    return os.environ.get("GENLAB_RENDER_ENGINE", "").strip() or None


def resolve_engine(niche_id: str, niche_config: dict | None = None) -> Engine:
    """The engine that PUBLISHES for this niche."""
    raw = _from_env(niche_id)
    source = "env"
    if not raw:
        cfg = (niche_config or {}).get("render", {})
        raw = (cfg.get("engine") or "").strip() if isinstance(cfg, dict) else ""
        source = "config"
    if not raw:
        return DEFAULT_ENGINE
    try:
        engine = Engine(raw.lower())
    except ValueError:
        # An unknown engine name must not silently become craft.
        logger.warning(
            "[render-engine] niche=%s: unknown engine %r from %s — using %s",
            niche_id, raw, source, DEFAULT_ENGINE)
        return DEFAULT_ENGINE
    logger.info("[render-engine] niche=%s -> %s (from %s)", niche_id, engine, source)
    return engine


def dual_render_enabled(niche_id: str, niche_config: dict | None = None) -> bool:
    """Should the OTHER engine also render, for comparison, without publishing?

    True during the three-fire trial. The reel it produces is written beside the
    published one and never returned as the render result.
    """
    env = os.environ.get(f"GENLAB_RENDER_DUAL_{niche_id.upper()}",
                         os.environ.get("GENLAB_RENDER_DUAL", "")).strip()
    if env:
        return env != "0"
    cfg = (niche_config or {}).get("render", {})
    return bool(cfg.get("dual", False)) if isinstance(cfg, dict) else False


def craft_publishes(niche_id: str, niche_config: dict | None = None) -> bool:
    return resolve_engine(niche_id, niche_config) is Engine.CRAFT
