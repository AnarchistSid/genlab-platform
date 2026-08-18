"""Feature-flag audit logger.

## Why this exists

`GENLAB_*_ENABLED` env-flag flips only take effect when a pipeline
process reads the fresh env. Systemd timers spawn short-lived
processes that get fresh env every fire, but there's currently no
positive confirmation the pipeline actually saw the flip:

  * Operator flips flag in `/opt/genlab/.env`
  * Waits for next pipeline fire
  * Feature-specific log lines eventually appear (e.g., `LLM_STEER
    music_mood`) — but ONLY when the flag-gated codepath actually
    executes
  * If the codepath doesn't fire for some other reason (no
    blueprint eligible, etc.), operator can't tell whether the
    flag propagated or the codepath just didn't trigger

This module logs a structured summary of ALL known GENLAB_*_ENABLED
flags at pipeline entry so the operator sees positive confirmation
of env state independent of whether feature codepaths fire.

## Scope

* Reads a curated allowlist of flag names (this module's
  `_KNOWN_FLAGS` constant)
* NOT auto-discovery via grep — that'd be fragile (misses new
  flags, catches old removed ones)
* Wire at pipeline runner startup + publisher startup + any other
  long-run entry point where env-state visibility is valuable

## Log format

    [flag_audit] context=pipeline_ai active=3/8 flags=[GENLAB_INTELLIGENT_TRANSFORM_ENABLED, GENLAB_YT_SHORTS_SEO_ENABLED, GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED]

Operator grep:

    journalctl -u genlab-* --since '2h ago' | grep flag_audit

## Not what this does

  * Doesn't validate that flags LEAD to actual codepath execution —
    that's the feature's own responsibility (see the LLM_STEER,
    yt_shorts_seo, etc. logs)
  * Doesn't audit non-boolean flags like GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT
    — those have their own natural log surfaces (bucket log lines)
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

_KNOWN_FLAGS: Final[tuple[str, ...]] = (
    # Tonight's organic-growth arc (2026-08-12)
    "GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED",
    "GENLAB_YT_SHORTS_SEO_ENABLED",
    "GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED",
    "GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED",
    "GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED",
    "GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED",
    "GENLAB_TRENDING_AUDIO_META_ENABLED",
    "GENLAB_FIRST_FRAME_VALIDATOR_ENABLED",
    # Core intelligence stack (pre-tonight)
    "GENLAB_INTELLIGENT_TRANSFORM_ENABLED",
    "GENLAB_LINUCB_PICK_ENABLED",
    "GENLAB_LINUCB_STOCHASTIC_ENABLED",
    "GENLAB_THOMPSON_PROPENSITY_ENABLED",
    "GENLAB_ENSEMBLE_DECISION_ENABLED",
    "GENLAB_ENSEMBLE_PERSIST_ENABLED",
    "GENLAB_CROSS_NICHE_TRANSFER_ENABLED",
    "GENLAB_TREND_ANTICIPATION_ENABLED",
    "GENLAB_COUNTERFACTUAL_REPLAY_ENABLED",
    "GENLAB_TEMPORAL_CONTEXT_ENABLED",
    "GENLAB_TOP_CREATOR_PRIORS_ENABLED",
    "GENLAB_STRATEGIST_INTEGRATION_ENABLED",
    "GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED",
    # Quality gates + judge stack
    "GENLAB_VISION_JUDGE_ENABLED",
    "GENLAB_RENDER_QC_ENABLED",
    "GENLAB_HOOK_CLASSIFIER_LLM_ENABLED",
    "GENLAB_HOOK_CRITIC_ENABLED",
    "GENLAB_HOOK_REWRITER_ENABLED",
    "GENLAB_HOOK_DIVERSITY_ENABLED",
    "GENLAB_LLM_JUDGE_ENABLED",
    "GENLAB_REPLY_CRITIC_ENABLED",
    "GENLAB_BAYESIAN_GATE_ENABLED",
    "GENLAB_CROSS_PLATFORM_GATE_ENABLED",
    "GENLAB_CONFORMAL_ROUTER_ENABLED",
    "GENLAB_SHADOW_REVIEWER_ENABLED",
    "GENLAB_POLICY_BLOCK_RCA_ENABLED",
    "GENLAB_POST_RCA_ENABLED",
    "GENLAB_VIDEO_ANALYSIS_ENABLED",
    "GENLAB_VIDEO_NICHE_FIT_ENABLED",
    # Reward + experimentation
    "GENLAB_MULTI_WINDOW_REWARD_ENABLED",
    "GENLAB_RATIONALE_CLASSIFIER_ENABLED",
    "GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED",
    "GENLAB_DRIFT_PERSIST_ENABLED",
    "GENLAB_EXPERIMENTATION_ENABLED",
    "GENLAB_AUTO_EXPERIMENT_ENABLED",
    "GENLAB_OPTIMAL_TIME_BANDIT_ENABLED",
    "GENLAB_PER_PLATFORM_BANDIT_ENABLED",
    "GENLAB_PRODUCT_SELECTOR_ENABLED",
    # Compositor variants
    "GENLAB_SPLIT_SCREEN_COMPOSITOR_ENABLED",
    "GENLAB_STORYTIME_COMPOSITOR_ENABLED",
    # Anticipation signals
    "GENLAB_ANTICIPATION_ACCURACY_ENABLED",
    "GENLAB_ANTICIPATION_NEWS_ENABLED",
    "GENLAB_ANTICIPATION_REDDIT_ENABLED",
    "GENLAB_ANTICIPATION_YT_ENABLED",
    # LLM infra
    "GENLAB_LLM_FALLBACK_ENABLED",
    # 2026-08-18 inference.sh integrations (tonight)
    "GENLAB_INFSH_TTS_ENABLED",
    "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED",
    "GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED",
)


# Canary-list flags — value is a comma-separated niche list or 'all'
# rather than a boolean. Reported separately with their value so
# operators can see which niches are canary-active.
_CANARY_FLAGS: Final[tuple[str, ...]] = (
    "GENLAB_HOOK_THUMBNAIL_NICHES",
    "GENLAB_CHART_BROLL_NICHES",
    "GENLAB_ANIME_BACKFILL_NICHES",
    "GENLAB_PERSONA_HINT_NICHES",
    "GENLAB_CROSS_CHANNEL_FOOTER_NICHES",
    "GENLAB_IG_DISCOVERY_TAGS_NICHES",
    "GENLAB_THREADS_HASHTAG_AUGMENT_NICHES",
)


def _is_flag_on(name: str) -> bool:
    """Match the `env_true` truthiness semantics from settings.py."""
    val = os.environ.get(name, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _canary_flag_value(name: str) -> str | None:
    """Return the non-empty value of a canary-list flag, or None
    when unset / off. Off tokens match the is_enabled_for semantics
    shared across hook_thumbnail / chart_broll / pruna_video_client."""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return None
    return raw


def log_active_flags(*, context: str) -> None:
    """Emit a structured INFO log summarising GENLAB_*_ENABLED state.

    Args:
        context: short identifier for where this fires from —
            e.g., "pipeline_ai", "publisher", "engagement_poller".
            Included in the log line so operator can grep per-context.

    Zero raises. Zero side effects beyond the log line.
    """
    try:
        active = [name for name in _KNOWN_FLAGS if _is_flag_on(name)]
        logger.info(
            "[flag_audit] context=%s active=%d/%d flags=%s",
            context,
            len(active),
            len(_KNOWN_FLAGS),
            active,
        )
        # Separately report canary-list flags (value-carrying, not boolean).
        # Emit only when at least one is active so we don't spam a second
        # empty log line every fire.
        canaries = {
            name: _canary_flag_value(name)
            for name in _CANARY_FLAGS
            if _canary_flag_value(name)
        }
        if canaries:
            logger.info(
                "[flag_audit] context=%s canaries=%d/%d values=%s",
                context,
                len(canaries),
                len(_CANARY_FLAGS),
                canaries,
            )
    except Exception as exc:  # noqa: BLE001
        # Never break the caller for observability
        logger.debug("[flag_audit] emit failed context=%s: %s", context, exc)


__all__ = [
    "log_active_flags",
    "_KNOWN_FLAGS",  # exported for tests + operator visibility
    "_CANARY_FLAGS",
]
