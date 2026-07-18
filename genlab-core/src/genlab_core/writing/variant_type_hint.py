"""Variant-type bandit hint for the writer — Layer 3 S5 (2026-07-18).

Symmetric to :mod:`genlab_core.writing.content_type_hint` but for the
structural variant dimension (single_clip / series_part / question_reveal /
watch_till_end / split_screen / storytime) shipped in Layer 3 S1-S4b.

Reads ``bandit_arms`` rows matching ``variant:{niche_id}:{X}`` (the
namespace populated by S5-prep at
:mod:`genlab_core.publishing.feedback_registration._build_bandit_context`),
Thompson-samples across them, and returns the winning variant name.

## Consumer wire

Called from :func:`video_content_writer.write_video_content` alongside
``content_type_hint``. Injects an INFORMATIONAL prompt section that
tells the writer which structural variant has been performing best
lately on this niche.

**Does NOT override rule-based variant SELECTION** (which happens at
push_to_backlog via series_detector / question_reveal_selector / etc).
The bandit hint is soft — the LLM knows the audience preference but
the pipeline's structural detectors still decide what actually ships.

This is deliberate: the bandit is learning from reward AFTER
structural variants are applied. It measures "which variant works
best" but doesn't know which variants a specific story is even
eligible for (that's what the selectors decide via title patterns +
duration + priority chain).

Future S6+ work could unify this: bandit as a tie-breaker when a
story matches multiple variant selectors, or as an exploration
signal when structural variants have high variance.

## Cold-start behavior

Returns ``None`` when:

- Niche has no variant_type arms in bandit_arms yet (day-1 post-S5-prep)
- BacklogClient / arm_loader unavailable
- Zero non-single_clip arms have observations (single_clip default
  wins uninteresting Thompson samples)

None is the well-defined "no bandit steer" signal — the caller
proceeds with an unhinted writer prompt (identical to pre-S5 behavior).

## Data readiness

S5-prep deployed 2026-07-17 evening. Arm attribution wire is live but
reward observations require 48h+ window per niche's fastest metric_
collector cycle. This module is meaningful from ~2026-07-19 onward.
Ships before data-ready so the wire is in place; graceful cold-start
handles the pre-data window.
"""

from __future__ import annotations

import logging
import random

from genlab_core.variant_types import DEFAULT_VARIANT, VARIANT_TYPES

logger = logging.getLogger(__name__)


# Human-readable prompt hints per variant type. Used to translate a
# picked variant into concrete steering language for the writer. Kept
# separate from ``variant_types.PAYLOAD_CONTRACTS`` because payload
# contracts are structural (what fields the variant needs) while these
# are stylistic (how the writer should think about the variant).
_VARIANT_ANGLE_HINTS: dict[str, str] = {
    "single_clip": (
        "single clip frame — one hook, one payoff. This is the "
        "workhorse baseline — clean, direct, no structural gimmick."
    ),
    "series_part": (
        "series frame — reference the arc, tease previous or next "
        "parts, reward viewers who've been following."
    ),
    "question_reveal": (
        "question frame — pose a specific answerable question, promise "
        "the reveal at the payoff moment."
    ),
    "watch_till_end": (
        "compilation frame — engineer completion, set up a specific reward at the video's climax."
    ),
    "split_screen": (
        "reaction frame — treat the source video as one side of a "
        "conversation, add commentary that lands harder alongside it."
    ),
    "storytime": (
        "narrative frame — tell a story with setup, escalation, "
        "payoff. Audience sits through the whole thing for the arc."
    ),
}


def pick_variant_type_hint(niche_id: str) -> str | None:
    """Thompson-sample a variant_type arm from ``bandit_arms``.

    Reads ``bandit_arms`` rows for the niche via
    :func:`arm_loader.load_all_arms`, filters to arms matching
    ``variant:{niche_id}:{variant_type}``, and draws Beta samples on
    each. Returns the variant_type name of the highest-sampled arm.

    Returns ``None`` when:
      * No variant arms exist yet for this niche (cold-start).
      * BacklogClient / arm loader is unavailable.
      * Any unexpected error occurs.
      * Only single_clip arm exists (single_clip is the default —
        no reason to inject a "prefer single_clip" hint that just
        re-states the fallback).
    """
    if not niche_id:
        return None

    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return None

    try:
        client = BacklogClient()
    except Exception as exc:
        logger.warning(
            "[variant_type_hint] BacklogClient failed for niche=%s: %s — no bandit steer",
            niche_id,
            exc,
        )
        return None

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return None

    try:
        arms = load_all_arms(proxy, niche_id)
    except Exception as exc:
        logger.warning(
            "[variant_type_hint] arm load failed for niche=%s: %s",
            niche_id,
            exc,
        )
        return None

    # Filter to arms in the ``variant:{niche_id}:{variant_type}`` namespace.
    # The prefix is set by S5-prep at feedback_registration._build_bandit_context.
    #
    # Per-platform arms may come through as
    # ``variant:{niche}:{variant}__{platform}`` (mirrors content_type's
    # bandit_platform_split.py pattern). Strip the platform suffix
    # BEFORE the enum check — otherwise ``series_part__youtube`` looks
    # unknown and gets filtered out (bug caught by
    # ``test_platform_split_arms_aggregate``).
    variant_arms: dict[str, tuple[float, float]] = {}
    prefix = f"variant:{niche_id}:"
    for arm_id, (alpha, beta) in arms.items():
        if not arm_id.startswith(prefix):
            continue
        # Extract variant + optionally strip platform suffix
        variant_and_platform = arm_id[len(prefix) :]
        variant_name = variant_and_platform.split("__", 1)[0]
        if variant_name not in VARIANT_TYPES:
            # Guard against typos + stale enum drift (rule #22 sibling)
            continue
        if variant_name in variant_arms:
            prev_a, prev_b = variant_arms[variant_name]
            variant_arms[variant_name] = (prev_a + alpha, prev_b + beta)
        else:
            variant_arms[variant_name] = (alpha, beta)

    if not variant_arms:
        return None

    # Skip the hint when ONLY single_clip has data — the hint would
    # just re-state the pipeline's fallback, wasting prompt tokens
    # for no signal. Wait for at least one non-default arm to have
    # observations before steering the writer.
    non_default_arms = {k: v for k, v in variant_arms.items() if k != DEFAULT_VARIANT}
    if not non_default_arms:
        return None

    best_sample = -1.0
    best_name: str | None = None
    for name, (alpha, beta) in variant_arms.items():
        # Beta(1,1) is the uniform-prior default when arms are cold.
        # Same clamping content_type_hint uses.
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


def format_variant_type_prompt(variant_type: str) -> str:
    """Return the writer-prompt section for a picked variant_type.

    Empty string when the variant has no human-readable hint (unknown
    variant, or when caller passes ``None`` / empty). Follows the
    same ``str.join``-safe empty-string semantics as
    :func:`content_type_hint.format_content_angle_prompt`.

    The prompt is INFORMATIONAL — it tells the writer which structural
    variant has been performing best lately, but does NOT override
    the rule-based structural variant selection at push_to_backlog.
    Writer uses this to steer TONE + REGISTER; structural decisions
    still come from series_detector / question_reveal_selector / etc.
    """
    if not variant_type:
        return ""
    hint = _VARIANT_ANGLE_HINTS.get(variant_type)
    if not hint:
        return ""
    return (
        f"\nVARIANT FRAME PREFERENCE ({variant_type}): {hint}\n"
        "  Recent bandit posterior favours this variant. Bias TONE\n"
        "  toward it — if the story naturally fits, lean into the frame.\n"
        "  Structural variant selection happens elsewhere in the pipeline\n"
        "  (title-pattern + duration detectors). This hint steers the\n"
        "  writer's voice, not the pipeline's routing decision.\n"
    )


__all__ = [
    "format_variant_type_prompt",
    "pick_variant_type_hint",
]
