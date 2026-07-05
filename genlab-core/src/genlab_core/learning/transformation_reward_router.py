"""Multi-arm reward attribution for the intelligent transformation stack.

When a reel publishes with transformation choices applied, the
``pending_feedback.arm_ids_by_dimension`` JSONB carries N arm
assignments (one per selected transformation dimension). At reward
time (6h/24h/48h/168h collection windows), this router iterates
those arm IDs and updates each dimension's arm posterior.

Architecture — dimension-specific reward attribution:

    time in reel:  0s   3s        15s          30s       end
    metric routes: ┌────┼──────────┼────────────┼─────────┼──────
    hook_framing   ████████                                       ← 3s hold
    music_mood            ██████████                              ← 15s hold
    caption_style         ███████████████████                     ← 3s+15s
    pan_zoom                         ██████████████████           ← full
    outro_cta                                     ██████████      ← completion
    overall_subst                                          ██████ ← sends/reach

Each dimension has natural attribution to a specific retention-curve
segment. Ideally we'd compute per-dimension rewards from the fetched
retention metrics (plays_past_3s, plays_past_15s, completion,
sends_per_reach). PR 5 ships the routing plumbing using the scalar
composite reward for ALL dimensions; PR 6 will add the retention-
window fetches and this module's per-dimension reward computation
will start reading them.

The plumbing-first, discriminative-second staging lets PR 5 ship
independently — even with scalar-only attribution, the bandit still
learns "which arm combinations correlate with high overall reward,"
just with less discriminative signal than per-dim attribution would
give.

Related sprint context:
- Migration a7v8w9x0y1z2 — pending_feedback.arm_ids_by_dimension JSONB
- PR 3 (#667) — registration script populates ~256 arms
- PR 4 (#668) — selector writes arm_ids_by_dim at publish time
- PR 6 (upcoming) — retention metric fetches enabling per-dim attribution
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


# When PR 6 lands, these lambdas will replace the scalar path with
# retention-window-specific extractors. Signature preserved so this
# module's public API stays stable.
_DIMENSION_TO_METRIC_FN: dict[str, str] = {
    "hook_framing": "hold_3s",  # 3-second hold rate
    "music_mood": "hold_15s",  # 15-second hold rate
    "caption_style": "hold_avg_3s_15s",  # blended 3s + 15s
    "caption_pacing": "hold_15s",
    "caption_emphasis_color": "engagement_quality",  # comments/likes signal
    "intro_animation": "hold_3s",
    "outro_cta": "completion",  # sends_per_reach proxy
    "pan_zoom": "completion",  # full-video retention
    "highlight_moment": "hold_avg_3s_15s",
    "audio_ducking": "saved_rate",  # audio quality proxy
    "music_visual_sync": "completion",
}
"""Maps dimension name → the metric key that best attributes to that
dimension. PR 5 IGNORES this — all dims get the scalar composite
reward. PR 6 will start reading the specific metric from `metrics`
and computing per-dim rewards. Documented here to fix the mapping
early so the eventual wire is unambiguous."""


def compute_dimension_reward(
    dimension: str,
    scalar_reward: float,
    metrics: dict[str, Any] | None = None,
) -> float:
    """Compute the reward signal for a specific transformation dimension.

    PR 5 posture: returns scalar_reward as-is for every dimension. The
    metrics dict is accepted (for API stability) but not consumed.

    PR 6 will replace this stub with per-dimension retention-window
    extraction using ``_DIMENSION_TO_METRIC_FN``.

    Args:
        dimension: e.g. 'music_mood', 'caption_style'.
        scalar_reward: the composite reward computed by RewardShaper.
        metrics: fetched platform metrics dict (ignored in PR 5).

    Returns:
        Reward in [0, 1] to update the arm's Beta posterior with.
    """
    # PR 5: scalar-only. PR 6 will branch on dimension + read from metrics.
    # Keep dimension arg so the API stays stable across the transition.
    _ = dimension, metrics
    return scalar_reward


def route_transformation_rewards(
    *,
    niche_id: str,
    platform: str,
    arm_ids_by_dimension: dict[str, str],
    scalar_reward: float,
    bandit_updater: Callable[[str, str, str, float, Any], None],
    bandit_context: Any = None,
    metrics: dict[str, Any] | None = None,
) -> int:
    """Route the composite reward to N transformation-arm posteriors.

    Iterates the ``arm_ids_by_dimension`` dict from pending_feedback
    and updates each arm's posterior via the provided ``bandit_updater``.

    Fail-open per arm — a single failed update NEVER blocks the rest.
    The overall bandit update (content-type arm) has already succeeded
    at the call site; transformation arm failures are secondary.

    Args:
        niche_id: e.g. 'gaming'
        platform: e.g. 'instagram' — passed to bandit_updater for the
            per-platform split.
        arm_ids_by_dimension: {dimension_name: arm_id, ...} — the
            payload written by TransformationChoices at publish time.
        scalar_reward: reward computed by RewardShaper.compute_reward()
            for this reel at this collection window. Currently applied
            to every dim; PR 6 will differentiate per-dim.
        bandit_updater: callback matching metric_collector's
            BanditUpdater protocol (niche, arm, platform, reward, ctx).
        bandit_context: LinUCB context for the arm update. Same across
            all dims — the context is per-reel, not per-arm.
        metrics: platform metrics dict, ignored in PR 5.

    Returns:
        Number of arm posteriors successfully updated.
    """
    if not arm_ids_by_dimension:
        return 0

    updated = 0
    for dimension, arm_id in arm_ids_by_dimension.items():
        if not arm_id:
            continue
        dim_reward = compute_dimension_reward(
            dimension, scalar_reward, metrics=metrics
        )
        try:
            bandit_updater(
                niche_id, arm_id, platform, dim_reward, bandit_context
            )
            updated += 1
            logger.debug(
                "[transform_reward_router] %s/%s/%s: reward=%.3f",
                niche_id, dimension, arm_id, dim_reward,
            )
        except Exception as exc:
            # Fail-OPEN. Same policy as bandit_platform_split — a single
            # dim update failure NEVER unwinds the content-type update
            # that already succeeded.
            logger.warning(
                "[transform_reward_router] update failed for "
                "%s/%s/%s: %s",
                niche_id, dimension, arm_id, exc,
            )

    logger.info(
        "[transform_reward_router] %s/%s: updated %d/%d arms (reward=%.3f)",
        niche_id,
        platform,
        updated,
        len(arm_ids_by_dimension),
        scalar_reward,
    )
    return updated


__all__ = [
    "compute_dimension_reward",
    "route_transformation_rewards",
]
