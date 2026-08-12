"""2026-08-12: pin the strategist's per-type `proposed` shape salvage.

Motivating class-of-bug: the prompt fix in commit 1cd74f5a instructs
the LLM to emit type-specific `proposed` values but prompts are
guidance — the LLM sometimes writes prose where a number is required.
Without runtime enforcement, malformed proposals land in
`strategist_reports.proposals` and every downstream auto-accept
classifier silently rejects them at consumer time.

Pattern mirrors the existing `_salvage_playbook` /
`_salvage_hypotheses` steps: drop malformed entries with a WARN,
keep the well-formed ones. Fail-soft — one bad proposal doesn't
invalidate the whole weekly report.
"""

from __future__ import annotations

from genlab_core.intelligence.strategist import (
    _PHASE_ENUM_VALUES,
    _proposal_has_valid_proposed_shape,
)


class TestNumericTypes:
    """reward_weight / gate_threshold / novelty_rate require numeric
    `proposed`. Prose was the F-QB-0702-adjacent bug shape."""

    def test_reward_weight_number_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "reward_weight", "proposed": 0.35}
        )
        assert ok

    def test_reward_weight_prose_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {"type": "reward_weight", "proposed": "Set weight to 0.35"}
        )
        assert not ok
        assert reason == "reward_weight:non_numeric_proposed"

    def test_gate_threshold_number_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "gate_threshold", "proposed": 0.4}
        )
        assert ok

    def test_novelty_rate_number_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "novelty_rate", "proposed": 0.25}
        )
        assert ok

    def test_novelty_rate_prose_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {
                "type": "novelty_rate",
                "proposed": (
                    "Set novelty_rate=0.30 for anime channel during "
                    "BOOTSTRAP. 30% of publishes should explore..."
                ),
            }
        )
        assert not ok
        assert reason.startswith("novelty_rate:")

    def test_numeric_string_accepts(self):
        """LLM sometimes serialises the number as a string like "0.35"
        — float() handles this so we accept."""
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "reward_weight", "proposed": "0.35"}
        )
        assert ok


class TestArmAdd:
    def test_dict_with_arm_id_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {
                "type": "arm_add",
                "proposed": {"arm_id": "style:gaming:tier_list_reaction"},
            }
        )
        assert ok

    def test_dict_missing_arm_id_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {"type": "arm_add", "proposed": {"prior_alpha": 1.0}}
        )
        assert not ok
        assert "not_dict_or_json_string" in reason

    def test_json_string_form_accepts(self):
        """2026-07-24 backward-compat: LLM sometimes serialises
        the dict as a JSON string. classify_arm_add parses this."""
        ok, _ = _proposal_has_valid_proposed_shape(
            {
                "type": "arm_add",
                "proposed": '{"arm_id": "style:sports:comparison"}',
            }
        )
        assert ok

    def test_json_string_missing_arm_id_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {"type": "arm_add", "proposed": '{"prior_alpha": 1.0}'}
        )
        assert not ok
        assert reason == "arm_add:json_string_no_arm_id"

    def test_prose_string_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {
                "type": "arm_add",
                "proposed": "Add a new style arm for gaming",
            }
        )
        assert not ok


class TestPhaseShift:
    def test_valid_enum_accepts(self):
        for phase in _PHASE_ENUM_VALUES:
            ok, _ = _proposal_has_valid_proposed_shape(
                {"type": "phase_shift", "proposed": phase}
            )
            assert ok, f"phase {phase!r} should accept"

    def test_lowercase_normalised_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "phase_shift", "proposed": "growth"}
        )
        assert ok

    def test_invalid_string_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {"type": "phase_shift", "proposed": "SPRINT_TO_MOON"}
        )
        assert not ok
        assert reason == "phase_shift:not_enum_string"


class TestProseTypes:
    def test_playbook_update_non_empty_string_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {
                "type": "playbook_update",
                "proposed": "Extend hook style guide with new pattern...",
            }
        )
        assert ok

    def test_manual_action_non_empty_string_accepts(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "manual_action", "proposed": "Operator: rotate FB tokens"}
        )
        assert ok

    def test_empty_prose_rejects(self):
        ok, reason = _proposal_has_valid_proposed_shape(
            {"type": "manual_action", "proposed": "   "}
        )
        assert not ok
        assert "empty_string" in reason


class TestUnknownType:
    """Unknown types pass shape-check; the outer Pydantic ProposalType
    enum validator catches them at model_validate time."""

    def test_unknown_type_accepts_at_shape_level(self):
        ok, _ = _proposal_has_valid_proposed_shape(
            {"type": "novel_type_the_llm_invented", "proposed": "anything"}
        )
        assert ok

    def test_missing_type_accepts_at_shape_level(self):
        ok, _ = _proposal_has_valid_proposed_shape({"proposed": 0.5})
        assert ok


class TestSalvageIntegrationViaParseReport:
    """End-to-end: _parse_report should drop malformed proposals
    with a WARN and keep well-formed ones."""

    def test_parse_report_drops_prose_proposal_keeps_number_proposal(
        self, caplog
    ):
        """Simulate the exact F-QB-0702 shape: one prose reward_weight
        proposal (should drop) + one well-formed one (should keep)."""
        import json
        from datetime import date

        from genlab_core.intelligence.strategist import Strategist, StrategistConfig

        # Minimal fixture: LLM returns a payload with two proposals,
        # one malformed. _parse_report should salvage.
        strategist = Strategist(
            collector=object(),  # type: ignore[arg-type]
            llm=object(),  # type: ignore[arg-type]
            persister=object(),  # type: ignore[arg-type]
            config=StrategistConfig(),
        )

        payload = {
            "detected_phase": "BOOTSTRAP",
            "phase_evidence": "test evidence citing concrete numbers " * 2,
            "weekly_summary": (
                "test summary describing the week's shape in prose " * 2
            ),
            "proposals": [
                {
                    "type": "reward_weight",
                    "target": "gaming.reward_weight.youtube.views",
                    "current": 0.3,
                    "proposed": 0.4,  # VALID
                    "reasoning": "twenty character reasoning here now",
                    "expected_impact": "twenty character expected impact",
                    "risk": "low",
                    "urgency": "this_week",
                },
                {
                    "type": "reward_weight",
                    "target": "gaming.reward_weight.youtube.likes",
                    "current": 0.1,
                    "proposed": "Please set weight to 0.15 next week",  # PROSE
                    "reasoning": "twenty character reasoning here now",
                    "expected_impact": "twenty character expected impact",
                    "risk": "low",
                    "urgency": "this_week",
                },
            ],
            "causal_hypotheses": [],
            "universal_playbook_proposals": [],
        }

        raw_text = json.dumps(payload)

        report = strategist._parse_report(raw_text, "gaming", date(2026, 8, 10))
        assert report is not None
        assert len(report.proposals) == 1
        assert float(report.proposals[0].proposed) == 0.4
