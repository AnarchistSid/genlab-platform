"""Pin: ai_creators AUTO #2 min_confidence threshold reflects actual
content-quality distribution.

Post-2026-07-13 diagnosis. The auto-approver was silently no-op-ing
for 14 days despite:

  - ``auto_publish.enabled: true`` in BlackboxBrief/config/publishing.yaml
  - Calibration data at 92% agreement (well above ≥90% ready threshold)
  - Systemd timer firing every 30 min
  - Kill switches inactive

Journal grep of `genlab-auto-approver.service` showed EVERY tick:
``[ai_creators] examined=9 approved=0 low_conf=4 rejected=3``.

Root cause: ``min_confidence: 0.85`` was set as a conservative
Week-1 default before the ai_creators content-quality distribution
was empirically known. Confidence math aggregates 6 checks, one being
the XGBoost hook classifier which consistently scores 0.35-0.45 on
real content — dragging the mean confidence from ~0.93 (5-check
average) to ~0.82. NO real blueprint hit 0.85.

Threshold lowered to 0.80 to match observed distribution. This test
pins the value + explains why raising it back should require
empirical justification, not just tightening the knob.

If a future PR raises min_confidence to 0.85+ without demonstrating
that the hook classifier scores or virality signals have improved,
this test fires — the class-of-bug "conservative default silently
disables the machine" is exactly what today's diagnosis exposed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_BB_PUBLISHING = _ROOT / "BlackboxBrief" / "config" / "publishing.yaml"


class TestAiCreatorsAutoPublishConfig:
    def _load(self) -> dict:
        with open(_BB_PUBLISHING) as f:
            return yaml.safe_load(f) or {}

    def test_auto_publish_enabled(self):
        """Sanity — the yaml block hasn't drifted to disabled."""
        cfg = self._load()
        assert cfg.get("auto_publish", {}).get("enabled") is True, (
            "ai_creators auto_publish.enabled must be True — otherwise "
            "the auto-approver worker skips this niche entirely and "
            "the AUTO #2 arc regresses to observation-only."
        )

    def test_min_confidence_matches_content_distribution(self):
        """The whole point of today's fix. 0.80 reflects the observed
        6-check confidence distribution for real ai_creators content.
        Raising back to 0.85+ without empirical justification (hook
        classifier retraining, virality signal improvement) will
        silently disable auto-approvals again — exactly the failure
        mode this diagnosis exposed."""
        cfg = self._load()
        min_conf = cfg.get("auto_publish", {}).get("min_confidence")
        assert min_conf is not None, "min_confidence must be set"
        assert 0.75 <= min_conf <= 0.82, (
            f"min_confidence={min_conf} is outside the 0.75-0.82 band "
            "matched to the observed content-quality distribution. If "
            "raising, first prove content quality has improved: "
            "  1. Query auto_approval_confidence distribution over "
            "     last 30 days across VISUAL_READY ai_creators blueprints "
            "  2. Show ≥3 blueprints/week clearing the new threshold "
            "  3. Update this test's band with a diagnosis-anchor "
            "     reference (e.g. 'session-2026-XX-XX')"
        )

    def test_rollout_pct_still_conservative(self):
        """Week 1 rollout at 10% — one-in-ten qualifiers auto-approves.
        A future PR ramps 0.1 → 0.25 → 0.5 → 1.0 based on confusion-
        matrix stability. Pin the current stage."""
        cfg = self._load()
        rollout = cfg.get("auto_publish", {}).get("rollout_pct")
        assert rollout is not None, "rollout_pct must be set"
        assert 0.05 <= rollout <= 0.30, (
            f"rollout_pct={rollout} outside the Week-1/Week-2 conservative "
            "band. Higher rollouts require calibration matrix review."
        )

    def test_max_approvals_per_pass_bounded(self):
        """Rate limit per 30-min run. 3 is fine for 1-post/day cadence
        with rollout 0.1 — statistically fires <1x/day today."""
        cfg = self._load()
        max_ap = cfg.get("auto_publish", {}).get("max_approvals_per_pass")
        assert max_ap is not None
        assert 1 <= max_ap <= 5, (
            f"max_approvals_per_pass={max_ap} outside safe band. "
            "The publisher fires 1/day; approving >5/pass overshoots."
        )
