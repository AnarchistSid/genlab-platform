"""Phase 3.D session 3 consumer-wire pins (2026-08-14) — active
experiments into Strategist prompt.

Two parts:

* ``PostgresStateCollector._active_experiments(niche_id)`` reads
  the ``auto_experiments`` table for running + last-5 verdicts.
  Fail-open: DB error → empty dict.

* ``prompts._format_active_experiments(summary)`` renders the
  section. Empty summary renders explicit "no active" line so
  the LLM sees the missing-signal state.

Pins:

* Empty summary renders cold-start line
* Running rendered as "N vs M · Xd / Yd"
* Recent verdicts rendered as "N vs M → VERDICT (p_b=X)"
* Collector fail-opens to empty dict on DB error
* Full-state ``collect()`` always includes ``active_experiments`` key
"""
from __future__ import annotations

from unittest.mock import MagicMock


class TestFormatterEmpty:
    def test_none_summary(self):
        from genlab_core.intelligence.prompts import _format_active_experiments
        out = _format_active_experiments(None)
        assert "no experiment data" in out

    def test_empty_dict(self):
        from genlab_core.intelligence.prompts import _format_active_experiments
        out = _format_active_experiments({"running": [], "recent_verdicts": []})
        assert "no active or recent experiments" in out


class TestFormatterRunning:
    def test_renders_arm_pair(self):
        from genlab_core.intelligence.prompts import _format_active_experiments
        out = _format_active_experiments({
            "running": [
                {"arms": ["question_hook", "bold_claim"],
                 "age_days": 3.5, "duration_days": 7},
            ],
            "recent_verdicts": [],
        })
        assert "question_hook vs bold_claim" in out
        assert "3.5d / 7d" in out

    def test_only_first_two_arms(self):
        """Guards against 3+ arm spec pollution — display "A vs B"
        even if spec has ['A', 'B', 'C']."""
        from genlab_core.intelligence.prompts import _format_active_experiments
        out = _format_active_experiments({
            "running": [
                {"arms": ["A", "B", "C"], "age_days": 1, "duration_days": 7},
            ],
            "recent_verdicts": [],
        })
        assert "A vs B" in out
        assert "vs C" not in out


class TestFormatterVerdicts:
    def test_recent_verdict_shape(self):
        from genlab_core.intelligence.prompts import _format_active_experiments
        out = _format_active_experiments({
            "running": [],
            "recent_verdicts": [
                {"arms": ["A", "B"], "verdict": "B_WINS",
                 "prob_b_beats_a": 0.97, "status": "completed"},
            ],
        })
        assert "B_WINS" in out
        assert "p_b=0.97" in out

    def test_missing_prob_renders_question(self):
        from genlab_core.intelligence.prompts import _format_active_experiments
        out = _format_active_experiments({
            "running": [],
            "recent_verdicts": [
                {"arms": ["A", "B"], "verdict": "INSUFFICIENT_SAMPLES",
                 "prob_b_beats_a": None, "status": "discarded"},
            ],
        })
        assert "p_b=?" in out


class TestCollectorFailOpen:
    def test_db_error_returns_empty_dict(self):
        from genlab_core.intelligence.state_collector import PostgresStateCollector
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        collector = PostgresStateCollector(conn)
        result = collector._active_experiments("gaming")
        assert isinstance(result, dict)
        assert result == {"running": [], "recent_verdicts": []}

    def test_normalizes_dict_rows(self):
        from genlab_core.intelligence.state_collector import PostgresStateCollector
        conn = MagicMock()
        # First execute = running query, second = verdicts query
        conn.execute.return_value.fetchall.side_effect = [
            [{
                "id": "exp1",
                "spec": {"arms": ["hook_A", "hook_B"], "duration_days": 7},
                "started_at": None,
                "age_seconds": 172800,  # 2 days
            }],
            [],
        ]
        collector = PostgresStateCollector(conn)
        result = collector._active_experiments("gaming")
        assert len(result["running"]) == 1
        assert result["running"][0]["arms"] == ["hook_A", "hook_B"]
        assert result["running"][0]["age_days"] == 2.0


class TestFullStateIncludesKey:
    def test_active_experiments_always_present(self):
        from datetime import date
        from genlab_core.intelligence.state_collector import PostgresStateCollector
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        collector = PostgresStateCollector(conn)
        state = collector.collect("gaming", date(2026, 8, 14))
        assert "active_experiments" in state
        assert state["active_experiments"] == {
            "running": [], "recent_verdicts": [],
        }
