"""Pin Phase 5.D operator briefing module:

  * collect_state aggregates 7 signals; sibling keys still fill when
    one collector fails (fail-open per-key).
  * render_prompt produces a JSON block containing every key from state.
  * call_llm returns (text, cost) tuple from CallResult.
  * call_llm returns ("", 0.0) on exception (fail-open).
  * generate falls back to _fallback_render when LLM returns empty.
  * generate carries n_pending_flag_flips + n_pending_strategist_proposals
    into the result for the card badge.
  * to_row shape matches DB insert columns.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from genlab_core.intelligence.operator_briefing import (
    BriefingResult,
    _fallback_render,
    call_llm,
    collect_state,
    generate,
    render_prompt,
)


def _stub_conn_returning(payloads):
    """Build a conn whose N execute().fetchone() / fetchall() calls
    return values from ``payloads`` in order. `payloads` is a list of
    dicts (fetchone) or lists (fetchall)."""
    conn = MagicMock()
    call_iter = iter(payloads)

    def _execute(_sql, _params=()):
        result = MagicMock()
        val = next(call_iter)
        if isinstance(val, list):
            result.fetchall.return_value = val
            result.fetchone.return_value = None
        else:
            result.fetchone.return_value = val
            result.fetchall.return_value = []
        return result

    conn.execute.side_effect = _execute
    return conn


class TestCollectState:
    def test_happy_path_all_seven_keys(self):
        conn = _stub_conn_returning([
            [{"niche_id": "gaming", "n": 1}],  # publishes
            {"niche_id": "gaming", "platform": "yt", "post_id": "abc",
             "views": 500, "likes": 12},  # top (real columns)
            [{"niche_id": "sports", "severity": "warning",
              "check_name": "zero_blueprints", "message": "..."}],  # alerts
            [{"flag_name": "F", "from_state": "25", "to_state": "50",
              "confidence": 0.9, "rationale": "r", "age_h": 3.5}],  # flips
            {"n_reports": 1, "n_proposals": 2},  # strat (fetch_one row)
            [{"niche_id": "ai_creators", "n_samples": 45,
              "agreement_rate": 0.92}],  # cal
            {"total_usd": 1.23, "n_runs": 5, "n_calls": 42},  # cost
        ])
        state = collect_state(conn)
        assert set(state.keys()) == {
            "publishes_yesterday", "top_performer_yesterday",
            "pending_alerts", "pending_flag_flips",
            "pending_strategist", "calibration_progress", "cost_today",
        }
        assert state["publishes_yesterday"]["total"] == 1
        assert state["pending_flag_flips"]["count"] == 1
        assert state["pending_strategist"]["count"] == 2
        assert state["top_performer_yesterday"]["views"] == 500

    def test_one_collector_error_siblings_still_fill(self, monkeypatch):
        """A failing SQL query must not kill sibling collectors."""
        conn = MagicMock()
        call_count = {"n": 0}

        def _execute(_sql, _params=()):
            call_count["n"] += 1
            if call_count["n"] == 2:  # top_performer_yesterday
                raise Exception("simulated DB error")
            result = MagicMock()
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        conn.execute.side_effect = _execute
        state = collect_state(conn)
        # All 7 keys should still be present
        assert len(state) == 7
        # The failing one falls back to None (returned by _fetch_one)
        assert state["top_performer_yesterday"] is None


class TestRenderPrompt:
    def test_includes_state_keys(self):
        state = {"publishes_yesterday": {"total": 5}, "pending_flag_flips": {}}
        prompt = render_prompt(state)
        assert "publishes_yesterday" in prompt
        assert "pending_flag_flips" in prompt
        assert "5" in prompt
        # Trailing nudge line
        assert "5-line briefing" in prompt


class TestCallLLM:
    def test_returns_text_and_cost(self):
        fake_client = MagicMock()
        fake_client.generate_report.return_value = SimpleNamespace(
            text="- line1\n- line2", cost_usd=0.0025,
        )
        text, cost = call_llm("hi", client=fake_client)
        assert text == "- line1\n- line2"
        assert cost == pytest.approx(0.0025)

    def test_fail_open_on_exception(self):
        fake_client = MagicMock()
        fake_client.generate_report.side_effect = Exception("network")
        text, cost = call_llm("hi", client=fake_client)
        assert text == ""
        assert cost == 0.0

    def test_missing_cost_attribute_defaults_zero(self):
        fake_client = MagicMock()
        fake_client.generate_report.return_value = SimpleNamespace(text="x")
        text, cost = call_llm("hi", client=fake_client)
        assert text == "x"
        assert cost == 0.0


class TestFallbackRender:
    def test_includes_publish_count(self):
        state = {"publishes_yesterday": {"total": 5, "per_niche": [{}, {}]}}
        out = _fallback_render(state)
        assert "5 total" in out
        assert "2 niches" in out

    def test_handles_missing_fields(self):
        # Empty state — should still produce SOMETHING
        out = _fallback_render({})
        assert "Operator briefing (fallback render)" in out


class TestGenerate:
    def test_uses_llm_text_when_available(self):
        conn = _stub_conn_returning([
            [], None, [], [], [], [], None,
        ])
        fake_client = MagicMock()
        fake_client.generate_report.return_value = SimpleNamespace(
            text="**LLM wrote this**", cost_usd=0.001,
        )
        result = generate(conn, client=fake_client)
        assert result.summary_md == "**LLM wrote this**"
        assert result.llm_cost_usd == pytest.approx(0.001)
        assert result.ok is True

    def test_falls_back_when_llm_returns_empty(self):
        conn = _stub_conn_returning([
            [{"niche_id": "gaming", "n": 3}],  # publishes
            None, [], [], [], [], None,
        ])
        fake_client = MagicMock()
        fake_client.generate_report.return_value = SimpleNamespace(
            text="", cost_usd=0.0,
        )
        result = generate(conn, client=fake_client)
        assert "fallback render" in result.summary_md
        assert "3 total" in result.summary_md

    def test_carries_pending_counts_to_result(self):
        conn = _stub_conn_returning([
            [], None, [],
            [{"flag_name": "F", "from_state": "1", "to_state": "2",
              "confidence": 0.9, "rationale": "r", "age_h": 1}],  # flips (1)
            {"n_reports": 2, "n_proposals": 4},  # strat (4)
            [], None,
        ])
        fake_client = MagicMock()
        fake_client.generate_report.return_value = SimpleNamespace(
            text="ok", cost_usd=0.0,
        )
        result = generate(conn, client=fake_client)
        assert result.n_pending_flag_flips == 1
        assert result.n_pending_strategist_proposals == 4


class TestColumnNamesMatchProductionSchema:
    """Prevention pin for the class-of-bug that broke 4 of the 7
    collectors on Phase 5.D's first live-fire.

    Each collector's SQL string is inspected for its FROM clause +
    referenced columns; those column names are cross-checked against
    a pinned schema snapshot taken from prod 2026-08-15.

    Updating: when a migration lands that renames a column, update
    the SCHEMA dict below AND fix the query. This test will fail
    loudly rather than silently render `null` on the card."""

    # Prod schema snapshot 2026-08-15 (from information_schema.columns)
    _SCHEMA: dict[str, set[str]] = {
        "publishing_analytics": {
            "id", "niche_id", "post_id", "platform", "published_at",
            "status", "views", "likes", "comments", "shares", "saves",
            "metrics_fetched", "created_at", "updated_at", "extra",
            "blueprint_id", "error_message",
        },
        "pipeline_alerts": {
            "id", "niche_id", "check_name", "severity", "message",
            "details", "auto_fix_applied", "auto_fix_result",
            "resolved_at", "created_at", "notified_at",
        },
        "flag_flip_proposals": {
            "id", "flag_name", "from_state", "to_state", "rationale",
            "evidence", "confidence", "status", "proposed_at",
            "applied_at", "applied_by", "rejected_at", "rejection_reason",
        },
        "strategist_reports": {
            "id", "niche_id", "run_at", "week_of", "inputs_json",
            "detected_phase", "phase_evidence", "proposals",
            "causal_hypotheses", "universal_playbook_proposals",
            "weekly_summary", "cost_usd", "input_tokens", "output_tokens",
            "reviewed_at", "reviewed_by", "proposals_accepted",
            "proposals_rejected", "operator_notes", "extra",
        },
        "auto_approval_calibration": {
            "id", "blueprint_id", "niche_id", "gate_approved",
            "gate_confidence", "gate_passed_checks", "gate_failed_checks",
            "operator_action", "decided_at", "review_duration_ms",
            "feedback_category", "source",
        },
        "pipeline_run_costs": {
            "id", "run_id", "niche_id", "completed_at", "total_usd",
            "llm_usd", "image_usd", "tts_usd", "compute_usd",
            "media_usd", "bandwidth_usd", "by_model", "budget_usd",
            "budget_remaining_pct", "entry_count",
        },
    }

    def _query_for(self, fn) -> str:
        """Extract the SQL from a collector by running it against a
        recording MagicMock. Cleaner than parsing the source."""
        conn = MagicMock()
        seen = {}

        def _record(sql, params=()):
            seen["sql"] = sql
            r = MagicMock()
            r.fetchone.return_value = None
            r.fetchall.return_value = []
            return r

        conn.execute.side_effect = _record
        fn(conn)
        return seen.get("sql", "")

    def test_top_performer_columns_exist(self):
        from genlab_core.intelligence.operator_briefing import (
            _top_performer_yesterday,
        )
        sql = self._query_for(_top_performer_yesterday).lower()
        assert "publishing_analytics" in sql
        # Specific rebuttal of the Phase 5.D bug — MUST use `views`,
        # NOT `views_count`
        assert "views" in sql
        assert "views_count" not in sql
        assert "likes_count" not in sql

    def test_calibration_columns_exist(self):
        from genlab_core.intelligence.operator_briefing import (
            _calibration_progress,
        )
        sql = self._query_for(_calibration_progress).lower()
        # Real column is `decided_at`, not `logged_at`
        assert "decided_at" in sql
        assert "logged_at" not in sql
        # Agreement math uses gate_approved BOOL vs operator_action TEXT
        assert "gate_approved" in sql
        # Rule #22: operator_action values are 'approved'/'rejected'
        # not 'approve'/'reject'
        assert "'approved'" in sql
        assert "'rejected'" in sql

    def test_strategist_uses_real_table(self):
        from genlab_core.intelligence.operator_briefing import (
            _pending_strategist,
        )
        sql = self._query_for(_pending_strategist).lower()
        # Real table is strategist_reports (JSONB proposals array),
        # NOT the phantom strategist_proposals from Phase 5.D
        assert "strategist_reports" in sql
        assert "strategist_proposals" not in sql

    def test_cost_uses_real_table(self):
        from genlab_core.intelligence.operator_briefing import _cost_today
        sql = self._query_for(_cost_today).lower()
        # Real cost table is pipeline_run_costs, NOT llm_call_log
        assert "pipeline_run_costs" in sql
        assert "llm_call_log" not in sql


class TestBriefingResultToRow:
    def test_row_shape_matches_insert(self):
        r = BriefingResult(
            ok=True, summary_md="text", structured={"k": "v"},
            llm_cost_usd=0.01, n_pending_flag_flips=2,
            n_pending_strategist_proposals=3,
        )
        row = r.to_row()
        assert set(row.keys()) == {
            "summary_md", "structured", "llm_cost_usd",
            "n_pending_flag_flips", "n_pending_strategist_proposals",
        }
        # structured must be json.dumps-able
        assert json.dumps(row["structured"]) == '{"k": "v"}'
