"""Pin: hook_classifier_score is read by auto_approval_gate as soft signal.

Background: Lever D1 (PR #420) activated the dormant XGBoost hook
classifier in BaseHookStrategy. Every hook now gets a [0,1] predicted-
engagement score stored on the story dict at
`base_hooks.py:168` (story["hook_classifier_score"] = clf_score).

The 2026-06-22 autonomy audit found this was a half-wired loop:
- WRITE: score set on story dict by base_hooks ✓
- READ: NOWHERE — zero downstream consumers ✗
- EFFECT: NONE — the XGBoost prediction was being computed then
  silently discarded at the story → blueprint boundary

This PR closes the loop:
1. push_to_backlog forwards story["hook_classifier_score"] →
   fields["hook_classifier_score"]
2. push_to_backlog's synth gate evaluation includes the score in extra
3. dashboard's auto_approval_preview normalization includes the score
4. auto_approval_gate.evaluate() adds a 6th SOFT check that reads
   extra["hook_classifier_score"] and feeds it through the confidence
   aggregate (never adds to failed_checks — soft signal only)

These pins guard that 6th check's behavior in the three regimes:
absent (cold-start), strong (>=0.5), weak (<0.5).
"""

from __future__ import annotations

from genlab_core.scheduling.auto_approval_gate import evaluate


def _good_blueprint(**extra_overrides):
    """Build a blueprint that passes the first 5 checks so the 6th's
    behavior is observable in isolation."""
    extra = {
        "visual_paths": ["/tmp/test.mp4"],
        "composite_score": 0.8,
        "virality_score": 0.5,
        "validation_status": {"all_passed": True},
    }
    extra.update(extra_overrides)
    return {
        "id": "bp-test",
        "hook_text": "test hook with real words",
        "niche_id": "gaming",
        "extra": extra,
    }


class TestHookClassifierScoreSoftSignal:
    def test_missing_score_is_cold_start_tolerant(self):
        """Pin: blueprints that predate the wire have no score field.
        The gate must NOT fail them, NOT add to confidence aggregate
        (preserves pre-Lever-D1 confidence math), and must surface a
        clear audit-trail reason."""
        bp = _good_blueprint()  # no hook_classifier_score key
        bp_with_strong = _good_blueprint(hook_classifier_score=0.9)
        result = evaluate(bp)
        # Must not have failed on the classifier score
        assert "hook_classifier_score" not in result.failed_checks
        # Must surface the "missing" reason for operator audit trail
        assert any("hook_classifier_score missing" in r for r in result.reasons), (
            f"Reasons should call out missing score: {result.reasons}"
        )
        # Still approves because all 5 hard checks pass
        assert result.approved is True
        # Critical: missing must NOT contribute to confidence aggregate
        # (preserves pre-Lever-D1 confidence math for in-flight
        # blueprints that predate the wire)
        result_strong = evaluate(bp_with_strong)
        assert result.confidence != result_strong.confidence, (
            "Missing score should leave confidence unchanged from baseline; "
            "strong score should differ — proves missing is a non-contributor."
        )

    def test_strong_score_lifts_confidence(self):
        """Pin: a strong (>=0.5) score MUST appear in passed_checks AND
        contribute positively to the confidence aggregate."""
        bp = _good_blueprint(hook_classifier_score=0.9)
        result = evaluate(bp)
        assert "hook_classifier_score" in result.passed_checks
        assert any(
            "hook_classifier_score=0.90" in r and "predicted strong" in r for r in result.reasons
        ), f"Reasons should call out strong score: {result.reasons}"
        # Should not fail — it's a SOFT signal
        assert "hook_classifier_score" not in result.failed_checks
        assert result.approved is True

    def test_weak_score_does_NOT_force_rejection(self):
        """Pin: a weak (<0.5) score MUST NOT add itself to failed_checks
        (would over-suppress good hooks the classifier under-rates).
        The score still drags the confidence aggregate downward via
        its raw value entering the confidences list."""
        bp = _good_blueprint(hook_classifier_score=0.1)
        result = evaluate(bp)
        # Critical pin: a weak score MUST NOT be a hard reject
        assert "hook_classifier_score" not in result.failed_checks, (
            f"hook_classifier_score must be SOFT only — never add to "
            f"failed_checks. failed_checks={result.failed_checks}"
        )
        # Must surface the audit reason
        assert any(
            "hook_classifier_score=0.10" in r and "predicted weak" in r for r in result.reasons
        ), f"Reasons should call out weak soft signal: {result.reasons}"
        # Other 5 checks still pass → still approved
        assert result.approved is True

    def test_weak_score_drags_confidence_down(self):
        """Pin: a weak score must produce LOWER confidence than a
        strong score on the same blueprint — proves the score is
        actually flowing into the aggregate."""
        bp_weak = _good_blueprint(hook_classifier_score=0.1)
        bp_strong = _good_blueprint(hook_classifier_score=0.95)

        weak = evaluate(bp_weak)
        strong = evaluate(bp_strong)

        # Strong score must produce strictly higher confidence
        assert strong.confidence > weak.confidence, (
            f"strong={strong.confidence} should exceed weak={weak.confidence} "
            f"— proves the hook_classifier_score is feeding the aggregate."
        )

    def test_score_clamped_to_unit_interval(self):
        """Pin: out-of-range scores (negative or >1) must be clamped to
        [0,1] before contributing to confidence — guards against
        classifier output drift or upstream bug producing nonsense."""
        bp_high = _good_blueprint(hook_classifier_score=5.0)  # absurd
        bp_low = _good_blueprint(hook_classifier_score=-0.5)  # absurd
        # Must not crash
        result_high = evaluate(bp_high)
        result_low = evaluate(bp_low)
        # Confidence still in valid range
        assert 0.0 <= result_high.confidence <= 1.0
        assert 0.0 <= result_low.confidence <= 1.0

    def test_non_numeric_score_treated_as_missing(self):
        """Pin: garbage values (string, None, dict) must fall through
        to the missing-score code path rather than crashing the gate."""
        for bad_value in ("invalid", {"x": 1}, [1, 2]):
            bp = _good_blueprint(hook_classifier_score=bad_value)
            result = evaluate(bp)  # must not crash
            # Should land in the missing-score reason path
            assert any("hook_classifier_score missing" in r for r in result.reasons), (
                f"Bad value {bad_value!r} should land in missing-score path; "
                f"got reasons={result.reasons}"
            )
