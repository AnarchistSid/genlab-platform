"""Content-type bandit hint for the video content writer.

Task #628 (2026-07-09, roadmap E2). Pre-#628 the writer prompt
already surfaced the ``style:*`` bandit arm as a STYLE MANDATE
(see ``video_content_writer.write_video_content``). This module
adds the symmetric hint for ``content_type`` arms:

  * gaming: ``gameplay_clip`` / ``esports_highlight`` /
    ``trailer_reaction`` / ``patch_news``
  * sports: ``highlight_play`` / ``breaking_trade`` /
    ``record_milestone`` / ``upset_reaction``
  * movies: ``trailer_drop`` / ``scene_reaction`` /
    ``box_office_update`` / ``cast_reveal``
  * anime: ``episode_moment`` / ``season_announcement`` /
    ``fight_scene`` / ``adaptation_news``
  * ai_creators: ``tool_demo`` / ``model_release`` /
    ``creative_showcase`` / ``comparison_test``

## Why this ships as a soft hint, not a mandate

The style dimension has a clean input→prompt→output loop: the
LLM directly controls hook style, so the mandate can be strict.
Content type has a keyword-based classifier that runs AFTER the
writer (see ``push_to_backlog._classify_arm_with_propensity``) and
scores the writer's output against per-niche keyword lists. Making
this a hard mandate would create a chicken-and-egg: the writer
must produce output that the post-hoc classifier will map to the
mandated arm — but that same classifier already picks arms based
on writer output. So this ships as a soft steering nudge:
"posterior currently favours X — bias if it fits."

## Wiring

``pick_content_type_hint(niche_id) → str | None`` returns the
Thompson-sampled winner's arm name (bare, no prefix). None when
no arms exist for this niche (cold-start) or on any error —
fail-open.

Consumer wire: ``video_content_writer.write_video_content`` calls
this and, if non-None, injects a CONTENT ANGLE PREFERENCE section
into the writer prompt.
"""

from __future__ import annotations

import logging
import random

logger = logging.getLogger(__name__)


# Known content_type arms per niche. Source of truth: ``_ARM_KEYWORDS``
# in ``pipeline/stages/push_to_backlog.py`` (the classifier that
# picks these post-hoc from writer output). Keep in sync when
# adding new content types.
_KNOWN_CONTENT_TYPES: dict[str, set[str]] = {
    "gaming": {
        "gameplay_clip",
        "esports_highlight",
        "trailer_reaction",
        "patch_news",
    },
    "sports": {
        "highlight_play",
        "breaking_trade",
        "record_milestone",
        "upset_reaction",
    },
    "movies": {
        "trailer_drop",
        "scene_reaction",
        "box_office_update",
        "cast_reveal",
    },
    "anime": {
        "episode_moment",
        "season_announcement",
        "fight_scene",
        "adaptation_news",
    },
    "ai_creators": {
        "tool_demo",
        "model_release",
        "creative_showcase",
        "comparison_test",
    },
}


# Human-readable prompt hints per content type. Used to translate
# an arm name into concrete steering language the LLM can act on.
_CONTENT_ANGLE_HINTS: dict[str, str] = {
    # gaming
    "gameplay_clip": "raw gameplay reaction — react to the moment, opinion-forward",
    "esports_highlight": ("esports frame — player names, team dynamics, tournament stakes"),
    "trailer_reaction": "trailer/reveal frame — build anticipation, drop specifics",
    "patch_news": "patch/meta frame — call out balance changes and community reactions",
    # sports
    "highlight_play": "clutch-play frame — react to the moment, specific play mechanics",
    "breaking_trade": "trade/transfer frame — implications for both teams",
    "record_milestone": "record frame — historical context, contextualize the number",
    "upset_reaction": "upset frame — underdog stakes, favorite's collapse",
    # movies
    "trailer_drop": "trailer frame — genre callouts, cast + director hooks",
    "scene_reaction": "scene reaction — specific character/actor moments",
    "box_office_update": "box-office frame — numbers with context, franchise stakes",
    "cast_reveal": "casting frame — role expectations, franchise-canon implications",
    # anime
    "episode_moment": "episode-moment frame — specific character/scene beat",
    "season_announcement": "season-drop frame — hype for confirmed adaptation",
    "fight_scene": "fight-scene frame — power scaling, animation callouts",
    "adaptation_news": "adaptation frame — studio + source-material fidelity",
    # ai_creators
    "tool_demo": "tool-demo frame — specific capability, tangible use case",
    "model_release": "release frame — model name, capabilities, availability",
    "creative_showcase": "creative-showcase frame — output medium, techniques used",
    "comparison_test": "comparison frame — clear head-to-head verdict",
}


def pick_content_type_hint(niche_id: str) -> str | None:
    """Thompson-sample a content-type arm from ``bandit_arms``.

    Reads ``bandit_arms`` rows for the niche, filters to the arms
    listed in ``_KNOWN_CONTENT_TYPES[niche_id]``, and draws Beta
    samples on each. Returns the arm name with the highest sample.

    Returns ``None`` when:
      * The niche has no known content types.
      * BacklogClient / arm loader is unavailable.
      * No content-type arms exist yet for this niche (cold-start).
      * Any unexpected error occurs.

    None is the well-defined "no bandit steer" signal — the caller
    proceeds with an unhinted writer prompt.
    """
    known = _KNOWN_CONTENT_TYPES.get(niche_id)
    if not known:
        return None

    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return None

    try:
        client = BacklogClient()
    except Exception:
        return None

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return None

    try:
        arms = load_all_arms(proxy, niche_id)
    except Exception as exc:
        logger.debug("[content_type_hint] arm load failed: %s", exc)
        return None

    # Per-platform arms come through as ``content_type__platform``
    # (see ``learning/bandit_platform_split.py``). Strip the platform
    # suffix so per-platform posteriors aggregate under one content
    # type for the hint. If BOTH the bare form and platform-split
    # forms exist, prefer the bare form (older, more observations).
    content_type_arms: dict[str, tuple[float, float]] = {}
    for arm_id, (alpha, beta) in arms.items():
        base = arm_id.split("__", 1)[0]
        if base not in known:
            continue
        if base in content_type_arms:
            # Sum posteriors when the same content-type appears
            # under multiple platform-split variants. This is a
            # crude aggregation but the alternative — picking the
            # first — would silently bias toward alphabetical
            # platform ordering.
            prev_a, prev_b = content_type_arms[base]
            content_type_arms[base] = (prev_a + alpha, prev_b + beta)
        else:
            content_type_arms[base] = (alpha, beta)

    if not content_type_arms:
        return None

    best_sample = -1.0
    best_name: str | None = None
    for name, (alpha, beta) in content_type_arms.items():
        a = alpha if alpha > 0 else 1.0
        b = beta if beta > 0 else 1.0
        try:
            sample = random.betavariate(a, b)
        except (ValueError, OverflowError):
            sample = 0.5
        if sample > best_sample:
            best_sample = sample
            best_name = name
    return best_name


def format_content_angle_prompt(content_type: str) -> str:
    """Return the writer-prompt section for a picked content-type.

    Empty string when the content-type has no human-readable hint
    (unknown arm name, cold-start). The caller uses ``str.join``
    semantics — an empty string safely omits the section.
    """
    hint = _CONTENT_ANGLE_HINTS.get(content_type)
    if not hint:
        return ""
    return (
        f"\nCONTENT ANGLE PREFERENCE ({content_type}): {hint}\n"
        "  Recent bandit posterior favours this angle. Bias toward it\n"
        "  IF the story genuinely fits. If it doesn't fit naturally,\n"
        "  ignore this hint and write to the story's real strengths.\n"
    )
