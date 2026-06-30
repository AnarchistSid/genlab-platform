"""Tests for the Strategist + supporting modules (PR Strategist-1).

Covers:
- Pydantic schema contracts (round-trip, validation, extra-field rejection)
- Strategist orchestration (happy path + all 4 failure modes)
- Prompt rendering (snapshot + state substitution)
- JsonlPersister (write + read round-trip)
- AnthropicStrategistClient with mocked client factory
- week_of helper

State collector tests are deferred to test_state_collector.py (requires
DB fixtures).
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from genlab_core.intelligence.anthropic_client import (
    STRATEGIST_MODEL,
    AnthropicStrategistClient,
    CallResult,
)
from genlab_core.intelligence.persister import JsonlPersister
from genlab_core.intelligence.prompts import (
    SYSTEM_PROMPT_VERSION,
    build_user_prompt,
    get_system_prompt,
    render_messages,
)
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
from genlab_core.intelligence.strategist import (
    RunOutcome,
    Strategist,
    StrategistConfig,
    week_of,
)
from pydantic import ValidationError

# === Schema contract tests ==========================================


class TestSchemaShape:
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
        assert Proposal.model_validate_json(p.model_dump_json()) == p

    def test_proposal_rejects_unknown_type(self):
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
        with pytest.raises(ValidationError):
            Proposal(
                type=ProposalType.PHASE_SHIFT,
                target="x",
                current=None,
                proposed=None,
                reasoning="ok",
                expected_impact="x" * 20,
                risk=ProposalRisk.LOW,
                urgency=ProposalUrgency.SHIP_NOW,
            )

    def test_proposal_rejects_extra_fields(self):
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
                evidence=[],
                testable_prediction="x" * 10,
            )

    def test_playbook_requires_multiple_niches(self):
        with pytest.raises(ValidationError):
            PlaybookProposal(
                pattern_text="x" * 20,
                evidence_niches=["gaming"],
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


# === Helpers ========================================================


class TestWeekOfHelper:
    def test_returns_monday_for_any_day_in_week(self):
        assert week_of(date(2026, 6, 30)) == date(2026, 6, 29)
        assert week_of(date(2026, 7, 1)) == date(2026, 6, 29)
        assert week_of(date(2026, 7, 5)) == date(2026, 6, 29)
        assert week_of(date(2026, 6, 29)) == date(2026, 6, 29)

    def test_accepts_datetime(self):
        assert week_of(datetime(2026, 7, 1, 14, 30)) == date(2026, 6, 29)

    def test_defaults_to_today(self):
        assert week_of().weekday() == 0


# === Strategist orchestration =======================================


def _valid_llm_output(niche_id: str = "ai_creators") -> str:
    """A minimal LLM response that validates against the schema."""
    return json.dumps(
        {
            "detected_phase": "GROWTH",
            "phase_evidence": "Follower count crossed 100 on 2026-06-22; growth positive.",
            "weekly_summary": "ai_creators in early GROWTH. Bandit converging on style:comparison; consider raising novelty rate.",
            "proposals": [
                {
                    "type": "novelty_rate",
                    "target": "ai_creators.novelty_rate",
                    "current": 0.05,
                    "proposed": 0.15,
                    "reasoning": "Bandit converged on style:comparison; engagement plateaued.",
                    "expected_impact": "1 in 7 publishes uses novelty; expected reward dip ~10%.",
                    "risk": "medium",
                    "urgency": "this_week",
                }
            ],
            "causal_hypotheses": [
                {
                    "pattern": "Posts with style:comparison get 1.5x reward",
                    "hypothesis": "AI audience values direct value comparisons.",
                    "confidence": "medium",
                    "evidence": ["ai_creators:style:comparison n=24 reward=0.154"],
                    "testable_prediction": "Similar comparison styles should also outperform.",
                }
            ],
            "universal_playbook_proposals": [],
        }
    )


def _make_strategist(
    state: dict | None = None,
    llm_text: str | None = None,
    llm_raises: Exception | None = None,
    collector_raises: Exception | None = None,
    persister_raises: Exception | None = None,
    cost: float = 0.05,
) -> tuple[Strategist, Mock, Mock, Mock]:
    """Factory for assembling a Strategist with mocked dependencies."""
    collector = Mock()
    if collector_raises:
        collector.collect.side_effect = collector_raises
    else:
        collector.collect.return_value = state or {"niche_id": "ai_creators"}

    llm = Mock()
    if llm_raises:
        llm.generate_report.side_effect = llm_raises
    else:
        llm.generate_report.return_value = MagicMock(
            text=llm_text or _valid_llm_output(),
            cost_usd=cost,
        )

    persister = Mock()
    if persister_raises:
        persister.persist.side_effect = persister_raises

    s = Strategist(collector=collector, llm=llm, persister=persister)
    return s, collector, llm, persister


class TestStrategistOrchestration:
    def test_happy_path_persists_and_returns_outcome(self):
        s, _, _, persister = _make_strategist()
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "persisted"
        assert outcome.proposals_count == 1
        assert outcome.hypotheses_count == 1
        assert outcome.cost_usd == 0.05
        assert outcome.report_id is not None
        persister.persist.assert_called_once()

    def test_state_collector_failure_returns_outcome_fail_soft(self):
        s, _, _, persister = _make_strategist(collector_raises=RuntimeError("DB down"))
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "state_collect_failed"
        assert outcome.error == "DB down"
        persister.persist.assert_not_called()

    def test_llm_call_failure_returns_outcome_fail_soft(self):
        s, _, _, persister = _make_strategist(llm_raises=RuntimeError("timeout"))
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "llm_call_failed"
        assert "timeout" in outcome.error
        persister.persist.assert_not_called()

    def test_malformed_json_returns_validation_failed(self):
        s, _, _, persister = _make_strategist(llm_text="not json at all")
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "validation_failed"
        persister.persist.assert_not_called()

    def test_invalid_schema_returns_validation_failed(self):
        s, _, _, persister = _make_strategist(
            llm_text=json.dumps({"detected_phase": "BOOTSTRAP"})  # missing required fields
        )
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "validation_failed"
        persister.persist.assert_not_called()

    def test_markdown_fences_are_stripped(self):
        """LLM ignoring 'JSON only' rule by wrapping in ```json fences should still parse."""
        fenced = "```json\n" + _valid_llm_output() + "\n```"
        s, _, _, _ = _make_strategist(llm_text=fenced)
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "persisted"

    def test_persist_failure_returns_outcome(self):
        s, _, _, _ = _make_strategist(persister_raises=RuntimeError("disk full"))
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "persist_failed"
        assert "disk full" in outcome.error

    def test_niche_id_and_week_of_are_trusted_not_llm_controlled(self):
        """Even if LLM outputs different niche_id/week_of, orchestrator overrides."""
        evil_text = json.dumps(
            {
                "niche_id": "EVIL_NICHE",
                "week_of": "1970-01-01",
                "detected_phase": "GROWTH",
                "phase_evidence": "x" * 30,
                "weekly_summary": "x" * 60,
                "proposals": [],
                "causal_hypotheses": [],
                "universal_playbook_proposals": [],
            }
        )
        s, _, _, persister = _make_strategist(llm_text=evil_text)
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "persisted"
        # Verify the persisted report has the trusted identity
        persisted_arg = persister.persist.call_args[0][0]
        assert persisted_arg.niche_id == "ai_creators"
        assert persisted_arg.week_of == date(2026, 6, 29)

    def test_cost_cap_warns_but_persists(self):
        """Cost over cap should still persist — operator decides next step."""
        config = StrategistConfig(cost_cap_per_run_usd=0.01)
        s = Strategist(
            collector=Mock(collect=Mock(return_value={})),
            llm=Mock(
                generate_report=Mock(
                    return_value=MagicMock(text=_valid_llm_output(), cost_usd=5.00)
                )
            ),
            persister=Mock(),
            config=config,
        )
        outcome = s.run_for_niche("ai_creators", date(2026, 6, 29))
        assert outcome.status == "persisted"
        assert outcome.cost_usd == 5.00


# === Prompts ========================================================


class TestPrompts:
    def test_system_prompt_versioned_and_non_empty(self):
        assert SYSTEM_PROMPT_VERSION == 1
        sp = get_system_prompt()
        assert "Strategist" in sp
        assert "JSON" in sp
        assert "OUTPUT RULES" in sp

    def test_build_user_prompt_substitutes_state(self):
        state = {
            "niche_id": "gaming",
            "week_of": "2026-06-29",
            "follower_count": 247,
            "engagement_rate_7d": 1.8,
            "bandit_state": [
                {"arm_id": "style:revelation", "n_plays": 167, "reward": 0.297},
                {"arm_id": "style:patch_news", "n_plays": 89, "reward": 0.087},
            ],
        }
        prompt = build_user_prompt(state)
        assert "NICHE: gaming" in prompt
        assert "247" in prompt
        assert "style:revelation" in prompt
        assert "0.297" in prompt
        assert "schema_version=1" in prompt

    def test_build_user_prompt_handles_missing_fields(self):
        """Missing keys → 'unknown' so LLM has explicit signal."""
        prompt = build_user_prompt({"niche_id": "anime"})
        assert "NICHE: anime" in prompt
        assert "unknown" in prompt
        assert "(no bandit state available)" in prompt
        assert "(no recent publishes)" in prompt

    def test_render_messages_returns_user_role(self):
        msgs = render_messages({"niche_id": "ai_creators"})
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "NICHE: ai_creators" in msgs[0]["content"]


# === Persister ======================================================


class TestJsonlPersister:
    def test_write_then_read_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            persister = JsonlPersister(tmp)
            report = StrategistReport(
                niche_id="anime",
                week_of=date(2026, 6, 29),
                detected_phase=StrategicPhase.BOOTSTRAP,
                phase_evidence="Follower count is 42 — clearly in bootstrap phase.",
                weekly_summary="Anime is in pure bootstrap. Test data only. " * 2,
            )
            persister.persist(report)
            reports = persister.list_reports("anime")
            assert len(reports) == 1
            assert reports[0].niche_id == "anime"
            assert reports[0].detected_phase == StrategicPhase.BOOTSTRAP

    def test_multiple_reports_appended_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            persister = JsonlPersister(tmp)
            r1 = StrategistReport(
                niche_id="gaming",
                week_of=date(2026, 6, 22),
                detected_phase=StrategicPhase.GROWTH,
                phase_evidence="Week 1 evidence — long enough to satisfy min length.",
                weekly_summary="Week 1 summary that is long enough. " * 3,
            )
            r2 = StrategistReport(
                niche_id="gaming",
                week_of=date(2026, 6, 29),
                detected_phase=StrategicPhase.OPTIMIZE,
                phase_evidence="Week 2 evidence — also long enough to pass.",
                weekly_summary="Week 2 summary that is long enough. " * 3,
            )
            persister.persist(r1)
            persister.persist(r2)
            reports = persister.list_reports("gaming")
            assert len(reports) == 2
            # Newest first by run_at
            assert reports[0].run_at >= reports[1].run_at

    def test_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            persister = JsonlPersister(tmp)
            assert persister.list_reports("nonexistent_niche") == []

    def test_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            persister = JsonlPersister(tmp)
            # Write a malformed line first
            bad_file = Path(tmp) / "movies.jsonl"
            bad_file.write_text("not json\n")
            # Then add a good one
            report = StrategistReport(
                niche_id="movies",
                week_of=date(2026, 6, 29),
                detected_phase=StrategicPhase.GROWTH,
                phase_evidence="Decent evidence string that meets the min length.",
                weekly_summary="A summary that is sufficiently long to pass. " * 3,
            )
            persister.persist(report)
            reports = persister.list_reports("movies")
            assert len(reports) == 1  # bad line skipped, good one kept


# === AnthropicStrategistClient ======================================


class TestAnthropicClient:
    def test_uses_injected_factory(self):
        """Tests can inject a mock client_factory; no real API call."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"some": "json"}')]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create.return_value = mock_response

        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("system", "user")

        assert isinstance(result, CallResult)
        assert result.text == '{"some": "json"}'
        assert result.model == STRATEGIST_MODEL
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        # cost = (100*3 + 50*15) / 1M = 0.001050
        assert result.cost_usd == pytest.approx(0.00105, rel=1e-3)
        mock_client.messages.create.assert_called_once()

    def test_transient_error_retries_once(self):
        mock_client = MagicMock()
        # First call raises a "transient" looking error, second succeeds
        success_response = MagicMock()
        success_response.content = [MagicMock(text="ok")]
        success_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        class APIConnectionError(Exception):
            pass

        mock_client.messages.create.side_effect = [
            APIConnectionError("network blip"),
            success_response,
        ]
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("system", "user")
        assert result.text == "ok"
        assert mock_client.messages.create.call_count == 2

    def test_non_transient_error_propagates_immediately(self):
        mock_client = MagicMock()

        class AuthError(Exception):
            pass

        mock_client.messages.create.side_effect = AuthError("bad api key")
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with pytest.raises(AuthError):
            client.generate_report("system", "user")
        # Only called once — no retry on non-transient
        assert mock_client.messages.create.call_count == 1


# === Config =========================================================


class TestStrategistConfig:
    def test_default_config_uses_schema_version_constant(self):
        config = StrategistConfig()
        assert config.schema_version == SCHEMA_VERSION
        assert config.cost_cap_per_run_usd == 1.50

    def test_custom_config_overrides(self):
        config = StrategistConfig(
            cost_cap_per_run_usd=5.00,
            max_input_tokens=50_000,
            require_min_evidence_count=3,
        )
        assert config.cost_cap_per_run_usd == 5.00
        assert config.require_min_evidence_count == 3


# === RunOutcome ====================================================


class TestRunOutcome:
    def test_outcome_default_values(self):
        outcome = RunOutcome(niche_id="ai", week_of=date(2026, 6, 29), status="started")
        assert outcome.cost_usd == 0.0
        assert outcome.proposals_count == 0
        assert outcome.error is None
