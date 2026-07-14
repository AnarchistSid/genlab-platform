"""Tests for PostgresPersister.

Uses a mock psycopg connection — no real Postgres needed. Verifies the SQL
shape + parameter binding + idempotency contract. Integration tests against
a real Postgres land in PR Strategist-2 when the cron + dashboard need them.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

from genlab_core.intelligence.persister import PostgresPersister
from genlab_core.intelligence.proposal_schema import (
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


def _make_full_report() -> StrategistReport:
    """A report with all three list sections populated, for serialization tests."""
    return StrategistReport(
        niche_id="ai_creators",
        week_of=date(2026, 6, 29),
        detected_phase=StrategicPhase.GROWTH,
        phase_evidence="Follower count crossed 100 on 2026-06-22; growth positive.",
        weekly_summary="ai_creators in early GROWTH. " * 4,
        proposals=[
            Proposal(
                type=ProposalType.NOVELTY_RATE,
                target="ai_creators.novelty_rate",
                current=0.05,
                proposed=0.15,
                reasoning="Bandit converged on style:comparison; engagement plateaued.",
                expected_impact="1 in 7 publishes uses novelty; expected reward dip ~10%.",
                risk=ProposalRisk.MEDIUM,
                urgency=ProposalUrgency.THIS_WEEK,
            )
        ],
        causal_hypotheses=[
            CausalHypothesis(
                pattern="style:comparison gets 1.5x reward vs style:question",
                hypothesis="AI audience values direct value comparisons.",
                confidence=HypothesisConfidence.MEDIUM,
                evidence=["ai_creators:style:comparison n=24 reward=0.154"],
                testable_prediction="Similar comparison styles should also outperform.",
            )
        ],
        universal_playbook_proposals=[
            PlaybookProposal(
                pattern_text="Posts at 06:30 UTC outperform 18:00 UTC by 2.1x consistently",
                evidence_niches=["ai_creators", "gaming", "anime"],
                confidence=HypothesisConfidence.HIGH,
            )
        ],
    )


class TestPostgresPersisterPersist:
    def test_persist_executes_upsert_with_correct_params(self):
        conn = MagicMock()
        persister = PostgresPersister(conn)
        report = _make_full_report()

        persister.persist(report)

        # Verify execute was called once with UPSERT SQL
        conn.execute.assert_called_once()
        sql_arg, params_arg = conn.execute.call_args[0]
        assert "INSERT INTO strategist_reports" in sql_arg
        assert "ON CONFLICT (niche_id, week_of) DO UPDATE" in sql_arg

        # Verify key params
        assert params_arg["niche_id"] == "ai_creators"
        assert params_arg["week_of"] == date(2026, 6, 29)
        assert params_arg["detected_phase"] == "GROWTH"
        assert params_arg["phase_evidence"].startswith("Follower count crossed")

        # Verify commit was called after execute
        conn.commit.assert_called_once()

    def test_persist_serializes_proposals_as_jsonb_array(self):
        conn = MagicMock()
        persister = PostgresPersister(conn)
        report = _make_full_report()

        persister.persist(report)
        params_arg = conn.execute.call_args[0][1]

        # Each list-typed field is a JSON-encoded array (not a wrapper object)
        proposals_list = json.loads(params_arg["proposals"])
        assert isinstance(proposals_list, list)
        assert len(proposals_list) == 1
        assert proposals_list[0]["type"] == "novelty_rate"
        assert proposals_list[0]["target"] == "ai_creators.novelty_rate"

        hyps_list = json.loads(params_arg["causal_hypotheses"])
        assert isinstance(hyps_list, list)
        assert len(hyps_list) == 1
        assert "style:comparison" in hyps_list[0]["pattern"]

        playbook_list = json.loads(params_arg["universal_playbook_proposals"])
        assert isinstance(playbook_list, list)
        assert len(playbook_list) == 1
        assert "ai_creators" in playbook_list[0]["evidence_niches"]

    def test_persist_handles_empty_lists(self):
        """A report with no proposals / no hypotheses still persists cleanly."""
        conn = MagicMock()
        persister = PostgresPersister(conn)
        report = StrategistReport(
            niche_id="anime",
            week_of=date(2026, 6, 29),
            detected_phase=StrategicPhase.BOOTSTRAP,
            phase_evidence="Follower count is 42 — clearly in bootstrap phase.",
            weekly_summary="Anime is in pure bootstrap. No proposals this week. " * 2,
        )
        persister.persist(report)
        params_arg = conn.execute.call_args[0][1]
        assert json.loads(params_arg["proposals"]) == []
        assert json.loads(params_arg["causal_hypotheses"]) == []
        assert json.loads(params_arg["universal_playbook_proposals"]) == []

    def test_persist_passes_inputs_snapshot_through(self):
        """The state snapshot passed at construction is persisted as inputs_json."""
        conn = MagicMock()
        inputs = {"follower_count": 247, "engagement_rate_7d": 1.8}
        persister = PostgresPersister(conn, inputs_snapshot=inputs)
        report = _make_full_report()
        persister.persist(report)
        params_arg = conn.execute.call_args[0][1]
        assert json.loads(params_arg["inputs_json"]) == inputs

    def test_persist_defaults_inputs_to_empty_dict(self):
        conn = MagicMock()
        persister = PostgresPersister(conn)  # no inputs_snapshot
        report = _make_full_report()
        persister.persist(report)
        params_arg = conn.execute.call_args[0][1]
        assert json.loads(params_arg["inputs_json"]) == {}


class TestPostgresPersisterRead:
    def test_get_latest_returns_none_when_no_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        persister = PostgresPersister(conn)
        result = persister.get_latest("ai_creators")
        assert result is None

    def test_get_latest_hydrates_dict_row(self):
        """psycopg with dict_row factory returns dict-like rows."""
        conn = MagicMock()
        report = _make_full_report()
        # Simulate a dict_row by returning a dict from fetchone
        mock_row = {
            "id": report.id,
            "niche_id": "ai_creators",
            "week_of": date(2026, 6, 29),
            "run_at": report.run_at,
            "detected_phase": "GROWTH",
            "phase_evidence": "Follower count crossed 100 on 2026-06-22; growth positive.",
            "proposals": [],
            "causal_hypotheses": [],
            "universal_playbook_proposals": [],
            "weekly_summary": "ai_creators in early GROWTH. " * 4,
        }
        conn.execute.return_value.fetchone.return_value = mock_row
        persister = PostgresPersister(conn)
        result = persister.get_latest("ai_creators")
        assert result is not None
        assert result.niche_id == "ai_creators"
        assert result.detected_phase == StrategicPhase.GROWTH

    def test_list_unreviewed_returns_empty_list_when_no_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        persister = PostgresPersister(conn)
        result = persister.list_unreviewed()
        assert result == []

    def test_list_unreviewed_respects_limit_param(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        persister = PostgresPersister(conn)
        persister.list_unreviewed(limit=5)
        sql_arg, params_arg = conn.execute.call_args[0]
        assert "LIMIT %s" in sql_arg
        assert params_arg == (5,)


class TestPostgresPersisterContract:
    def test_satisfies_report_persister_protocol(self):
        """Verify PostgresPersister can substitute for the Protocol in Strategist."""
        from genlab_core.intelligence.strategist import Strategist

        conn = MagicMock()
        persister = PostgresPersister(conn)

        # If Protocol doesn't match, Strategist instantiation works regardless
        # (Python Protocols are structural), but the persist() call must succeed
        strategist = Strategist(collector=MagicMock(), llm=MagicMock(), persister=persister)
        assert strategist.persister is persister


class TestPostgresPersisterCostTelemetry:
    """Pin the 2026-07-14 cost-tracking fix.

    Prior bug: ``persister.py:135-137`` hardcoded ``cost_usd``,
    ``input_tokens``, ``output_tokens`` to ``None`` with a "PR
    Strategist-2 will wire that" TODO comment. Every strategist_reports
    row from 2026-04-XX through 2026-07-12 had NULL cost columns —
    operator had no visibility into weekly Strategist LLM spend.

    Fix: extend ``persist()`` signature with optional cost kwargs +
    thread them from ``Strategist.run_for_niche`` via ``call_result``.
    These pins prevent the same NULL-hardcode regression.
    """

    def _base_report(self):
        return _make_full_report()

    def test_persist_writes_cost_usd_when_provided(self):
        conn = MagicMock()
        persister = PostgresPersister(conn)
        persister.persist(
            self._base_report(),
            cost_usd=0.0421,
            input_tokens=8500,
            output_tokens=1200,
        )
        _, params = conn.execute.call_args[0]
        assert params["cost_usd"] == 0.0421
        assert params["input_tokens"] == 8500
        assert params["output_tokens"] == 1200

    def test_persist_defaults_to_none_when_kwargs_omitted(self):
        """Backward-compat: callers that don't pass cost still work."""
        conn = MagicMock()
        persister = PostgresPersister(conn)
        persister.persist(self._base_report())
        _, params = conn.execute.call_args[0]
        assert params["cost_usd"] is None
        assert params["input_tokens"] is None
        assert params["output_tokens"] is None

    def test_persist_accepts_zero_cost(self):
        """Zero cost is meaningful data (LLM cache hit) — NOT the None sentinel."""
        conn = MagicMock()
        persister = PostgresPersister(conn)
        persister.persist(
            self._base_report(),
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
        )
        _, params = conn.execute.call_args[0]
        assert params["cost_usd"] == 0.0  # explicitly 0.0, not None
        assert params["input_tokens"] == 0
        assert params["output_tokens"] == 0

    def test_strategist_passes_call_result_telemetry_through(self):
        """End-to-end pin: Strategist.run_for_niche reads cost from call_result
        and passes it to persister.persist."""
        from datetime import date
        from unittest.mock import MagicMock

        from genlab_core.intelligence.strategist import Strategist

        # Build a full report as valid JSON for the LLM mock
        report = self._base_report()
        report_json = report.model_dump_json()

        call_result = MagicMock()
        call_result.text = report_json
        call_result.cost_usd = 0.0678
        call_result.input_tokens = 12345
        call_result.output_tokens = 890

        llm = MagicMock()
        llm.generate_report.return_value = call_result

        collector = MagicMock()
        collector.collect.return_value = {"followers": None}

        persister = MagicMock()
        strategist = Strategist(collector=collector, llm=llm, persister=persister)
        outcome = strategist.run_for_niche("ai_creators", date(2026, 6, 29))

        # persist() must have been called with cost_usd + tokens as kwargs
        persister.persist.assert_called_once()
        _, kwargs = persister.persist.call_args
        assert kwargs["cost_usd"] == 0.0678
        assert kwargs["input_tokens"] == 12345
        assert kwargs["output_tokens"] == 890

        # RunOutcome mirrors the same cost — already worked pre-fix but pin it
        assert outcome.cost_usd == 0.0678
