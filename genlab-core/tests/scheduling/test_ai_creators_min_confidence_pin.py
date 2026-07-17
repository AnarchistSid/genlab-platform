"""Pin: auto-approver enforcement is on GAMING (not ai_creators).

2026-07-17 update: enforcement moved from ai_creators → gaming per
empirical calibration data (session-2026-07-17 audit round 3):

    ai_creators:  38 samples,  7.9% agreement (WORST of 5 niches)
    gaming:      116 samples, 90.5% agreement (READY per ≥30 × ≥90%)

Gaming's bandit stack also has 590 observations (best-trained), top
posteriors at 3-4× baseline (style:gaming:question 19.5% n=29,
gameplay_clip 11.1% n=191), and active ensemble decisioning (84 votes
in 30d vs ai_creators's 4). Enforcement follows the evidence.

## Historical context (kept for future re-enrollment of ai_creators)

Original 2026-07-13 diagnosis on ai_creators: min_confidence=0.85
was blocking every real content piece because the XGBoost hook
classifier scored 0.35-0.45 on production hooks, dragging the
6-check confidence mean below the threshold. Lowered to 0.80.

Follow-up 2026-07-17: hook_classifier uncertainty-band skip
(commit 5350d1c1) removes the sub-0.4 raw-score drag from confidence
math. Combined with today's move to gaming (a niche whose bandit
posteriors + ensemble votes produce genuine high-confidence signals),
the 0.80 floor is now empirically reachable.

## Ai_creators re-enrollment prerequisites (before flipping back on)

  1. calibration_agreement climbs to ≥90% (currently 7.9%)
  2. hook_classifier retrained on ≥500 examples (currently 125,
     pos_rate 25.6% → structural drag on confidence)
  3. Update this pin to point at BlackboxBrief again (currently
     tests CriticalRush's gaming yaml)
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_GAMING_PUBLISHING = (
    _ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "publishing.yaml"
)
_BB_PUBLISHING = _ROOT / "BlackboxBrief" / "config" / "publishing.yaml"


class TestAutoApproverEnrolledOnGaming:
    """Enforcement is on gaming as of 2026-07-17."""

    def _load_gaming(self) -> dict:
        with open(_GAMING_PUBLISHING) as f:
            return yaml.safe_load(f) or {}

    def _load_ai_creators(self) -> dict:
        with open(_BB_PUBLISHING) as f:
            return yaml.safe_load(f) or {}

    def test_gaming_auto_publish_enabled(self):
        """Gaming must be enabled — it's the empirically-ready niche
        (116 samples, 90.5% calibration agreement).

        Regression scenario: someone flips gaming off without ALSO
        flipping ai_creators back on with fresh calibration justification.
        That would leave the auto-approver system with ZERO enrolled
        niches — regressing to observation-only mode.
        """
        cfg = self._load_gaming()
        assert cfg.get("auto_publish", {}).get("enabled") is True, (
            "gaming auto_publish.enabled must be True — this is the "
            "empirically-ready niche (session-2026-07-17 audit round 3). "
            "If flipping off, first re-enroll another niche with fresh "
            "calibration data ≥30 samples × ≥90% agreement."
        )

    def test_ai_creators_disabled_pending_calibration(self):
        """ai_creators is disabled until calibration agreement climbs
        from 7.9% back above 90%. Regression scenario: someone flips
        it back on without empirical justification — repeating the
        14-day silent-block from 2026-07-13.
        """
        cfg = self._load_ai_creators()
        enabled = cfg.get("auto_publish", {}).get("enabled")
        assert enabled is False, (
            "ai_creators auto_publish.enabled must be False as of "
            "2026-07-17 (7.9% calibration agreement — WORST of 5 niches). "
            "Re-enrollment requires: (1) agreement ≥90%, (2) hook_classifier "
            "retrained on ≥500 examples. See yaml header for full "
            "prerequisites."
        )

    def test_gaming_min_confidence_matches_content_distribution(self):
        """0.80 threshold matched to the observed 6-check distribution.
        Higher threshold would silently disable approvals again."""
        cfg = self._load_gaming()
        min_conf = cfg.get("auto_publish", {}).get("min_confidence")
        assert min_conf is not None, "min_confidence must be set"
        assert 0.75 <= min_conf <= 0.82, (
            f"min_confidence={min_conf} outside the 0.75-0.82 band "
            "matched to observed content-quality distribution."
        )

    def test_gaming_rollout_pct_conservative(self):
        """Week-1 conservative rollout. auto2-ramp advances 0.10/day."""
        cfg = self._load_gaming()
        rollout = cfg.get("auto_publish", {}).get("rollout_pct")
        assert rollout is not None, "rollout_pct must be set"
        assert 0.05 <= rollout <= 0.50, (
            f"rollout_pct={rollout} outside the Week-1..auto-ramp-ceiling "
            "band. The auto2-ramp timer caps at DEFAULT_MAX_ROLLOUT_PCT=0.50; "
            "pushing past 0.50 requires manual operator action."
        )

    def test_gaming_max_approvals_per_pass_bounded(self):
        """Rate limit per 30-min run. 3 is fine for 1-post/day cadence."""
        cfg = self._load_gaming()
        max_ap = cfg.get("auto_publish", {}).get("max_approvals_per_pass")
        assert max_ap is not None
        assert 1 <= max_ap <= 5, (
            f"max_approvals_per_pass={max_ap} outside safe band."
        )
