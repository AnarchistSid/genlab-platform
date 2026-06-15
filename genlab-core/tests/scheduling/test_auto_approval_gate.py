"""Tests for genlab_core.scheduling.auto_approval_gate.

Pins task AUTO #1: the foundation for the owner's autonomous-agent
vision. The gate evaluates whether each VISUAL_READY blueprint COULD be
auto-approved — does NOT execute approval. Dashboard surfaces the
decision as a "would auto-approve" badge.
"""

from __future__ import annotations

import pytest
from genlab_core.scheduling.auto_approval_gate import (
    evaluate,
)


def _bp(
    *,
    hook_text: str = "A reasonable hook",
    visual_paths: list | None = None,
    composite_score=0.6,
    virality_score=0.1,
    validation_status=None,
    **extra,
) -> dict:
    """Build a blueprint dict in the shape the gate consumes."""
    extra_dict = {
        "visual_paths": visual_paths if visual_paths is not None else ["/tmp/v.mp4"],
        "composite_score": composite_score,
        "virality_score": virality_score,
        "validation_status": validation_status,
        **extra,
    }
    return {
        "id": "test-id",
        "niche_id": "gaming",
        "status": "VISUAL_READY",
        "hook_text": hook_text,
        "extra": extra_dict,
    }


class TestHappyPath:
    def test_clean_blueprint_passes(self):
        """All checks pass with default thresholds."""
        decision = evaluate(_bp(validation_status={"all_passed": True}))
        assert decision.approved is True
        assert "has_video" in decision.passed_checks
        assert "has_hook" in decision.passed_checks
        assert "qc_passed" in decision.passed_checks
        assert "composite_score" in decision.passed_checks
        assert "virality_score" in decision.passed_checks
        assert decision.failed_checks == []
        # Confidence should be > 0.5 for any clean blueprint
        assert decision.confidence > 0.5

    def test_returns_immutable_decision(self):
        decision = evaluate(_bp(validation_status={"all_passed": True}))
        with pytest.raises((AttributeError, Exception)):
            decision.approved = False  # type: ignore[misc]


class TestVideoCheck:
    def test_missing_video_rejects(self):
        decision = evaluate(_bp(visual_paths=[]))
        assert decision.approved is False
        assert "has_video" in decision.failed_checks

    def test_video_check_can_be_disabled(self):
        decision = evaluate(_bp(visual_paths=[]), require_video=False)
        # No longer fails on video
        assert "has_video" not in decision.failed_checks


class TestHookCheck:
    def test_missing_hook_rejects(self):
        decision = evaluate(_bp(hook_text=""))
        assert decision.approved is False
        assert "has_hook" in decision.failed_checks

    def test_whitespace_only_hook_rejects(self):
        decision = evaluate(_bp(hook_text="   "))
        assert decision.approved is False
        assert "has_hook" in decision.failed_checks


class TestQCCheck:
    def test_qc_failed_rejects(self):
        decision = evaluate(
            _bp(validation_status={"all_passed": False, "issues": ["Missing caption"]})
        )
        assert decision.approved is False
        assert "qc_passed" in decision.failed_checks
        # The issue text should surface in reasons for the dashboard tooltip
        assert any("Missing caption" in r for r in decision.reasons)

    def test_qc_unknown_does_not_reject(self):
        """Cold-start tolerance — missing validation_status doesn't fail
        the gate. The blueprint can still auto-approve based on other
        signals; confidence reflects the uncertainty."""
        decision = evaluate(_bp(validation_status=None))
        assert "qc_unknown" in decision.passed_checks
        # Still approved iff other checks passed
        assert decision.approved is True

    def test_qc_can_be_disabled(self):
        decision = evaluate(
            _bp(validation_status={"all_passed": False, "issues": ["bad"]}),
            require_qc_pass=False,
        )
        # qc_passed not in failed when disabled
        assert "qc_passed" not in decision.failed_checks

    def test_string_validation_status_is_parsed(self):
        """Some writers store validation_status as JSON-encoded string.
        The gate must tolerate."""
        decision = evaluate(_bp(validation_status='{"all_passed": true}'))
        assert "qc_passed" in decision.passed_checks


class TestCompositeScore:
    def test_high_composite_passes(self):
        decision = evaluate(_bp(composite_score=0.9, validation_status={"all_passed": True}))
        assert "composite_score" in decision.passed_checks
        # High score → high confidence contribution
        assert decision.confidence > 0.7

    def test_below_threshold_rejects(self):
        decision = evaluate(_bp(composite_score=0.1, validation_status={"all_passed": True}))
        assert decision.approved is False
        assert "composite_score" in decision.failed_checks

    def test_missing_composite_does_not_reject(self):
        """Cold start: composite_score not yet computed → unknown not reject."""
        decision = evaluate(_bp(composite_score=None, validation_status={"all_passed": True}))
        # Other checks still drive approval
        assert "composite_score" not in decision.failed_checks
        assert decision.approved is True

    def test_custom_threshold_respected(self):
        # 0.5 below custom 0.7 threshold → reject
        decision = evaluate(
            _bp(composite_score=0.5, validation_status={"all_passed": True}),
            min_composite_score=0.7,
        )
        assert "composite_score" in decision.failed_checks


class TestViralityScore:
    def test_high_virality_passes(self):
        decision = evaluate(_bp(virality_score=0.8, validation_status={"all_passed": True}))
        assert "virality_score" in decision.passed_checks

    def test_zero_virality_rejects_at_default_threshold(self):
        decision = evaluate(_bp(virality_score=0.0, validation_status={"all_passed": True}))
        assert decision.approved is False
        assert "virality_score" in decision.failed_checks

    def test_missing_virality_does_not_reject(self):
        decision = evaluate(_bp(virality_score=None, validation_status={"all_passed": True}))
        assert "virality_score" not in decision.failed_checks
        assert decision.approved is True

    def test_default_threshold_is_002_per_d13(self):
        """Pin the AUTO #2 D1.3 threshold value. 2026-06-15 lowered
        ``_DEFAULT_MIN_VIRALITY_SCORE`` from 0.05 → 0.02 per the
        rollout runbook. Reverting silently would make future
        noise-floor blueprints (0.01-0.04) fail virality even though
        operator data shows they're approval-worthy. If you genuinely
        need to bump the threshold back up, update this pin AND the
        runbook AND the operator's expectation."""
        from genlab_core.scheduling.auto_approval_gate import _DEFAULT_MIN_VIRALITY_SCORE

        assert _DEFAULT_MIN_VIRALITY_SCORE == 0.02, (
            f"D1.3 threshold value drift: {_DEFAULT_MIN_VIRALITY_SCORE} != 0.02. "
            "Update both the constant AND this pin AND the runbook if you "
            "need to change the floor."
        )

    def test_virality_just_above_002_threshold_passes(self):
        """Boundary regression: 0.03 must pass under the new floor."""
        decision = evaluate(_bp(virality_score=0.03, validation_status={"all_passed": True}))
        assert "virality_score" in decision.passed_checks

    def test_virality_just_below_002_threshold_fails(self):
        """Boundary regression: 0.01 must still fail at the new floor."""
        decision = evaluate(_bp(virality_score=0.01, validation_status={"all_passed": True}))
        assert "virality_score" in decision.failed_checks
        assert decision.approved is False


class TestConfidenceAggregation:
    def test_confidence_in_unit_interval(self):
        for composite in (0.0, 0.3, 0.5, 0.7, 1.0):
            for virality in (0.0, 0.1, 0.5, 1.0):
                decision = evaluate(
                    _bp(
                        composite_score=composite,
                        virality_score=virality,
                        validation_status={"all_passed": True},
                    )
                )
                assert 0.0 <= decision.confidence <= 1.0

    def test_two_low_scores_lower_confidence_than_two_high(self):
        low = evaluate(
            _bp(
                composite_score=0.3,
                virality_score=0.05,
                validation_status={"all_passed": True},
            )
        )
        high = evaluate(
            _bp(
                composite_score=1.0,
                virality_score=1.0,
                validation_status={"all_passed": True},
            )
        )
        # Both approved but one is more confident
        assert low.approved is True
        assert high.approved is True
        assert high.confidence > low.confidence


class TestReasonsAlwaysPopulated:
    """The dashboard tooltip reads `reasons`. Empty would be confusing."""

    def test_reasons_present_on_approval(self):
        decision = evaluate(_bp(validation_status={"all_passed": True}))
        assert len(decision.reasons) > 0

    def test_reasons_present_on_rejection(self):
        decision = evaluate(_bp(visual_paths=[], hook_text=""))
        assert len(decision.reasons) > 0
        # Should include REJECT prefix for failed checks
        assert any(r.startswith("REJECT:") for r in decision.reasons)


class TestDefensiveExtraHandling:
    def test_none_extra_handled(self):
        bp = {"id": "x", "niche_id": "gaming", "hook_text": "ok", "extra": None}
        # Should not crash; should fail on missing video (which is in extra)
        decision = evaluate(bp)
        assert "has_video" in decision.failed_checks

    def test_non_dict_extra_handled(self):
        bp = {
            "id": "x",
            "niche_id": "gaming",
            "hook_text": "ok",
            "extra": "not a dict",
        }
        decision = evaluate(bp)
        # has_video defaults to False; we reject on that
        assert "has_video" in decision.failed_checks

    def test_visual_paths_at_top_level_also_recognized(self):
        """Some code paths put visual_paths at top level instead of in extra."""
        bp = {
            "id": "x",
            "niche_id": "gaming",
            "hook_text": "ok",
            "visual_paths": ["/tmp/v.mp4"],
            "extra": {"validation_status": {"all_passed": True}},
        }
        decision = evaluate(bp)
        assert "has_video" in decision.passed_checks


class TestVisualPathsStringDecodeInGate:
    """Pin the 2026-06-15 audit fix.

    Bug: the gate's has_video check did `bool(extra.get("visual_paths"))`.
    On the Postgres path every blueprint has extra as a dict, so the
    caller-side wrapper-builders (auto_approver, calibration_helper)
    that do the JSON-decode short-circuit and never run. extra
    ["visual_paths"] arrives as the literal JSON string `"[]"` or
    `'["/x.mp4"]'`, and bool of any non-empty string is True — so
    has_video=True for blueprints with empty visual_paths arrays.

    Fix: gate decodes inline via _decode_visual_paths.
    """

    def test_empty_array_string_decoded_to_false(self):
        """The headline pin: extra.visual_paths = "[]" (empty array
        as string) must produce has_video=False."""
        decision = evaluate(_bp(visual_paths=None, validation_status={"all_passed": True}))
        # In _bp, visual_paths=None drops the key from extra. Let me
        # use the actual problematic shape directly via dict.
        bp = {
            "id": "x",
            "niche_id": "gaming",
            "status": "VISUAL_READY",
            "hook_text": "h",
            "extra": {
                "visual_paths": "[]",  # the BUG case: string with empty array
                "composite_score": 0.5,
                "virality_score": 0.5,
                "validation_status": {"all_passed": True},
            },
        }
        decision = evaluate(bp)
        assert "has_video" in decision.failed_checks, (
            "extra.visual_paths='[]' (empty array as string) must fail "
            "has_video — bool of any non-empty string is True without the "
            "fix, so this would have falsely passed."
        )

    def test_non_empty_array_string_decoded_to_true(self):
        """Boundary: a string containing a real array should pass."""
        bp = {
            "id": "x",
            "niche_id": "gaming",
            "status": "VISUAL_READY",
            "hook_text": "h",
            "extra": {
                "visual_paths": '["/a.mp4"]',
                "composite_score": 0.5,
                "virality_score": 0.5,
                "validation_status": {"all_passed": True},
            },
        }
        decision = evaluate(bp)
        assert "has_video" in decision.passed_checks

    def test_malformed_json_decoded_to_false(self):
        """Garbled string must not pass has_video."""
        bp = {
            "id": "x",
            "niche_id": "gaming",
            "status": "VISUAL_READY",
            "hook_text": "h",
            "extra": {
                "visual_paths": "not-valid-json!!",
                "composite_score": 0.5,
                "virality_score": 0.5,
                "validation_status": {"all_passed": True},
            },
        }
        decision = evaluate(bp)
        assert "has_video" in decision.failed_checks
