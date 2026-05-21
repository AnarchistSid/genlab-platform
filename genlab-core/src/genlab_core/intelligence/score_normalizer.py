"""genlab_core.intelligence.score_normalizer — Scoring math infrastructure.

Provides the mathematical building blocks for content scoring without encoding
any niche-specific signals. Signals (what you measure) stay in each niche;
the math (how you normalise, decay, and composite) lives here.

Components:
    WelfordNormalizer  — Numerically stable running mean/variance (online algorithm)
    temporal_decay     — Exponential time-decay multiplier with configurable half-life
    stouffer_composite — Weighted composite score via Stouffer's Z-score method
    ScoringWeightLoader — YAML weight loading with mtime-based cache invalidation
"""

from __future__ import annotations

import math
import os


class WelfordNormalizer:
    """Welford's online algorithm for running mean and variance.

    Numerically stable alternative to collecting all values then computing
    mean/std. Handles streaming data without storing all samples in memory.

    The key advantage over the naive approach (accumulating sum/sum_sq):
    Welford's algorithm maintains numerical stability even when values have
    very different magnitudes or when there are large numbers of samples.
    This matters for scoring because engagement metrics can range from
    0 (zero views) to 100M+ (viral content), and naive variance computation
    loses precision in the low-value range.

    Usage:
        norm = WelfordNormalizer()
        for engagement_rate in stream_of_values:
            norm.update(engagement_rate)

        # Later, to normalise a new value to [0, 1]:
        score = norm.normalise(new_engagement_rate)

    Reference: Welford, B.P. (1962). "Note on a method for calculating
    corrected sums of squares and products." Technometrics. 4(3): 419-420.
    """

    def __init__(self) -> None:
        self.count: int = 0
        self.mean: float = 0.0
        self._M2: float = 0.0  # sum of squared deviations from mean

    def update(self, value: float) -> None:
        """Incorporate a new observation. O(1) time and space."""
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self._M2 += delta * delta2

    @property
    def variance(self) -> float:
        """Population variance (returns 0 if fewer than 2 observations)."""
        return self._M2 / self.count if self.count >= 2 else 0.0

    @property
    def std(self) -> float:
        """Population standard deviation."""
        return self.variance**0.5

    def normalise(self, value: float, clip: bool = True) -> float:
        """Z-score normalise value using running statistics, then rescale to [0, 1].

        Uses (value - mean) / (3 * std) so that values within 3 standard
        deviations map to roughly [0, 1]. Extreme outliers are clipped to [0, 1]
        if clip=True (the default).

        Returns 0.5 (neutral) if insufficient data to normalise.
        """
        if self.count < 2 or self.std < 1e-9:
            return 0.5  # neutral score when no distribution data available
        z = (value - self.mean) / (3 * self.std)
        normalised = z / 2 + 0.5  # shift [-0.5, 0.5] -> [0, 1]
        return max(0.0, min(1.0, normalised)) if clip else normalised

    def to_dict(self) -> dict:
        """Serialise state for persistence (e.g., to YAML or SharePoint)."""
        return {"count": self.count, "mean": self.mean, "M2": self._M2}

    @classmethod
    def from_dict(cls, data: dict) -> WelfordNormalizer:
        """Restore state from serialised dict."""
        norm = cls()
        norm.count = data["count"]
        norm.mean = data["mean"]
        norm._M2 = data["M2"]
        return norm


def temporal_decay(
    age_hours: float,
    half_life_hours: float = 24.0,
) -> float:
    """Compute a [0, 1] decay multiplier based on content age.

    Uses the standard exponential decay formula: 2^(-age/half_life).
    A half_life of 24 hours means content published 24 hours ago has
    half the temporal weight of content published now.

    Different content types should use different half-lives:
      - Breaking AI news: 6 hours (news decays fast)
      - Gaming clips: 48 hours (clips stay relevant longer)
      - Evergreen tutorials: 168 hours (one week)

    The configurable half_life_hours means niche configs can express
    content freshness requirements in YAML without touching the formula:

        scoring:
          temporal_decay_half_life_hours: 48   # gaming clips

    Args:
        age_hours: How old the content is in hours.
        half_life_hours: Time at which temporal weight = 0.5.

    Returns:
        Decay multiplier in (0, 1]. A fresh item returns close to 1.0;
        an item many half-lives old returns close to 0.0.
    """
    return math.pow(2.0, -age_hours / half_life_hours)


def stouffer_composite(
    scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Compute a weighted composite score using Stouffer's Z-score method.

    Stouffer's method is preferred over naive weighted average because
    it is statistically correct when combining independent signals of
    different reliability. The formula:

        Z_composite = sum(w_i * z_i) / sqrt(sum(w_i^2))

    where w_i are the weights and z_i are the individual signal scores.

    The key insight: naive weighted average treats all signals as equally
    precise. Stouffer accounts for the fact that signals with higher weights
    contribute proportionally more to the composite but also introduce
    proportionally more uncertainty. The sqrt(sum(w^2)) denominator
    normalises the combined variance.

    In practice for GenLab, this means a story with a very high virality
    score but missing engagement data doesn't get artificially inflated
    by the missing signal being set to 0.

    Args:
        scores:  Dict mapping signal name -> normalised score in [0, 1].
        weights: Dict mapping signal name -> weight (need not sum to 1).

    Returns:
        Composite score in [0, 1]. Missing signals (in weights but not
        scores) are filled with 0.5 (neutral) rather than 0, preventing
        composite deflation from unavailable APIs.

    Example:
        stouffer_composite(
            scores={"virality": 0.9, "relevance": 0.7},
            weights={"virality": 0.4, "relevance": 0.3, "recency": 0.3},
        )
        # recency is in weights but not scores -> filled with 0.5
    """
    if not weights:
        return 0.5

    weighted_sum = 0.0
    weight_sq_sum = 0.0
    for signal, w in weights.items():
        z = scores.get(signal, 0.5)  # neutral fallback for missing signals
        weighted_sum += w * z
        weight_sq_sum += w * w

    denominator = math.sqrt(weight_sq_sum)
    if denominator < 1e-9:
        return 0.5

    raw = weighted_sum / denominator
    # Rescale from z-space to [0, 1] via clip
    return max(0.0, min(1.0, raw))


class ScoringWeightLoader:
    """Load and validate scoring weights from YAML with mtime-based caching.

    Caches weights at instance level to avoid re-reading YAML on every
    pipeline run. Invalidates cache when the file modification time changes.

    Usage:
        loader = ScoringWeightLoader("/path/to/scoring_weights.yaml")
        weights = loader.get_weights("content_scoring")
    """

    def __init__(self, yaml_path: str) -> None:
        self._path = yaml_path
        self._weights: dict | None = None
        self._mtime: float = 0.0

    def get_weights(self, section: str) -> dict[str, float]:
        """Return weights for a named section. Re-reads file if modified."""
        import yaml

        current_mtime = os.path.getmtime(self._path)
        if self._weights is None or current_mtime > self._mtime:
            with open(self._path) as f:
                self._weights = yaml.safe_load(f)
            self._mtime = current_mtime
        return dict(self._weights.get(section, {}))
