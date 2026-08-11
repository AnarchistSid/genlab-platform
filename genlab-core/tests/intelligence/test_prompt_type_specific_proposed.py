"""2026-08-11: pins that guarantee the strategist prompt tells the LLM
to emit type-specific `proposed` values (numbers for numeric proposal
types, structured dict for arm_add) instead of prose.

Motivation: prior to today's fix, the schema hint read
    "proposed": "proposed value, or arm definition"
which invited the LLM to write prose like
    "Set novelty_rate to 0.30 for anime channel during BOOTSTRAP..."
Downstream auto-accept classifiers require structured values; prose
fails `float(proposed)` and every proposal for reward_weight /
gate_threshold / novelty_rate sat unaccepted forever.

These pins fire if a future edit removes the type-specific guidance,
so we catch the regression at CI rather than at a 5-day-later
"nothing auto-accepted this week again" audit.
"""

from __future__ import annotations


class TestSchemaHintCoversAllTypes:
    """Every proposal type must appear in the concrete example list so
    the LLM sees the correct shape for each."""

    def test_example_per_type_present(self):
        from genlab_core.intelligence.prompts import _SCHEMA_HINT

        # Concrete examples in the schema hint (one per type)
        # tell the LLM structural shape by demonstration.
        for proposal_type in [
            "arm_add",
            "reward_weight",
            "gate_threshold",
            "novelty_rate",
            "phase_shift",
            "manual_action",
        ]:
            assert f'"type": "{proposal_type}"' in _SCHEMA_HINT, (
                f"schema hint is missing a concrete example for "
                f"type={proposal_type!r} — LLM will guess shape"
            )


class TestProposedFieldRulesCoversNumericTypes:
    """Numeric proposal types MUST be documented as requiring numbers.
    If a future edit weakens this ('proposed': string OK), the auto-
    accept classifiers break silently."""

    def test_reward_weight_number_rule(self):
        from genlab_core.intelligence.prompts import _PROPOSED_FIELD_RULES

        assert "reward_weight" in _PROPOSED_FIELD_RULES
        assert "NUMBER" in _PROPOSED_FIELD_RULES

    def test_gate_threshold_number_rule(self):
        from genlab_core.intelligence.prompts import _PROPOSED_FIELD_RULES

        assert "gate_threshold" in _PROPOSED_FIELD_RULES
        assert "0.05" in _PROPOSED_FIELD_RULES
        assert "0.85" in _PROPOSED_FIELD_RULES

    def test_novelty_rate_number_rule(self):
        from genlab_core.intelligence.prompts import _PROPOSED_FIELD_RULES

        assert "novelty_rate" in _PROPOSED_FIELD_RULES
        assert "0.0" in _PROPOSED_FIELD_RULES
        assert "0.50" in _PROPOSED_FIELD_RULES

    def test_reward_weight_target_format_documented(self):
        """strategy_phase.py:213 parses reward_weight targets as
        `{niche}.reward_weight.{platform}.{metric}`. If the prompt
        doesn't document this, the LLM emits arbitrary target
        strings and every reward_weight proposal skips at target
        parsing time."""
        from genlab_core.intelligence.prompts import _PROPOSED_FIELD_RULES

        # Look for the format specifier tokens; the exact wording
        # can change but these tokens must be there.
        assert "{niche}" in _PROPOSED_FIELD_RULES
        assert "{platform}" in _PROPOSED_FIELD_RULES
        assert "{metric}" in _PROPOSED_FIELD_RULES

    def test_prose_goes_in_reasoning_not_proposed(self):
        """The critical rule that stops the LLM from putting prose
        inside `proposed`. This is the exact regression this pin
        exists to prevent."""
        from genlab_core.intelligence.prompts import _PROPOSED_FIELD_RULES

        # Should tell the LLM WHERE to put prose (reasoning /
        # expected_impact) and WHERE NOT (proposed for numeric types).
        assert "reasoning" in _PROPOSED_FIELD_RULES
        assert "NEVER" in _PROPOSED_FIELD_RULES or "NOT in" in _PROPOSED_FIELD_RULES.replace("NOT in", "NOT in ")


class TestRulesActuallyReachTheLLM:
    """Pin that the rules block is included in the user prompt sent
    to the LLM — not just defined and never referenced."""

    def test_user_prompt_includes_rules(self):
        from genlab_core.intelligence.prompts import (
            _PROPOSED_FIELD_RULES,
            build_user_prompt,
        )

        # Minimal state — most fields have defaults or format helpers
        # that tolerate empty/missing inputs.
        state = {
            "niche_id": "ai_creators",
            "week_of": "2026-08-10",
            "schema_version": 1,
            "detected_phase": "BOOTSTRAP",
            "phase_evidence": "test",
            "recent_publishes": [],
            "other_niches_summary": {},
            "active_findings": [],
            "last_week_outcomes": [],
            "counterfactual_replay": None,
        }
        prompt = build_user_prompt(state)

        # The rules text must be embedded in the actual prompt.
        # Sample a distinctive fragment to keep the assertion tight.
        assert "PROPOSED_FIELD_RULES" in prompt
        assert "reward_weight" in prompt
        assert "NUMBER" in prompt
