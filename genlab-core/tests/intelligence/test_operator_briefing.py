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
             "views_count": 500, "likes_count": 12},  # top
            [{"niche_id": "sports", "severity": "HIGH",
              "check_name": "zero_blueprints", "message": "..."}],  # alerts
            [{"flag_name": "F", "from_state": "25", "to_state": "50",
              "confidence": 0.9, "rationale": "r", "age_h": 3.5}],  # flips
            [{"proposal_type": "arm_disable", "n": 2}],  # strat
            [{"niche_id": "ai_creators", "n_samples": 45,
              "agreement_rate": 0.92}],  # cal
            {"total_usd": 1.23, "n_calls": 42},  # cost
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
            [{"proposal_type": "T", "n": 4}],  # strat (4)
            [], None,
        ])
        fake_client = MagicMock()
        fake_client.generate_report.return_value = SimpleNamespace(
            text="ok", cost_usd=0.0,
        )
        result = generate(conn, client=fake_client)
        assert result.n_pending_flag_flips == 1
        assert result.n_pending_strategist_proposals == 4


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
