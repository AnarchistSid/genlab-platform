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
import os
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

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
        "views": 0.2,
        "saves": 0.3,
        "dm_send_rate": 0.3,
        "shares": 0.15,
        "skip_rate": -0.05,
    },
    "tiktok": {
        "views": 0.25,
        "avg_watch_time": 0.30,
        "share_rate": 0.25,
        "follower_gained": 0.20,
    },
    "facebook": {
        "minutes_viewed": 0.40,
        "shares": 0.30,
        "completion_rate": 0.20,
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
        "views": 0.30,
        "replies": 0.30,
        "reposts": 0.25,
        "discovery_share": 0.15,
    },
}

# Default "good" metric values for logistic normalisation.
_METRIC_TARGETS: dict[str, dict[str, float]] = {
    "views": {"youtube": 10000, "instagram": 5000, "tiktok": 50000,
              "facebook": 3000, "twitter": 5000, "x": 5000},
    "avg_view_duration": {"youtube": 60},
    "saves": {"instagram": 200},
    "dm_send_rate": {"instagram": 0.05},
    "share_rate": {"tiktok": 0.02},
    "reply_chain_rate": {"twitter": 0.01, "x": 0.01},
    "completion_rate": {"facebook": 0.4},
    "skip_rate": {"instagram": 0.3},
    "subscriber_gained": {"youtube": 10},
    "follower_gained": {"tiktok": 50},
    "avg_watch_time": {"tiktok": 30},
    "minutes_viewed": {"facebook": 500},
    "shares": {"instagram": 50, "facebook": 30},
    "impressions": {"twitter": 5000, "x": 5000},
    "engagements": {"twitter": 200, "x": 200},
    "profile_clicks": {"twitter": 50, "x": 50},
    "like_rate": {"youtube": 0.04},
    "comment_rate": {"youtube": 0.01},
    "reach": {"facebook": 1000},
    "replies": {"threads": 20},
    "reposts": {"threads": 10},
    "discovery_share": {"threads": 0.1},
}


class RewardShaper:
    """Compute monetisation-aware rewards for bandit updates.

    Usage:
        shaper = RewardShaper(channel_metrics_fn=get_channel_metrics)
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 10000, "avg_view_duration": 45.0},
        )
        bandit.update(arm=selected_arm, reward=reward)

    Args:
        channel_metrics_fn: Callable that takes a platform name and returns
            a dict of channel-level metrics (subscriber_count, watch_hours, etc.).
            If None, no threshold boosting is applied (base weights only).
    """

    THRESHOLD_PROXIMITY = 0.20  # Within 20% triggers boost

    def __init__(
        self,
        channel_metrics_fn: Callable[[str], dict[str, float]] | None = None,
    ) -> None:
        self._channel_metrics_fn = channel_metrics_fn

    def get_adjusted_weights(
        self, platform: str, channel_metrics: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Compute reward weights adjusted for threshold proximity.

        When a channel metric is within 20% of its monetisation threshold,
        the boost_metric gets its weight multiplied by boost_factor.
        Weights are re-normalised afterward so they sum to 1.0.
        """
        weights = dict(BASE_WEIGHTS.get(platform, {}))
        if not weights:
            return weights

        if channel_metrics is None and self._channel_metrics_fn is not None:
            try:
                channel_metrics = self._channel_metrics_fn(platform)
            except Exception as e:
                logger.warning(
                    "[REWARD] Channel metrics fetch failed for %s: %s",
                    platform, e,
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
    ) -> float:
        """Compute a [0, 1] reward value for a bandit update.

        Args:
            platform: Platform the post was published on.
            metrics: Per-post metrics (views, saves, etc.).
            channel_metrics: Channel-level metrics for threshold proximity.
                If None and channel_metrics_fn was provided, it's called automatically.

        Returns:
            Reward in [0, 1].
        """
        weights = self.get_adjusted_weights(platform, channel_metrics)

        raw_reward = 0.0
        for metric, weight in weights.items():
            value = metrics.get(metric, 0.0)
            normalised = _normalise_metric(metric, value, platform)
            raw_reward += weight * normalised

        return max(0.0, min(1.0, raw_reward))


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
        multiplier_provider: Optional["MonetisationMultiplierProvider"] = None,
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
    def from_config(cls, niche_id: str) -> "MonetisationRewardShaper":
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
        """Compute a shaped reward in [0, ~1.5] from raw platform metrics.

        Returns 0.0 on any error — never raises.
        """
        try:
            reward_config = self._config.get("reward", {})
            weights = reward_config.get("weights", {
                "completion_rate": 0.40,
                "engagement": 0.35,
                "shares": 0.25,
            })

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

            logger.debug(
                "RewardShaper: platform=%s window=%s base=%.4f boost=%.3f "
                "shaped=%.4f normalised=%.4f",
                platform, window, base, boost, shaped, normalised,
            )
            return normalised

        except Exception as e:
            logger.error("RewardShaper: reward computation failed: %s", e)
            return 0.0

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
                    self._niche_id, platform,
                )
                # Convert multiplier (1.0/1.5/3.0) to a boost additive
                # 1.0 → 0.0 boost, 1.5 → 0.075 boost, 3.0 → 0.30 boost
                reward_config = self._config.get("reward", {})
                bonus = reward_config.get("threshold_proximity_bonus", 0.15)
                boost = bonus * (mult - 1.0)
                if boost > 0:
                    logger.info(
                        "[REWARD] %s/%s: live SP multiplier=%.1f -> boost=%.3f",
                        self._niche_id, platform, mult, boost,
                    )
                return boost
            except Exception as e:
                logger.warning(
                    "[REWARD] Provider failed for %s/%s: %s — falling back to YAML",
                    self._niche_id, platform, e,
                )

        # Fallback: static YAML path
        reward_config = self._config.get("reward", {})
        bonus = reward_config.get("threshold_proximity_bonus", 0.15)
        trigger = reward_config.get("threshold_proximity_trigger", 0.80)

        # Guard against division by zero when trigger==1.0
        if trigger >= 1.0:
            return 0.0

        primary_metric = _PLATFORM_PRIMARY_METRICS.get(platform, "engagement_rate")
        metric_key, threshold_yaml_key = _THRESHOLD_KEYS.get(
            primary_metric, (None, None)
        )
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

            from genlab_core.http.graph_proxy import GraphTableProxy

            # Load list_id from targets config
            try:
                with open(_get_targets_path()) as f:
                    cfg = yaml.safe_load(f) or {}
                list_id = cfg.get("sharepoint", {}).get("list_id", "")
            except Exception:
                list_id = ""

            if not list_id:
                logger.warning("MonetisationMultiplierProvider: no SP list_id configured")
                return

            site_id = os.environ.get("SHAREPOINT_SITE_ID", "").strip()
            proxy = GraphTableProxy(
                client._graph_client, site_id, list_id,
                "GenLab_MonetisationProgress",
            )
            records = proxy.all()

            new_cache: dict[str, dict] = {}
            for rec in records:
                key = f"{rec.get('niche_id')}/{rec.get('platform')}/{rec.get('metric_name')}"
                new_cache[key] = rec

            self._cache = new_cache
            self._cache_ts = now
            logger.debug(
                "MonetisationMultiplierProvider: refreshed %d records", len(new_cache),
            )

        except Exception as e:
            logger.error("MonetisationMultiplierProvider: cache refresh failed: %s", e)

    def get_progress(
        self, niche_id: str, platform: str, metric_name: str,
    ) -> Optional[dict]:
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
