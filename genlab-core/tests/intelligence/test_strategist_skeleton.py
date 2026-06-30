"""Smoke tests for Strategist skeleton — locks interfaces before PR Strategist-1.

These tests verify the shape of the contracts (Pydantic schemas + Protocol
interfaces). Full execution tests land with PR Strategist-1 when the
NotImplementedError body is replaced.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from genlab_core.intelligence.proposal_schema import (
    SCHEMA_VERSION,
    CausalHypothesis,
    HypothesisConfidence,
    PlaybookProposal,
    Proposal,
    ProposalRisk,
    ProposalType,
    ProposalUrgency,
    StrategicPhase,
    StrategistReport,
)
from genlab_core.intelligence.strategist import Strategist, StrategistConfig, week_of


class TestSchemaShape:
    """Validates the contracts the LLM output must conform to."""

    def test_proposal_round_trips_through_json(self):
        p = Proposal(
            type=ProposalType.PHASE_SHIFT,
            target="ai_creators.phase",
            current="BOOTSTRAP",
            proposed="GROWTH",
            reasoning="Follower count crossed 100 on 2026-06-22; phase config should shift.",
            expected_impact="Reward weights move to shares + new_followers from views-heavy mix.",
            risk=ProposalRisk.LOW,
            urgency=ProposalUrgency.SHIP_NOW,
        )
        round_tripped = Proposal.model_validate_json(p.model_dump_json())
        assert round_tripped == p

    def test_proposal_rejects_unknown_type(self):
        """Type whitelist is a safety boundary — bad LLM output must be rejected."""
        with pytest.raises(ValidationError):
            Proposal(
                type="phase_destroy",  # not in ProposalType
                target="x",
                current=None,
                proposed=None,
                reasoning="x" * 20,
                expected_impact="x" * 20,
                risk="low",
                urgency="ship_now",
            )

    def test_proposal_rejects_short_reasoning(self):
        """Reasoning must be substantive (>20 chars) — guards against vacuous LLM output."""
        with pytest.raises(ValidationError):
            Proposal(
                type=ProposalType.PHASE_SHIFT,
                target="x",
                current=None,
                proposed=None,
                reasoning="ok",  # too short
                expected_impact="x" * 20,
                risk=ProposalRisk.LOW,
                urgency=ProposalUrgency.SHIP_NOW,
            )

    def test_proposal_rejects_extra_fields(self):
        """extra='forbid' — prevents LLM from hiding instructions in unrecognized keys."""
        with pytest.raises(ValidationError):
            Proposal.model_validate(
                {
                    "type": "phase_shift",
                    "target": "x",
                    "current": None,
                    "proposed": None,
                    "reasoning": "x" * 20,
                    "expected_impact": "x" * 20,
                    "risk": "low",
                    "urgency": "ship_now",
                    "execute_now": True,  # malicious extra
                }
            )

    def test_causal_hypothesis_requires_evidence(self):
        with pytest.raises(ValidationError):
            CausalHypothesis(
                pattern="some pattern",
                hypothesis="x" * 20,
                confidence=HypothesisConfidence.LOW,
                evidence=[],  # empty
                testable_prediction="x" * 10,
            )

    def test_playbook_requires_multiple_niches(self):
        """Cross-niche patterns need ≥2 niches to qualify as 'universal'."""
        with pytest.raises(ValidationError):
            PlaybookProposal(
                pattern_text="x" * 20,
                evidence_niches=["gaming"],  # only one
                confidence=HypothesisConfidence.MEDIUM,
            )

    def test_full_report_round_trips(self):
        report = StrategistReport(
            niche_id="ai_creators",
            week_of=date(2026, 6, 29),
            detected_phase=StrategicPhase.GROWTH,
            phase_evidence="Follower count crossed 100 on 2026-06-22; growth trajectory positive.",
            weekly_summary="ai_creators is in early GROWTH phase. " * 3,
        )
        round_tripped = StrategistReport.model_validate_json(report.model_dump_json())
        assert round_tripped.niche_id == report.niche_id
        assert round_tripped.detected_phase == StrategicPhase.GROWTH


class TestWeekOfHelper:
    def test_returns_monday_for_any_day_in_week(self):
        # 2026-06-30 is a Tuesday; Monday of that week is 2026-06-29
        assert week_of(date(2026, 6, 30)) == date(2026, 6, 29)
        # Wednesday
        assert week_of(date(2026, 7, 1)) == date(2026, 6, 29)
        # Sunday
        assert week_of(date(2026, 7, 5)) == date(2026, 6, 29)
        # Monday itself
        assert week_of(date(2026, 6, 29)) == date(2026, 6, 29)

    def test_accepts_datetime(self):
        assert week_of(datetime(2026, 7, 1, 14, 30)) == date(2026, 6, 29)

    def test_defaults_to_today(self):
        # Just ensure it returns a Monday without error
        result = week_of()
        assert result.weekday() == 0


class TestStrategistSkeleton:
    """The skeleton raises NotImplementedError but interfaces must still hold."""

    def test_constructs_with_protocols(self):
        s = Strategist(
            collector=Mock(),
            llm=Mock(),
            persister=Mock(),
        )
        assert s.config.cost_cap_per_run_usd == 1.50  # default
        assert s.config.schema_version == SCHEMA_VERSION

    def test_run_collects_state_then_raises_not_implemented(self):
        """Verifies collector is called before the NotImplementedError stub."""
        collector = Mock()
        collector.collect.return_value = {"some": "state"}
        s = Strategist(collector=collector, llm=Mock(), persister=Mock())

        with pytest.raises(NotImplementedError, match="PR Strategist-1"):
            s.run_for_niche("ai_creators", date(2026, 6, 29))

        collector.collect.assert_called_once_with("ai_creators", date(2026, 6, 29))

    def test_state_collector_failure_returns_none_fail_soft(self):
        """Spec §6: state collector failure must fail-soft (return None, log)."""
        collector = Mock()
        collector.collect.side_effect = RuntimeError("DB down")
        s = Strategist(collector=collector, llm=Mock(), persister=Mock())

        result = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert result is None

    def test_custom_config_overrides_defaults(self):
        config = StrategistConfig(
            cost_cap_per_run_usd=5.00,
            max_input_tokens=50_000,
            require_min_evidence_count=3,
        )
        s = Strategist(collector=Mock(), llm=Mock(), persister=Mock(), config=config)
        assert s.config.cost_cap_per_run_usd == 5.00
        assert s.config.require_min_evidence_count == 3
