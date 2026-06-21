"""Pin: ``_llm_judge_borderline`` extends Lever K's pattern to the gate (Lever C).

Lever K (PR #425) added hook self-critique. Lever C extends that
pattern to the auto_approval_gate itself: when the gate's rule-based
confidence is in the borderline 0.3..0.7 range, ask Claude Haiku to
reason about the blueprint with the gate's signals as context. The
LLM verdict can override the rule-based decision (with audit trail
in the reasons list).

Opt-in via ``GENLAB_LLM_JUDGE_ENABLED=1`` env so the mechanism can
ship + be tested in shadow mode before activation.

These pins lock in:
  1. Default disabled — judge returns None when env unset
  2. Enabled with no API key → returns None (fail open)
  3. Enabled + grounded LLM verdict → returns updated AutoApprovalDecision
  4. Override case adds "[LLM judge OVERRIDE]" marker to reasons
  5. Agree case adds "[LLM judge AGREE]" marker to reasons
  6. evaluate() only fires the judge on borderline (0.3..0.7) confidence
  7. evaluate() returns the rule-based decision unchanged outside borderline
  8. JSON parse failures → judge returns None (fail open)
  9. API exceptions → judge returns None (fail open)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision


def _rule_decision(confidence: float = 0.5, approved: bool = True) -> AutoApprovalDecision:
    """Build a minimal AutoApprovalDecision to pass as the rule-based input."""
    return AutoApprovalDecision(
        approved=approved,
        confidence=confidence,
        passed_checks=["has_video", "has_hook"],
        failed_checks=[] if approved else ["composite_score"],
        reasons=["Video clip present", "Hook present"],
    )


def _blueprint(**overrides):
    """Build a blueprint dict matching what evaluate() expects."""
    bp = {
        "niche_id": "gaming",
        "hook_text": "Some borderline hook",
        "extra": {
            "composite_score": 0.32,  # Just above default 0.30 threshold
            "virality_score": 0.025,
            "validation_status": {"all_passed": True},
            "visual_paths": ["/x.mp4"],
        },
    }
    bp.update(overrides)
    return bp


class TestJudgeDisabledByDefault:
    def test_returns_none_when_env_unset(self, monkeypatch):
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        monkeypatch.delenv("GENLAB_LLM_JUDGE_ENABLED", raising=False)
        result = _llm_judge_borderline(_blueprint(), _rule_decision())
        assert result is None, "Default-disabled judge must return None"

    def test_returns_none_when_env_is_zero(self, monkeypatch):
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "0")
        assert _llm_judge_borderline(_blueprint(), _rule_decision()) is None


class TestJudgeFailOpen:
    @pytest.fixture(autouse=True)
    def _enable_judge(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")

    def test_no_api_key_returns_none(self, monkeypatch):
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _llm_judge_borderline(_blueprint(), _rule_decision()) is None

    def test_api_exception_returns_none(self, monkeypatch):
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        with patch("anthropic.Anthropic", side_effect=RuntimeError("simulated API outage")):
            result = _llm_judge_borderline(_blueprint(), _rule_decision())
        assert result is None

    def test_malformed_json_returns_none(self, monkeypatch):
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="not valid json {[}")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = _llm_judge_borderline(_blueprint(), _rule_decision())
        assert result is None


class TestJudgeVerdicts:
    @pytest.fixture(autouse=True)
    def _enable_judge(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    def test_agree_verdict_adds_audit_marker(self):
        """When LLM agrees with rule-based, the AGREE marker is added."""
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(text='{"approved": true, "reason": "hook is grounded + on-niche"}')
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        rule = _rule_decision(confidence=0.5, approved=True)
        with patch("anthropic.Anthropic", return_value=fake_client):
            result = _llm_judge_borderline(_blueprint(), rule)

        assert result is not None
        assert result.approved is True  # Same as rule-based
        assert result.confidence == 0.85  # Bumped to high-confidence
        # Audit marker present
        audit_entries = [r for r in result.reasons if "[LLM judge" in r]
        assert len(audit_entries) == 1
        assert "[LLM judge AGREE]" in audit_entries[0]
        assert "hook is grounded" in audit_entries[0]

    def test_override_verdict_flips_decision_and_adds_marker(self):
        """The killer pin: rule-based said APPROVE but LLM says REJECT
        — the gate's final decision flips to reject with audit trail."""
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(text='{"approved": false, "reason": "hook is generic clickbait template"}')
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        rule = _rule_decision(confidence=0.5, approved=True)  # rule says approve
        with patch("anthropic.Anthropic", return_value=fake_client):
            result = _llm_judge_borderline(_blueprint(), rule)

        assert result is not None
        # LLM overrode rule-based decision
        assert result.approved is False
        assert result.confidence == 0.7  # Override gets slightly lower confidence
        audit_entries = [r for r in result.reasons if "[LLM judge" in r]
        assert len(audit_entries) == 1
        assert "[LLM judge OVERRIDE]" in audit_entries[0]
        assert "generic clickbait" in audit_entries[0]

    def test_tolerates_code_fence_wrapped_json(self):
        """LLMs sometimes wrap JSON in code fences — parser strips them."""
        from genlab_core.scheduling.auto_approval_gate import _llm_judge_borderline

        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text='```json\n{"approved": true, "reason": "ok"}\n```')]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = _llm_judge_borderline(_blueprint(), _rule_decision())
        assert result is not None
        assert result.approved is True


class TestEvaluateIntegratesJudge:
    """End-to-end: evaluate() only fires the judge on borderline
    confidence + the judge's verdict shows up in the final decision."""

    @pytest.fixture(autouse=True)
    def _enable_judge(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    def test_judge_fires_on_borderline_confidence(self, monkeypatch):
        """When the rule-based confidence falls in 0.3..0.7, evaluate()
        invokes the LLM judge. We verify this via the audit-marker
        appearing in the returned decision's reasons list."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text='{"approved": true, "reason": "judge fired ok"}')]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        # Build a blueprint that lands in borderline range.
        bp = _blueprint()
        # composite_score=0.32 with default threshold 0.30 → confidence
        # is just-above-0.5, virality_score=0.025 → adds another low
        # confidence; averaged confidence will land in 0.4..0.6 range.
        with patch("anthropic.Anthropic", return_value=fake_client):
            decision = evaluate(bp)

        # Judge audit-marker present → judge fired
        audit_entries = [r for r in decision.reasons if "[LLM judge" in r]
        assert len(audit_entries) == 1, (
            f"Expected judge to fire on borderline confidence; reasons: {decision.reasons}"
        )

    def test_judge_does_not_fire_on_high_confidence(self, monkeypatch):
        """When confidence is well above 0.7 (clear approve), the judge
        is skipped — saves cost on the easy cases."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        fake_client = MagicMock()
        # If the judge fires, this fake client would be called; assert
        # call_count==0 to verify it's NOT invoked.

        bp = _blueprint()
        bp["extra"]["composite_score"] = 0.95  # very high
        bp["extra"]["virality_score"] = 0.10

        with patch("anthropic.Anthropic", return_value=fake_client):
            decision = evaluate(bp)

        # Judge not invoked → fake client's messages.create has 0 calls
        assert fake_client.messages.create.call_count == 0, (
            "Judge fired on high-confidence case (should be skipped to save cost)"
        )
        # No judge audit marker
        audit_entries = [r for r in decision.reasons if "[LLM judge" in r]
        assert audit_entries == []

    def test_judge_does_not_fire_when_disabled(self, monkeypatch):
        """Sanity: with env flag flipped off, evaluate() doesn't call
        the LLM even on borderline cases."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "0")

        fake_client = MagicMock()
        with patch("anthropic.Anthropic", return_value=fake_client):
            evaluate(_blueprint())

        assert fake_client.messages.create.call_count == 0
