"""Pin: DecisionRouter (per-decision-stakes model tier selection).

The 2026-06-22 'make-the-agent-smarter' Month-2 roadmap identified
stakes-aware routing as the highest-leverage architectural lever
after the Month-1 trio: today every LLM call uses Haiku regardless
of decision importance, so the gate's borderline judge gets the
same reasoning as a trivial length check.

This module ships ``route_decision()`` — the sibling to
``model_router.get_model_with_budget()``. They compose: budget
pressure can still downgrade an escalated decision; decision stakes
can upgrade what the task type would otherwise be.

These pins guard:
- Closed-set decision types route to the right default tier
- Unknown decision_type falls through to "default"
- Confidence band triggers escalation (0.4-0.6 for gate_judge)
- Outside-band confidence stays at default tier
- Tier → model mapping is config-overridable
- Fail-OPEN: every error path returns the cheap tier
- auto_approval_gate source pin: imports + calls route_decision
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class TestDecisionTypeDefaults:
    def setup_method(self):
        # Clear caches so test isolates from prior config overrides
        from genlab_core.cost.decision_router import reset_cache_for_tests

        reset_cache_for_tests()

    def test_gate_judge_defaults_to_cheap(self):
        """Pin: ``gate_judge`` with no confidence given → cheap (Haiku).
        Most gate decisions are clear-cut; we only escalate when the
        rule-based confidence lands in the uncertainty band."""
        from genlab_core.cost.decision_router import route_decision

        # No confidence → use default tier
        result = route_decision("gate_judge")
        assert "haiku" in result.lower()

    def test_cross_run_reflection_defaults_to_expensive(self):
        """Pin: ``cross_run_reflection`` (weekly/monthly summary of
        full episodic_events) defaults to ``expensive`` (Opus 1M).
        These calls need both the 1M context window AND the better
        reasoning to spot patterns. Cost is ~1-4 calls/month."""
        from genlab_core.cost.decision_router import route_decision

        result = route_decision("cross_run_reflection")
        assert "opus" in result.lower()

    def test_unknown_decision_type_falls_to_default(self):
        """Pin: an unknown decision_type isn't an error — fall back to
        the ``default`` rule (cheap). New decision types can be added
        without breaking existing call sites."""
        from genlab_core.cost.decision_router import route_decision

        result = route_decision("brand_new_unknown_decision_type_xyz")
        assert "haiku" in result.lower()


class TestConfidenceEscalation:
    def setup_method(self):
        from genlab_core.cost.decision_router import reset_cache_for_tests

        reset_cache_for_tests()

    def test_gate_judge_in_band_escalates_to_balanced(self):
        """Pin: gate_judge with confidence in [0.4, 0.6] escalates to
        balanced tier (Sonnet). This is the borderline band where
        rule-based gates are most uncertain and benefit from extra
        reasoning."""
        from genlab_core.cost.decision_router import route_decision

        for conf in (0.4, 0.45, 0.5, 0.55, 0.6):
            result = route_decision("gate_judge", confidence=conf)
            assert "sonnet" in result.lower(), (
                f"Confidence {conf} should escalate gate_judge to sonnet "
                f"(in 0.4-0.6 band); got {result}"
            )

    def test_gate_judge_outside_band_stays_cheap(self):
        """Pin: gate_judge with confidence OUTSIDE [0.4, 0.6] stays on
        cheap tier. We don't escalate when the rule-based step was
        very confident (either direction)."""
        from genlab_core.cost.decision_router import route_decision

        for conf in (0.05, 0.2, 0.39, 0.61, 0.8, 0.95):
            result = route_decision("gate_judge", confidence=conf)
            assert "haiku" in result.lower(), (
                f"Confidence {conf} should stay on haiku (outside 0.4-0.6 band); got {result}"
            )

    def test_hook_critic_escalation_band_different_from_gate(self):
        """Pin: hook_critic escalates on [0.35, 0.55] (different band
        from gate_judge [0.4, 0.6]). Each decision type has its own
        escalation rules tuned to its uncertainty profile."""
        from genlab_core.cost.decision_router import route_decision

        # 0.36 is in hook_critic's band but NOT in gate_judge's band
        hook_result = route_decision("hook_critic", confidence=0.36)
        gate_result = route_decision("gate_judge", confidence=0.36)
        assert "sonnet" in hook_result.lower()
        assert "haiku" in gate_result.lower()


class TestConfigOverride:
    def setup_method(self):
        from genlab_core.cost.decision_router import reset_cache_for_tests

        reset_cache_for_tests()

    def test_yaml_overrides_default_tier_models(self, tmp_path, monkeypatch):
        """Pin: a decision_tiers.yaml file can override the tier →
        model mapping. Operators can switch to cheaper models
        (e.g. swap Sonnet 4.6 for Sonnet 3.5 if pricing changes)
        without code changes."""
        from genlab_core.cost.decision_router import reset_cache_for_tests, route_decision

        config = tmp_path / "decision_tiers.yaml"
        config.write_text(
            """
tier_models:
  cheap: claude-haiku-CUSTOM
  balanced: claude-sonnet-CUSTOM
  expensive: claude-opus-CUSTOM
decisions:
  gate_judge:
    default_tier: cheap
"""
        )
        monkeypatch.setenv("DECISION_TIERS_CONFIG", str(config))
        reset_cache_for_tests()

        result = route_decision("gate_judge")
        assert result == "claude-haiku-CUSTOM"
        result_expensive = route_decision("cross_run_reflection")
        # cross_run_reflection isn't overridden in the YAML, so it uses
        # the default rules → expensive tier. The expensive tier IS
        # overridden in the YAML.
        assert result_expensive == "claude-opus-CUSTOM"

    def test_missing_config_uses_defaults(self, tmp_path, monkeypatch):
        """Pin: missing decision_tiers.yaml = use built-in defaults
        (no crash, just standard tier→model mapping)."""
        from genlab_core.cost.decision_router import reset_cache_for_tests, route_decision

        monkeypatch.setenv("DECISION_TIERS_CONFIG", str(tmp_path / "does_not_exist.yaml"))
        reset_cache_for_tests()
        result = route_decision("gate_judge")
        # Must still return a valid Haiku string (the default cheap tier)
        assert "haiku" in result.lower()

    def test_corrupt_config_falls_back_silently(self, tmp_path, monkeypatch):
        """Pin: bad YAML doesn't crash the router. Fail-OPEN to
        built-in defaults so a bad config push doesn't take down gates."""
        from genlab_core.cost.decision_router import reset_cache_for_tests, route_decision

        config = tmp_path / "broken.yaml"
        config.write_text("this: is: not: valid: yaml: [[")
        monkeypatch.setenv("DECISION_TIERS_CONFIG", str(config))
        reset_cache_for_tests()
        # MUST NOT raise
        result = route_decision("gate_judge")
        assert "haiku" in result.lower()


class TestFailOpen:
    def test_router_logic_exception_returns_cheap(self):
        """Pin: any exception in routing falls back to the cheap tier.
        Gates MUST NEVER block on cost-router errors — better to ship
        with Haiku than fail to ship at all."""
        from genlab_core.cost.decision_router import route_decision

        # Patch the rules loader to raise unexpectedly
        with patch(
            "genlab_core.cost.decision_router._get_decision_rules",
            side_effect=RuntimeError("config service crashed"),
        ):
            result = route_decision("gate_judge", confidence=0.5)
            assert "haiku" in result.lower()


class TestAutoApprovalGateWire:
    """Source-level pin: auto_approval_gate's _llm_judge_borderline
    function uses route_decision instead of hardcoded Haiku.

    Source pin is appropriate here because the actual LLM call is
    behind an env flag (GENLAB_LLM_JUDGE_ENABLED) + a real API key —
    a runtime test would need both. The source-level check confirms
    the wire is structurally present."""

    def test_gate_imports_and_uses_route_decision(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "scheduling"
            / "auto_approval_gate.py"
        )
        content = src.read_text()
        assert "from genlab_core.cost.decision_router import route_decision" in content, (
            "auto_approval_gate must import route_decision from decision_router"
        )
        assert 'route_decision("gate_judge"' in content, (
            'auto_approval_gate must call route_decision("gate_judge", ...)'
        )
        # The hardcoded model string should be GONE from the LLM judge
        # path (it's been replaced by the variable `model`)
        assert 'client.messages.create(\n            model="claude-haiku' not in content, (
            "auto_approval_gate must no longer hardcode the Haiku model in the gate judge"
        )
