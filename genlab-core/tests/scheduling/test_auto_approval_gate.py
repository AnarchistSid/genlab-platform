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


class TestShadowModeEnsembleWire:
    """2026-07-21: `evaluate()` invokes `ensemble_decide` as shadow-mode
    observability call. Locks in that the gate's decision is UNAFFECTED
    by ensemble outcomes (safety) AND that ensemble failure NEVER blocks
    the gate (fail-open)."""

    def _bp(self):
        return {
            "id": "bp-shadow-1",
            "niche_id": "gaming",
            "status": "VISUAL_READY",
            "hook_text": "hook",
            "extra": {
                "visual_paths": ["/tmp/x.mp4"],
                "composite_score": 0.6,
                "virality_score": 0.5,
                "validation_status": {"all_passed": True},
            },
        }

    def test_ensemble_decide_is_called(self):
        """evaluate() must invoke ensemble_decide with the same blueprint
        + niche_id. Otherwise the shadow-mode data flow is broken and
        ensemble_votes stops growing."""
        from unittest.mock import patch

        with patch(
            "genlab_core.scheduling.ensemble_decide.ensemble_decide"
        ) as mock_ensemble:
            evaluate(self._bp())
        mock_ensemble.assert_called_once()
        args, kwargs = mock_ensemble.call_args
        # blueprint positional, niche_id positional, enable_llm_judge kwarg
        assert args[0]["id"] == "bp-shadow-1"
        assert args[1] == "gaming"
        # LLM judge must be DISABLED — cost bound
        assert kwargs.get("enable_llm_judge") is False

    def test_ensemble_exception_does_not_break_gate(self):
        """Ensemble raising an exception must NOT propagate — the gate's
        decision is authoritative and must always return."""
        from unittest.mock import patch

        with patch(
            "genlab_core.scheduling.ensemble_decide.ensemble_decide",
            side_effect=RuntimeError("ensemble broken"),
        ):
            decision = evaluate(self._bp())
        # Gate still returned a decision
        assert decision is not None
        assert isinstance(decision.confidence, float)

    def test_llm_judge_never_enabled_from_gate_shadow(self):
        """Source-grep pin: the shadow call must pass
        `enable_llm_judge=False`. Cost anchor — if enabled, every gate
        evaluation could hit Anthropic on borderline decisions."""
        import inspect
        from genlab_core.scheduling.auto_approval_gate import evaluate

        src = inspect.getsource(evaluate)
        assert "enable_llm_judge=False" in src, (
            "shadow-mode ensemble_decide call must explicitly disable "
            "the LLM judge to keep cost bounded"
        )


class TestLLMJudgeOverrideAttribution:
    """2026-07-23: LLM judge override must attribute the reject/approve
    to the judge via failed_checks / passed_checks so downstream
    confusion-matrix analysis doesn't lose the signal.

    Bug caught in prod: 5 gaming FN rows in auto_approval_calibration
    have approved=false AND failed_checks=[] because the LLM overrode
    a rule-based approve to reject, and the old code preserved the
    (empty) rule-based failed_checks list.
    """

    def test_llm_override_reject_marks_failed_checks(self, monkeypatch):
        """LLM overrides approve -> reject: failed_checks must gain
        the 'llm_judge_override' marker so analysis attributes the
        rejection reason."""
        from unittest.mock import MagicMock, patch

        from genlab_core.scheduling.auto_approval_gate import (
            AutoApprovalDecision,
            _llm_judge_borderline,
        )

        # Rule-based decision that approves everything.
        rule_decision = AutoApprovalDecision(
            approved=True,
            confidence=0.5,  # Borderline so LLM fires.
            passed_checks=["has_video", "has_hook", "qc_passed"],
            failed_checks=[],
            reasons=["all checks passed"],
        )

        # Mock Anthropic response to reject.
        with patch("anthropic.Anthropic") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"approved": false, "reason": "content quality low"}')]
            mock_client.return_value.messages.create.return_value = mock_resp

            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
            monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")
            result = _llm_judge_borderline(
                {"id": "test", "niche_id": "gaming", "extra": {}},
                rule_decision,
            )

        assert result is not None
        assert result.approved is False, "LLM overrode to reject"
        assert "llm_judge_override" in result.failed_checks, (
            "override marker must appear in failed_checks so downstream "
            "analysis attributes the reject to the judge, not to an "
            "empty rule-based failed list"
        )

    def test_llm_override_approve_marks_passed_checks(self, monkeypatch):
        """LLM overrides reject -> approve: failed_checks cleared,
        'llm_judge_override' appended to passed_checks."""
        from unittest.mock import MagicMock, patch

        from genlab_core.scheduling.auto_approval_gate import (
            AutoApprovalDecision,
            _llm_judge_borderline,
        )

        rule_decision = AutoApprovalDecision(
            approved=False,
            confidence=0.5,
            passed_checks=["has_video"],
            failed_checks=["virality_score"],
            reasons=["low virality"],
        )

        with patch("anthropic.Anthropic") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"approved": true, "reason": "strong hook"}')]
            mock_client.return_value.messages.create.return_value = mock_resp

            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
            monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")
            result = _llm_judge_borderline(
                {"id": "test", "niche_id": "gaming", "extra": {}},
                rule_decision,
            )

        assert result is not None
        assert result.approved is True, "LLM overrode to approve"
        assert result.failed_checks == [], (
            "failed_checks must be cleared when LLM approves — leaving "
            "rule-based failures in place while approved=True is a "
            "confusing/inconsistent row shape"
        )
        assert "llm_judge_override" in result.passed_checks, (
            "override marker must appear in passed_checks so analysis "
            "attributes the approval to the judge"
        )

    def test_llm_agree_preserves_rule_lists_verbatim(self, monkeypatch):
        """When LLM agrees with rule-based, passed/failed lists match
        the rule-based verbatim — no synthetic marker added."""
        from unittest.mock import MagicMock, patch

        from genlab_core.scheduling.auto_approval_gate import (
            AutoApprovalDecision,
            _llm_judge_borderline,
        )

        rule_decision = AutoApprovalDecision(
            approved=False,
            confidence=0.5,
            passed_checks=["has_video"],
            failed_checks=["virality_score"],
            reasons=["low virality"],
        )

        with patch("anthropic.Anthropic") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"approved": false, "reason": "agree with rule"}')]
            mock_client.return_value.messages.create.return_value = mock_resp

            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
            monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")
            result = _llm_judge_borderline(
                {"id": "test", "niche_id": "gaming", "extra": {}},
                rule_decision,
            )

        assert result is not None
        assert result.approved is False
        assert result.passed_checks == ["has_video"]
        assert result.failed_checks == ["virality_score"]
        assert "llm_judge_override" not in result.failed_checks
        assert "llm_judge_override" not in result.passed_checks
