"""Pin the Phase 1.B LLM reviewer wire in auto_accept_strategist_
proposals.

Tests focus on the runner-level logic (budget gate, accept/reject
routing, source-tag stamps). The reviewer parse + system prompt shape
are pinned separately in test_llm_proposal_reviewer.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "auto_accept_strategist_proposals.py"


@pytest.fixture
def script_mod():
    spec = importlib.util.spec_from_file_location("aasp", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aasp"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("aasp", None)


class TestDailyLLMSpendGate:
    def test_no_llm_run_cost_table_returns_zero(self, script_mod):
        """Query error → fail-open (return 0.0) so a broken cost table
        can't silently block auto-accept."""
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("relation does not exist")
        assert script_mod._daily_llm_spend_usd(conn) == 0.0

    def test_returns_summed_spend(self, script_mod):
        """Normal path: returns the sum from the query result."""
        cur = MagicMock()
        cur.fetchone.return_value = {"spend": 0.42}
        conn = MagicMock()
        conn.execute.return_value = cur
        assert script_mod._daily_llm_spend_usd(conn) == pytest.approx(0.42)

    def test_null_row_returns_zero(self, script_mod):
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn = MagicMock()
        conn.execute.return_value = cur
        assert script_mod._daily_llm_spend_usd(conn) == 0.0

    def test_budget_constant_is_bounded(self, script_mod):
        """Sanity: daily cap is $0.50 or less by design (bounded
        blast radius). If someone bumps this above $2.00 without
        updating this test, they should think about why."""
        assert script_mod._LLM_REVIEWER_DAILY_BUDGET_USD <= 2.00
        assert script_mod._LLM_REVIEWER_DAILY_BUDGET_USD >= 0.10


class TestStateSnapshot:
    def test_includes_niche_id(self, script_mod):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("no bandit table")
        snap = script_mod._build_state_snapshot(conn, "anime")
        assert snap["niche_id"] == "anime"

    def test_populates_arm_counts_on_success(self, script_mod):
        cur = MagicMock()
        cur.fetchone.return_value = {"active_arms": 45, "total_arms": 80}
        conn = MagicMock()
        conn.execute.return_value = cur
        snap = script_mod._build_state_snapshot(conn, "gaming")
        assert snap["active_arms"] == 45
        assert snap["total_arms"] == 80

    def test_survives_db_error(self, script_mod):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("db down")
        snap = script_mod._build_state_snapshot(conn, "sports")
        assert snap == {"niche_id": "sports"}


class TestAppendHelpers:
    def test_append_llm_accepted_updates_extra(self, script_mod):
        """LLM-accepted appends to proposals_accepted AND writes the
        llm_reviewer_accepted_indices marker in extra so audit can
        distinguish LLM from heuristic accepts."""
        conn = MagicMock()
        script_mod._append_llm_accepted(conn, "report-uuid", [3, 5])
        # One UPDATE statement issued
        assert conn.execute.call_count == 1
        sql = conn.execute.call_args.args[0]
        assert "UPDATE strategist_reports" in sql
        assert "proposals_accepted" in sql
        assert "llm_reviewer_accepted_indices" in sql
        assert "llm_reviewer_last_run_at" in sql

    def test_append_llm_rejected_writes_rejected(self, script_mod):
        """LLM-rejected appends to proposals_rejected (not accepted)."""
        conn = MagicMock()
        script_mod._append_llm_rejected(conn, "report-uuid", [1])
        assert conn.execute.call_count == 1
        sql = conn.execute.call_args.args[0]
        assert "proposals_rejected" in sql
        assert "llm_reviewer_rejected_indices" in sql

    def test_now_iso_returns_iso_string(self, script_mod):
        s = script_mod._now_iso()
        # ISO 8601 with UTC offset marker
        assert "T" in s
        assert s.endswith("+00:00") or s.endswith("Z")


class TestLLMAnthropicClientAdapter:
    def test_review_returns_text_on_success(self, script_mod):
        """Adapter unwraps the CallResult.text field the Reviewer
        expects."""
        with patch(
            "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
        ) as fake_cls:
            fake_instance = MagicMock()
            fake_result = MagicMock()
            fake_result.text = '{"decision":"accept","confidence":0.9,"reason":"ok"}'
            fake_instance.generate_report.return_value = fake_result
            fake_cls.return_value = fake_instance
            client = script_mod._LLMAnthropicClient()
            out = client.review("sys prompt", "user prompt")
        assert '"decision":"accept"' in out

    def test_review_raises_on_underlying_failure(self, script_mod):
        with patch(
            "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
        ) as fake_cls:
            fake_instance = MagicMock()
            fake_instance.generate_report.side_effect = RuntimeError("429")
            fake_cls.return_value = fake_instance
            client = script_mod._LLMAnthropicClient()
            with pytest.raises(RuntimeError, match="anthropic call failed"):
                client.review("sys", "user")
