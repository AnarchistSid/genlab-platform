"""Monetisation-aware bandit reward computation.

The core insight: a reward formula that treats all engagement equally
is correct in steady state but suboptimal when approaching monetisation
thresholds. When a YouTube channel is at 800/1,000 subscribers, content
that drives watch hours is 3x more valuable than content that only
generates likes.

The reward shaper adjusts weights when within 20% of a threshold.
This creates a feedback loop where the bandit naturally learns to
optimise for the metrics that matter most for the current monetisation
stage.

Thresholds (verified against platform docs, March 2026):
  YouTube:   1,000 subs + 4,000 watch hours OR 10M Shorts views/90d
  TikTok:    10K followers + 100K views/30d
  Facebook:  5K followers + 600K minutes viewed/60d
  Instagram: Invite-only (no fixed threshold) — DM send rate always highest
  X/Twitter: Premium + 5M impressions/3mo
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class MonetisationThreshold:
    """Threshold definition for one platform metric."""

    platform: str
    metric: str  # Channel-level metric to track toward threshold
    target: float  # The threshold value
    boost_metric: str  # Which reward metric to upweight
    boost_factor: float  # Multiplier when within proximity of threshold


# Canonical thresholds. Update when platforms change policies.
MONETISATION_THRESHOLDS: list[MonetisationThreshold] = [
    MonetisationThreshold(
        platform="youtube",
        metric="watch_hours_total",
        target=4000.0,
        boost_metric="avg_view_duration",
        boost_factor=3.0,
    ),
    MonetisationThreshold(
        platform="youtube",
        metric="subscriber_count",
        target=1000.0,
        boost_metric="subscriber_gained",
        boost_factor=2.0,
    ),
    MonetisationThreshold(
        platform="tiktok",
        metric="follower_count",
        target=10_000.0,
        boost_metric="share_rate",
        boost_factor=2.0,
    ),
    MonetisationThreshold(
        platform="facebook",
        metric="minutes_viewed_60d",
        target=600_000.0,
        boost_metric="completion_rate",
        boost_factor=3.0,
    ),
    MonetisationThreshold(
        platform="twitter",
        metric="impressions_3mo",
        target=5_000_000.0,
        boost_metric="reply_chain_rate",
        boost_factor=4.0,
    ),
]

# Base per-post reward weights by platform (used when not near any threshold).
BASE_WEIGHTS: dict[str, dict[str, float]] = {
    "youtube": {
        "views": 0.3,
        "avg_view_duration": 0.3,
        "subscriber_gained": 0.2,
        "like_rate": 0.1,
        "comment_rate": 0.1,
    },
    "instagram": {
        # 2026-07-17 (Layer 4 foundational): added `follower_gained`
        # at 0.15 weight. Prior state: NO follower-growth signal on
        # IG at all — the bandit optimized purely for engagement
        # (views/saves/dm/shares) with no reward for actually growing
        # the audience. Wired by learning/metrics/follower_delta.py
        # augment call at 168h reward-collection window. Other weights
        # reduced proportionally to preserve sum ≈ 1.0.
        "views": 0.15,
        "saves": 0.25,
        "dm_send_rate": 0.25,
        "shares": 0.15,
        "follower_gained": 0.15,
        "skip_rate": -0.05,
    },
    "tiktok": {
        "views": 0.25,
        "avg_watch_time": 0.30,
        "share_rate": 0.25,
        "follower_gained": 0.20,
    },
    "facebook": {
        # 2026-07-17 (Layer 4 foundational): added `follower_gained`
        # at 0.15 weight (same rationale as instagram above). Facebook's
        # legacy weights had ZERO follower-growth signal even though FB
        # is the highest-follower niche for ai_creators (10K legacy) +
        # movies (8.7K).
        "minutes_viewed": 0.30,
        "shares": 0.25,
        "completion_rate": 0.20,
        "follower_gained": 0.15,
        "reach": 0.10,
    },
    "twitter": {
        "impressions": 0.20,
        "reply_chain_rate": 0.50,
        "engagements": 0.20,
        "profile_clicks": 0.10,
    },
    "x": {
        "impressions": 0.20,
        "reply_chain_rate": 0.50,
        "engagements": 0.20,
        "profile_clicks": 0.10,
    },
    "threads": {
        # 2026-07-17 (Layer 4 foundational): added `follower_gained`
        # at 0.15 weight. Threads is one of the 4 in-scope platforms
        # (2026-07-17 operator directive) — needs follower growth
        # signal like IG and FB. Other weights reduced proportionally.
        "views": 0.25,
        "replies": 0.25,
        "reposts": 0.20,
        "follower_gained": 0.15,
        "discovery_share": 0.15,
    },
}

# "Good" metric targets — scaled for early-stage channels (<5K followers).
# These will be dynamically adjusted as channels grow.
# Original targets (for mature channels with 50K+ followers) were 10-100x higher
# and produced near-zero rewards for new channels (Break 4 fix).
_METRIC_TARGETS: dict[str, dict[str, float]] = {
    "views": {
        "youtube": 200,
        "instagram": 500,
        "tiktok": 5000,
        "facebook": 300,
        "twitter": 500,
        "x": 500,
    },
    "avg_view_duration": {"youtube": 30},
    "saves": {"instagram": 15},
    "dm_send_rate": {"instagram": 0.02},
    "share_rate": {"tiktok": 0.01},
    "reply_chain_rate": {"twitter": 0.005, "x": 0.005},
    "completion_rate": {"facebook": 0.3},
    "skip_rate": {"instagram": 0.4},
    # 2026-07-17 (Layer 4 foundational): early-stage targets. New IG/YT/FB/
    # Threads channels typically gain 0-2 followers per post; setting target
    # low (2-5) means normalized reward saturates at ~1.0 when a post
    # genuinely drives 2-5 new followers — a meaningful early-stage signal.
    # As channels grow past 5K followers, retune upward (10-50 range).
    "subscriber_gained": {"youtube": 2},
    "follower_gained": {
        "tiktok": 10,
        "instagram": 3,
        "facebook": 3,
        "threads": 2,
    },
    "avg_watch_time": {"tiktok": 15},
    "minutes_viewed": {"facebook": 50},
    "shares": {"instagram": 5, "facebook": 3},
    "impressions": {"twitter": 500, "x": 500},
    "engagements": {"twitter": 20, "x": 20},
    "profile_clicks": {"twitter": 5, "x": 5},
    "like_rate": {"youtube": 0.04},
    "comment_rate": {"youtube": 0.01},
    "reach": {"facebook": 100},
    "replies": {"threads": 3},
    "reposts": {"threads": 2},
    "discovery_share": {"threads": 0.05},
}


class RewardShaper:
    """Compute monetisation-aware rewards for bandit updates.

    Usage:
        shaper = RewardShaper(
            channel_metrics_fn=get_channel_metrics,  # takes (niche_id, platform)
            niche_id="ai_creators",
        )
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 10000, "avg_view_duration": 45.0},
        )
        bandit.update(arm=selected_arm, reward=reward)

    Args:
        channel_metrics_fn: Callable that takes ``(niche_id, platform)`` and
            returns a dict of channel-level metrics (subscriber_count,
            watch_hours, etc.). If None, no threshold boosting is applied
            (base weights only).

            2026-07-14: signature changed from single-arg ``(platform)`` to
            two-arg ``(niche_id, platform)`` to match the prod
            ``get_channel_metrics`` function, which requires both to query
            the ``monetisationprogress`` table (rows are keyed by both).
            Prior to this fix, injecting the real fn produced
            ``TypeError: get_channel_metrics() missing 1 required
            positional argument: 'platform'`` on every reward compute —
            caught + logged as WARNING, silently disabling the
            monetisation-threshold boost across all niches.
        percentile_targets_fn: Callable that takes (niche_id, platform, metric)
            and returns the 70th-percentile value of the most-recent N posts
            for that pair, or None to skip percentile-relative normalisation.
            Enables fix #6 of the autonomy roadmap: instead of hardcoded
            absolute targets (e.g. YT views=200), the target self-calibrates
            so the top 30% of recent posts always yield reward ≥ 0.7. This
            unblocks the bandit on channels where most posts get
            <hardcoded-target views and the static normaliser was producing
            avg_reward ≈ 0.06 even on signal-rich data. Falls back to
            the hardcoded _METRIC_TARGETS when the fn returns None or
            during cold start.
        niche_id: Niche identifier used by percentile_targets_fn lookups
            AND (as of 2026-07-14) passed to channel_metrics_fn for the
            monetisation-threshold boost lookup.
    """

    THRESHOLD_PROXIMITY = 0.20  # Within 20% triggers boost
    PERCENTILE_FOR_TARGET = 0.70  # 70th percentile = reward ~0.7 floor on top 30%

    def __init__(
        self,
        channel_metrics_fn: Callable[[str, str], dict[str, float]] | None = None,
        percentile_targets_fn: Callable[[str, str, str], float | None] | None = None,
        niche_id: str = "",
    ) -> None:
        self._channel_metrics_fn = channel_metrics_fn
        self._percentile_targets_fn = percentile_targets_fn
        self._niche_id = niche_id

    def get_adjusted_weights(
        self,
        platform: str,
        channel_metrics: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Compute reward weights adjusted for threshold proximity.

        When a channel metric is within 20% of its monetisation threshold,
        the boost_metric gets its weight multiplied by boost_factor.
        Weights are re-normalised afterward so they sum to 1.0.
        """
        weights = dict(BASE_WEIGHTS.get(platform, {}))
        if not weights:
            return weights

        # PR Strategist-3: apply operator-accepted reward_weight overrides
        # from the latest Strategist report BEFORE monetisation-threshold
        # boosting. Overrides target format is
        # ``{niche_id}.reward_weight.{platform}.{metric}`` (matches what
        # the proposal schema documents at proposal_schema.Proposal.target).
        # Fail-closed: any error → skip overrides, use BASE_WEIGHTS as-is.
        # The proposal-level clamp (0.0 ≤ value ≤ reasonable bounds) is
        # trusted from strategy_phase._load_phase_config; we don't
        # re-validate here to keep this hot path fast.
        if self._niche_id:
            try:
                from genlab_core.scheduling.strategy_phase import get_phase_config

                phase_cfg = get_phase_config(self._niche_id)
                for target, value in phase_cfg.reward_weight_overrides.items():
                    # Only apply overrides matching (this niche, this platform)
                    prefix = f"{self._niche_id}.reward_weight.{platform}."
                    if not target.startswith(prefix):
                        continue
                    metric = target[len(prefix) :]
                    if metric in weights and 0.0 <= value <= 5.0:
                        old = weights[metric]
                        weights[metric] = value
                        logger.info(
                            "[REWARD] Strategist override %s/%s %s: %.3f -> %.3f",
                            self._niche_id,
                            platform,
                            metric,
                            old,
                            value,
                        )
            except Exception as exc:
                logger.debug("[REWARD] Strategist override skipped: %s", exc)

        if channel_metrics is None and self._channel_metrics_fn is not None:
            try:
                # 2026-07-14: pass (niche_id, platform) — prod
                # get_channel_metrics requires both to query
                # monetisationprogress rows (keyed by both). Empty
                # niche_id returns {} (harmless) — same fallback as
                # the pre-fix silent-failure path.
                channel_metrics = self._channel_metrics_fn(self._niche_id, platform)
            except Exception as e:
                logger.warning(
                    "[REWARD] Channel metrics fetch failed for %s/%s: %s",
                    self._niche_id or "<no-niche>",
                    platform,
                    e,
                )
                channel_metrics = {}

        if not channel_metrics:
            return _normalise_weights(weights)

        for threshold in MONETISATION_THRESHOLDS:
            if threshold.platform != platform:
                continue

            current = channel_metrics.get(threshold.metric, 0.0)
            if threshold.target <= 0:
                continue

            proximity = current / threshold.target

            if proximity >= (1 - self.THRESHOLD_PROXIMITY):
                boost_metric = threshold.boost_metric
                if boost_metric in weights:
                    old = weights[boost_metric]
                    weights[boost_metric] = old * threshold.boost_factor
                    logger.info(
                        "[REWARD] %s/%s: %.0f%% to threshold -> %s weight %.2f->%.2f",
                        platform,
                        threshold.metric,
                        proximity * 100,
                        boost_metric,
                        old,
                        weights[boost_metric],
                    )

        return _normalise_weights(weights)

    def compute_reward(
        self,
        platform: str,
        metrics: dict[str, float],
        channel_metrics: dict[str, float] | None = None,
    ) -> float | None:
        """Compute a [0, 1] reward value for a bandit update.

        Args:
            platform: Platform the post was published on.
            metrics: Per-post metrics (views, saves, etc.).
            channel_metrics: Channel-level metrics for threshold proximity.
                If None and channel_metrics_fn was provided, it's called automatically.

        Returns:
            Reward in [0, 1] on success. ``None`` when the shaper's
            monetisation-boost component raises internally (see
            ``MonetisationRewardShaper.compute``) — 2026-07-14 change,
            preferable to the prior 0.0 fallback which poisoned the
            bandit posterior with synthetic zeros. All external
            callers MUST null-check the return before feeding it to
            downstream float ops. Verified pin: ``late_reward.py:252``,
            ``metric_collector.py:1033``, and (2026-07-14)
            ``backfill_bandit_from_history.py:300``.
        """
        weights = self.get_adjusted_weights(platform, channel_metrics)

        # Redistribute weight for metrics the fetcher couldn't produce.
        # Some keys (IG dm_send_rate/skip_rate, Twitter profile_clicks,
        # Threads discovery_share) require API tiers or webhook data we
        # don't have. Rather than pinning those slices to 0 (which makes
        # the bandit reward systematically max out below the theoretical
        # 1.0 ceiling), reallocate their share to observed metrics so
        # the reward fully describes the metrics we DO see. Negative
        # weights (e.g. IG skip_rate's -0.05 penalty) are preserved
        # only when the metric is present so the reweight stays bounded
        # to [0, 1].
        #
        # Blind Spot #2 telemetry (2026-07-01): make the redistribution
        # observable. Silent 30% shifts (e.g. IG dm_send unavailable →
        # 30% of weight shifts to saves/shares) inflate reward signals
        # for the still-present metrics without any operator visibility.
        # We log at DEBUG for every non-trivial redistribution and at
        # WARNING when the dropped share crosses 15% of total weight —
        # the threshold picked because IG's typical unreachable-DM
        # slice is 30% (well above), and small drops from any single
        # platform slice sit well below.
        total_abs_weight = sum(abs(w) for w in weights.values())
        present_weights = {k: w for k, w in weights.items() if k in metrics}
        weight_sum = sum(abs(w) for w in present_weights.values())
        if weight_sum > 0 and total_abs_weight > 0:
            dropped_keys = sorted(k for k in weights.keys() if k not in metrics)
            dropped_abs = total_abs_weight - weight_sum
            dropped_pct = dropped_abs / total_abs_weight
            scale = total_abs_weight / weight_sum
            weights = {k: w * scale for k, w in present_weights.items()}
            if dropped_pct >= 0.15:
                logger.warning(
                    "[REWARD] weight redistribution on %s: dropped %.0f%% of "
                    "weight from %d missing metrics %s → scale=%.2f× applied to "
                    "%d present metrics. This inflates the present-metric "
                    "signals; if the missing metrics are systematically absent "
                    "for this platform (e.g. IG dm_send without webhook), the "
                    "bandit posterior is being trained on partial evidence.",
                    platform,
                    dropped_pct * 100,
                    len(dropped_keys),
                    dropped_keys,
                    scale,
                    len(present_weights),
                )
            elif dropped_pct > 0:
                logger.debug(
                    "[REWARD] weight redistribution on %s: dropped %.0f%% (%d keys) → scale=%.2f×",
                    platform,
                    dropped_pct * 100,
                    len(dropped_keys),
                    scale,
                )

        raw_reward = 0.0
        for metric, weight in weights.items():
            value = metrics.get(metric, 0.0)
            # Fix #6 of the autonomy roadmap: try percentile-relative target
            # first; fall back to the hardcoded _METRIC_TARGETS during cold
            # start (n < 20 observations) or when no fn was injected.
            normalised = self._normalise_with_percentile(metric, value, platform)
            raw_reward += weight * normalised

        # Monetization bonus: affiliate clicks boost reward (Break 13 fix)
        affiliate_clicks = float(metrics.get("affiliate_clicks", 0))
        if affiliate_clicks > 0:
            # Each click is worth a 0.05 reward bonus, capped at 0.3
            monetization_bonus = min(0.3, affiliate_clicks * 0.05)
            raw_reward += monetization_bonus
            logger.debug(
                "[REWARD] Monetization bonus: %d clicks → +%.2f",
                int(affiliate_clicks),
                monetization_bonus,
            )

        return max(0.0, min(1.0, raw_reward))

    @staticmethod
    def apply_rationale_multiplier(
        base_reward: float,
        rationale: str | None,
        arm_kind: str,
    ) -> float:
        """Wire to per-rationale reward multipliers (PR AA).

        Thin re-export of :func:`genlab_core.learning.rationale_weighted_reward.apply_rationale_multiplier`
        so callers that already hold a ``RewardShaper`` instance can
        access the rationale wire without an extra import. Default-OFF
        until ``GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED=1`` is set.

        Reads category → multiplier mapping from
        ``genlab-core/config/learning_rationale_weights.yaml``. See the
        module docstring for the design contract.
        """
        from genlab_core.learning.rationale_weighted_reward import (
            apply_rationale_multiplier,
        )

        return apply_rationale_multiplier(base_reward, rationale, arm_kind)

    def _normalise_with_percentile(self, metric: str, value: float, platform: str) -> float:
        """Normalise ``value`` to [0, 1] using percentile-relative target
        when available, otherwise the hardcoded fallback.

        The percentile target is the 70th-percentile of recent observations
        for (niche_id × platform × metric). When the bandit feedback chain
        is healthy, this self-calibrates: a niche where YT posts cluster
        around 50 views still gets meaningful reward gradient (above-50
        = reward > 0.7, below-50 = reward < 0.7). Without this, the
        hardcoded YT views=200 target produces reward<0.3 for everything
        in a niche where the actual distribution centres at 50.

        Falls back to _normalise_metric when:
          - No fn was injected (caller didn't opt in)
          - Fn returns None (cold start, insufficient observations)
          - Fn raises (defensively wrapped — never block reward compute)
        """
        if self._percentile_targets_fn is None or not self._niche_id:
            return _normalise_metric(metric, value, platform)
        try:
            target = self._percentile_targets_fn(self._niche_id, platform, metric)
        except Exception as exc:
            logger.debug(
                "[REWARD] percentile_targets_fn raised for %s/%s/%s: %s; using hardcoded",
                self._niche_id,
                platform,
                metric,
                exc,
            )
            return _normalise_metric(metric, value, platform)
        if target is None or target <= 0:
            return _normalise_metric(metric, value, platform)
        return min(1.0, value / target)


def _normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    """Re-normalise weights so absolute values sum to 1.0."""
    total = sum(abs(w) for w in weights.values())
    if total == 0:
        return weights
    return {k: v / total for k, v in weights.items()}


def _normalise_metric(metric: str, value: float, platform: str) -> float:
    """Normalise a raw metric value to [0, 1] using a simple ramp."""
    targets = _METRIC_TARGETS.get(metric, {})
    target = targets.get(platform, 1000.0)
    if target == 0:
        return 0.0
    return min(1.0, value / target)


# ---------------------------------------------------------------------------
# Monetisation-aware reward shaper with threshold proximity boosting
# ---------------------------------------------------------------------------

# Maps primary_metric to (raw_metric_key, monetisation_yaml_key)
_THRESHOLD_KEYS: dict[str, tuple[str | None, str | None]] = {
    "watch_hours": ("watch_time_hours", "threshold_watch_hours"),
    "impressions": ("impressions", "threshold_impressions_90d"),
    "minutes_viewed": ("minutes_viewed", "threshold_minutes_viewed_60d"),
    "engagement_rate": (None, None),  # no fixed threshold for IG/Threads
    "views": ("views", "threshold_views_30d"),
}

# Per-platform primary metrics for threshold lookup
_PLATFORM_PRIMARY_METRICS: dict[str, str] = {
    "youtube": "watch_hours",
    "instagram": "engagement_rate",
    "x_twitter": "impressions",
    "twitter": "impressions",
    "facebook": "minutes_viewed",
    "threads": "engagement_rate",
    "tiktok": "views",
}


class MonetisationRewardShaper:
    """Compute rewards with monetisation proximity boosting.

    .. deprecated::
        This class is not used in production. The simpler ``RewardShaper``
        above handles threshold proximity via ``MonetisationMultiplierProvider``.
        Kept for reference — remove if not adopted by Sprint 72.

    The core insight: reward the bandit not just for engagement, but for
    PROGRESS TOWARD MONETISATION THRESHOLDS. The same 1,000 views means
    different things depending on whether your YouTube channel has 200 or
    980 subscribers.

    Reward formula:
      base_reward = weighted_sum(completion_rate, engagement_rate, shares)
      threshold_multiplier = 1.0 + (threshold_bonus * proximity_factor)
      final_reward = normalise(base_reward * threshold_multiplier)

    Where:
      proximity_factor = max(0, (current_pct - trigger_pct) / (1.0 - trigger_pct))
      threshold_bonus = 0.15  (configurable)
      trigger_pct = 0.80      (bonus activates when 80% of threshold reached)
    """

    def __init__(
        self,
        config: dict,
        niche_id: str,
        monetisation_config: dict,
        multiplier_provider: MonetisationMultiplierProvider | None = None,
    ) -> None:
        self._config = config
        self._niche_id = niche_id
        self._monetisation = monetisation_config
        self._multiplier_provider = multiplier_provider
        # Reuse WelfordNormalizer from Day 5 — no percentile() available,
        # so we use normalise() which maps to [0, 1] via z-score.
        from genlab_core.intelligence.score_normalizer import WelfordNormalizer

        self._normalizers: dict[str, WelfordNormalizer] = {}

    @classmethod
    def from_config(cls, niche_id: str) -> MonetisationRewardShaper:
        """Load scoring.yaml and monetisation.yaml from AGENT_ROOT."""
        import yaml

        from genlab_core.utils.env import get_agent_root

        root = get_agent_root()

        scoring_path = root / "config" / "scoring.yaml"
        config = {}
        if scoring_path.exists():
            with open(scoring_path) as f:
                config = yaml.safe_load(f) or {}
        else:
            logger.warning("scoring.yaml not found at %s — using defaults", scoring_path)

        monetisation_path = root / "config" / "monetisation.yaml"
        monetisation_config = {}
        if monetisation_path.exists():
            with open(monetisation_path) as f:
                monetisation_config = yaml.safe_load(f) or {}
        else:
            logger.warning(
                "monetisation.yaml not found at %s — threshold proximity boost will be 0.0",
                monetisation_path,
            )

        return cls(config=config, niche_id=niche_id, monetisation_config=monetisation_config)

    def compute(self, metrics: dict, platform: str, window: str) -> float:
        """Compute a shaped reward in [0, 1] from raw platform metrics.

        Returns None on any error — never raises. The [0, 1] clamp
        matches the base RewardShaper.compute_reward invariant so the
        bandit's Beta posterior stays well-defined.
        """
        try:
            reward_config = self._config.get("reward", {})
            weights = reward_config.get(
                "weights",
                {
                    "completion_rate": 0.40,
                    "engagement": 0.35,
                    "shares": 0.25,
                },
            )

            # Share normalisation scale from config with safe fallback matching
            # the previous hardcoded value exactly (zero behaviour change).
            # share_rate (~0.01-0.05 raw) * scale → [0, 1] range.
            bandit_blend = reward_config.get("bandit_blend", {})
            share_scale = float(bandit_blend.get("share_rate_scale", 10))

            completion = float(metrics.get("completion_rate", 0.0))
            engagement = float(metrics.get("engagement_rate", 0.0))
            views = max(1, int(metrics.get("views", 1)))
            shares = float(metrics.get("shares", 0)) / views

            base = (
                weights.get("completion_rate", 0.40) * min(1.0, completion)
                + weights.get("engagement", 0.35) * min(1.0, engagement)
                + weights.get("shares", 0.25) * min(1.0, shares * share_scale)
            )

            boost = self._compute_threshold_boost(metrics, platform)
            shaped = base * (1.0 + boost)
            normalised = self._normalise(shaped, platform)

            # 2026-07-14: final clamp to [0, 1]. Docstring said "[0, ~1.5]"
            # but the base class holds a strict clamp invariant at
            # compute_reward:426; sibling contract must match. Without
            # this, a Monetisation-shaped reward > 1.0 reaches the
            # bandit and skews its posterior against the [0, 1] Beta
            # distribution assumption. WelfordNormalizer's clip=True
            # allows some > 1 slippage under welford_zscore mode.
            clamped = max(0.0, min(1.0, normalised))

            logger.debug(
                "RewardShaper: platform=%s window=%s base=%.4f boost=%.3f "
                "shaped=%.4f normalised=%.4f clamped=%.4f",
                platform,
                window,
                base,
                boost,
                shaped,
                normalised,
                clamped,
            )
            return clamped

        except Exception as e:
            # 2026-07-14 class-of-bug fix: return None on exception
            # (not 0.0). Prior return-0.0 semantics were the exact
            # anti-pattern from [[class-of-bug-metric-proxies-mask-
            # audience-facing-failures]]: silent 0.0 is
            # indistinguishable from a real "post got 0 views" reward.
            # The bandit's α/β update then trained on garbage — every
            # exception got attributed as bad content, not fetch/compute
            # failure.
            #
            # Elevated to WARNING (was ERROR — good, but the sentinel
            # 0.0 defeated observability anyway). Callers must handle
            # None: metric_collector's caller wraps this in a
            # ``if reward_48h is not None`` guard before bandit update.
            logger.warning(
                "RewardShaper: reward computation failed on platform=%s (%s) "
                "— returning None so bandit skips this observation instead of "
                "training on synthetic 0.0",
                platform,
                e,
            )
            return None  # type: ignore[return-value]

    def _compute_threshold_boost(self, metrics: dict, platform: str) -> float:
        """Compute the monetisation proximity boost.

        When a MonetisationMultiplierProvider is available, uses live
        SharePoint data from the daily tracker. Otherwise falls back
        to monetisation.yaml (loaded at init).
        """
        # Live SP path: use provider multiplier directly
        if self._multiplier_provider is not None:
            try:
                mult = self._multiplier_provider.get_multiplier(
                    self._niche_id,
                    platform,
                )
                # Convert multiplier (1.0/1.5/3.0) to a boost additive
                # 1.0 → 0.0 boost, 1.5 → 0.075 boost, 3.0 → 0.30 boost
                reward_config = self._config.get("reward", {})
                bonus = reward_config.get("threshold_proximity_bonus", 0.15)
                boost = bonus * (mult - 1.0)
                if boost > 0:
                    logger.info(
                        "[REWARD] %s/%s: live SP multiplier=%.1f -> boost=%.3f",
                        self._niche_id,
                        platform,
                        mult,
                        boost,
                    )
                return boost
            except Exception as e:
                logger.warning(
                    "[REWARD] Provider failed for %s/%s: %s — falling back to YAML",
                    self._niche_id,
                    platform,
                    e,
                )

        # Fallback: static YAML path
        reward_config = self._config.get("reward", {})
        bonus = reward_config.get("threshold_proximity_bonus", 0.15)
        trigger = reward_config.get("threshold_proximity_trigger", 0.80)

        # Guard against division by zero when trigger==1.0
        if trigger >= 1.0:
            return 0.0

        primary_metric = _PLATFORM_PRIMARY_METRICS.get(platform, "engagement_rate")
        metric_key, threshold_yaml_key = _THRESHOLD_KEYS.get(primary_metric, (None, None))
        if not metric_key or not threshold_yaml_key:
            return 0.0

        platform_config = self._monetisation.get("platforms", {}).get(platform, {})
        threshold_value = platform_config.get(threshold_yaml_key, 0)
        if not threshold_value:
            return 0.0

        current_value = float(metrics.get(metric_key, 0.0))
        current_pct = min(1.0, current_value / threshold_value)

        if current_pct < trigger:
            return 0.0

        proximity_factor = (current_pct - trigger) / (1.0 - trigger)
        return bonus * proximity_factor

    def _normalise(self, reward: float, platform: str) -> float:
        """Normalise using WelfordNormalizer.

        The existing WelfordNormalizer uses z-score normalisation (no
        percentile method). Falls back to raw reward before 10 samples.
        """
        from genlab_core.intelligence.score_normalizer import WelfordNormalizer

        key = f"{self._niche_id}_{platform}"
        if key not in self._normalizers:
            self._normalizers[key] = WelfordNormalizer()

        normalizer = self._normalizers[key]
        normalizer.update(reward)

        if normalizer.count < 10:
            return reward

        return normalizer.normalise(reward, clip=True)


# ---------------------------------------------------------------------------
# MonetisationMultiplierProvider — live SP data for threshold proximity
# ---------------------------------------------------------------------------


def _get_targets_path() -> Path:
    """Resolve path to monetisation_targets.yaml.

    Uses settings._PROJECT_ROOT (respects AGENT_ROOT env var) so this
    works under both editable installs and wheel installs where the
    source-tree relative path ``parents[3]`` would be meaningless.
    """
    from genlab_core.settings import _PROJECT_ROOT

    return _PROJECT_ROOT / "genlab-core" / "config" / "monetisation_targets.yaml"


class MonetisationMultiplierProvider:
    """Read monetisation progress from SharePoint, cached hourly.

    Provides reward multipliers based on how close each niche/platform
    combination is to its monetisation threshold. The tracker fills
    the SP list daily; this provider reads it on demand with a 1-hour
    cache to avoid per-request overhead.

    Multiplier tiers (from monetisation_targets.yaml reward_boost):
        within_20pct  → 3.0  (pct_complete >= 80)
        within_50pct  → 1.5  (pct_complete >= 50)
        above_threshold → 1.0  (pct_complete >= 100, already monetised)
        default        → 1.0  (below 50%)
    """

    _CACHE_TTL = 3600.0  # 1 hour

    def __init__(self, backlog_client: Any = None) -> None:
        self._client = backlog_client
        self._cache: dict[str, dict] = {}  # keyed by "niche_id/platform/metric_name"
        self._cache_ts: float = 0.0
        self._boost_tiers = self._load_boost_tiers()

    @staticmethod
    def _load_boost_tiers() -> dict[str, float]:
        """Load reward_boost tiers from monetisation_targets.yaml."""
        try:
            with open(_get_targets_path()) as f:
                cfg = yaml.safe_load(f) or {}
            boost = cfg.get("reward_boost", {})
            return {
                "within_20pct": float(boost.get("within_20pct", 3.0)),
                "within_50pct": float(boost.get("within_50pct", 1.5)),
                "above_threshold": float(boost.get("above_threshold", 1.0)),
            }
        except Exception as e:
            logger.warning("Failed to load reward_boost config: %s", e)
            return {"within_20pct": 3.0, "within_50pct": 1.5, "above_threshold": 1.0}

    def _ensure_cache(self) -> None:
        """Refresh cache if stale."""
        now = _time.time()
        if self._cache and (now - self._cache_ts) < self._CACHE_TTL:
            return

        try:
            client = self._client
            if client is None:
                from genlab_core.http.backlog_client import BacklogClient

                client = BacklogClient()
                self._client = client

            proxy = getattr(client, "monetisation_progress", None)
            if proxy is None:
                logger.warning(
                    "MonetisationMultiplierProvider: no monetisation_progress proxy on BacklogClient"
                )
                return

            records = proxy.all()

            new_cache: dict[str, dict] = {}
            for rec in records:
                fields = rec.get("fields", rec)
                key = (
                    f"{fields.get('niche_id')}/{fields.get('platform')}/{fields.get('metric_name')}"
                )
                new_cache[key] = fields

            self._cache = new_cache
            self._cache_ts = now
            logger.debug(
                "MonetisationMultiplierProvider: refreshed %d records",
                len(new_cache),
            )

        except Exception as e:
            logger.error("MonetisationMultiplierProvider: cache refresh failed: %s", e)

    def get_progress(
        self,
        niche_id: str,
        platform: str,
        metric_name: str,
    ) -> dict | None:
        """Get raw progress record for a specific metric."""
        self._ensure_cache()
        return self._cache.get(f"{niche_id}/{platform}/{metric_name}")

    def get_multiplier(self, niche_id: str, platform: str) -> float:
        """Get aggregate reward multiplier for a niche/platform pair.

        Takes the maximum pct_complete across all metrics for the platform
        and maps it to the corresponding boost tier.
        """
        self._ensure_cache()

        prefix = f"{niche_id}/{platform}/"
        pcts = [
            rec.get("pct_complete", 0) or 0
            for key, rec in self._cache.items()
            if key.startswith(prefix) and rec.get("target_value") is not None
        ]

        if not pcts:
            return 1.0

        max_pct = max(pcts)
        return self._pct_to_multiplier(max_pct)

    def _pct_to_multiplier(self, pct: float) -> float:
        """Map a pct_complete value to a reward multiplier."""
        if pct >= 100:
            return self._boost_tiers["above_threshold"]
        if pct >= 80:
            return self._boost_tiers["within_20pct"]
        if pct >= 50:
            return self._boost_tiers["within_50pct"]
        return 1.0
