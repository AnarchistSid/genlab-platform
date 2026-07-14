"""Pin tests for the 2026-07-14 multi-window reward collector fixes.

Session 2026-07-14 diagnostic found the ``genlab-late-reward.service``
runner had been executing daily since deploy but its output was
silently degraded by two bugs:

1. **RewardShaper.channel_metrics_fn signature mismatch**: shaper
   called ``fn(platform)`` but prod ``get_channel_metrics`` requires
   ``(niche_id, platform)``. Every reward compute logged
   ``TypeError: get_channel_metrics() missing 1 required positional
   argument: 'platform'`` at WARNING; the ``channel_metrics = {}``
   fallback then disabled monetisation-threshold boosting across all
   niches for months.

2. **delta_pct==0 when reward_48h==0**: the ``significant_lift``
   detection gate used ``abs(delta.delta_pct) > 0.20``. When
   ``reward_48h`` is 0, division-by-zero returns 0 by design — but
   that means every bombed-at-48h/recovered-at-7d post (the strongest
   late-tail-lift signal in the dataset) was invisible to the gate.
   Prior to fix, ``significant_lift`` was 0 in every batch even when
   real lift existed.

These tests pin both fixes so future refactors can't regress them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from genlab_core.learning.late_reward import (
    LateRewardDelta,
    _is_significant_lift,
)
from genlab_core.learning.reward_shaper import RewardShaper


class TestChannelMetricsFnSignature:
    """RewardShaper must call channel_metrics_fn with (niche_id, platform)."""

    def test_fn_called_with_two_args(self):
        received = []

        def spy_fn(niche_id: str, platform: str) -> dict:
            received.append((niche_id, platform))
            return {}

        shaper = RewardShaper(channel_metrics_fn=spy_fn, niche_id="gaming")
        shaper.get_adjusted_weights("facebook")
        assert received == [("gaming", "facebook")]

    def test_niche_id_flows_from_constructor(self):
        received = []

        def spy_fn(niche_id: str, platform: str) -> dict:
            received.append((niche_id, platform))
            return {}

        # Each niche_id in construction should flow through
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            spy = RewardShaper(channel_metrics_fn=spy_fn, niche_id=niche)
            spy.get_adjusted_weights("youtube")

        assert received == [
            ("ai_creators", "youtube"),
            ("gaming", "youtube"),
            ("sports", "youtube"),
            ("movies", "youtube"),
            ("anime", "youtube"),
        ]

    def test_prod_get_channel_metrics_signature_compat(self):
        """Regression pin: the prod get_channel_metrics is 2-arg.
        Injecting it must not raise TypeError."""
        from genlab_core.learning.metric_collector import get_channel_metrics

        shaper = RewardShaper(channel_metrics_fn=get_channel_metrics, niche_id="")
        # Even if DB is unreachable in tests, this must NOT raise TypeError —
        # it should either return the shaper's fallback weights or the empty
        # dict from get_channel_metrics's own except-branch. TypeError from
        # signature mismatch is what we're pinning against.
        try:
            shaper.get_adjusted_weights("youtube")
        except TypeError as exc:
            if "missing" in str(exc) and "argument" in str(exc):
                raise AssertionError(
                    "get_channel_metrics signature mismatch regressed: "
                    + str(exc)
                ) from exc
            raise


class TestSignificantLiftGate:
    """The _is_significant_lift gate must fire for the bombed-then-recovered
    class of posts that delta_pct alone can't detect."""

    @staticmethod
    def _delta(reward_48h: float, reward_late: float) -> LateRewardDelta:
        delta = reward_late - reward_48h
        delta_pct = (delta / reward_48h) if reward_48h else 0.0
        return LateRewardDelta(
            blueprint_id="test",
            niche_id="gaming",
            arm_id="viral_moment",
            platform="facebook",
            reward_48h=reward_48h,
            reward_late=reward_late,
            window_days=7,
            delta=delta,
            delta_pct=delta_pct,
            measured_at=datetime.now(UTC),
        )

    def test_bombed_at_48h_recovered_at_7d_is_significant(self):
        """The primary bug this fix targets. reward_48h=0 + real late reward
        was invisible pre-fix because delta_pct=0."""
        d = self._delta(reward_48h=0.0, reward_late=0.13)
        assert _is_significant_lift(d), (
            f"delta_pct={d.delta_pct} was 0 (base-zero); "
            "gate must fire on absolute delta"
        )

    def test_bombed_at_48h_still_bombed_at_7d_is_NOT_significant(self):
        """Both zero = no signal, no bandit push."""
        d = self._delta(reward_48h=0.0, reward_late=0.0)
        assert not _is_significant_lift(d)

    def test_small_lift_from_zero_below_threshold_is_NOT_significant(self):
        """Absolute-delta gate has a floor — a 0.02 reward from zero base
        is noise, not signal."""
        d = self._delta(reward_48h=0.0, reward_late=0.02)
        assert not _is_significant_lift(d)

    def test_large_positive_pct_lift_is_significant(self):
        """Primary criterion still works — 100% relative lift triggers."""
        d = self._delta(reward_48h=0.1, reward_late=0.25)
        # delta_pct = 1.5 (150%), way above 0.20
        assert _is_significant_lift(d)

    def test_large_negative_pct_lift_is_significant(self):
        """Regressions matter too — abs() gate catches them."""
        d = self._delta(reward_48h=0.5, reward_late=0.1)
        # delta_pct = -0.8, abs > 0.20
        assert _is_significant_lift(d)

    def test_flat_change_is_NOT_significant(self):
        """No delta = no signal."""
        d = self._delta(reward_48h=0.3, reward_late=0.31)
        # delta_pct ~ 0.033, below 0.20 threshold
        assert not _is_significant_lift(d)
