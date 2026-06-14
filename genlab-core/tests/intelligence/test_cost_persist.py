"""Pins for cost_persist.persist_run_cost.

Closes the cost-tracking visibility gap from the 2026-06-13 deep-dive:
CostAccumulator dumped per-run totals to JSON but never persisted to
Postgres, so the operator had to grep 50 JSON files to answer
"what did gaming cost this week."

This module ships the persistence layer + tests its safety:
  1. **Best-effort fail-open** — DB failure NEVER raises to caller.
     The on-disk run_report.json remains the source of truth; the
     table is an aggregated convenience.
  2. **Idempotent UPSERT** — re-running RunReport on the same
     (run_id, niche_id) updates the row, not inserts a duplicate.
  3. **Defensive against malformed summary** — wrong types, missing
     keys, None values → row still writes with zeros (so the missing
     niche shows up in the dashboard rather than silently disappearing).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.intelligence.cost_persist import persist_run_cost


@pytest.fixture
def mock_db(monkeypatch):
    """Patch psycopg.connect so tests don't need a live DB."""
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False

    monkeypatch.setattr(
        "psycopg.connect",
        MagicMock(return_value=mock_conn),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    return {"conn": mock_conn, "cur": mock_cur}


class TestInputValidation:
    """Pre-DB checks — fail fast on bad inputs without opening a connection."""

    def test_empty_run_id_returns_false(self, mock_db):
        ok = persist_run_cost(run_id="", niche_id="gaming", summary={"total_usd": 0.5})
        assert ok is False
        mock_db["cur"].execute.assert_not_called()

    def test_empty_niche_id_returns_false(self, mock_db):
        ok = persist_run_cost(run_id="r1", niche_id="", summary={"total_usd": 0.5})
        assert ok is False
        mock_db["cur"].execute.assert_not_called()

    def test_non_dict_summary_returns_false(self, mock_db):
        ok = persist_run_cost(run_id="r1", niche_id="gaming", summary="oops")  # type: ignore[arg-type]
        assert ok is False
        mock_db["cur"].execute.assert_not_called()

    def test_missing_database_url_returns_false_quietly(self, monkeypatch):
        """No DATABASE_URL → debug-log + return False. Common in tests
        and dev environments; shouldn't spam warnings."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with patch("psycopg.connect") as mock_connect:
            ok = persist_run_cost(run_id="r1", niche_id="gaming", summary={"total_usd": 0.5})
            assert ok is False
            mock_connect.assert_not_called()


class TestHappyPath:
    def test_full_summary_persists_with_all_category_splits(self, mock_db):
        summary = {
            "total_usd": 0.5,
            "by_category": {
                "llm": 0.30,
                "image": 0.15,
                "tts": 0.05,
                "compute": 0.00,
            },
            "by_model": {"claude-haiku-4-5": 0.30, "gpt-image-1": 0.15},
            "budget_remaining_pct": 90.0,
            "entry_count": 12,
        }
        ok = persist_run_cost(
            run_id="run_abc",
            niche_id="gaming",
            summary=summary,
            budget_usd=5.0,
        )
        assert ok is True
        # The INSERT was issued
        mock_db["cur"].execute.assert_called_once()
        call_args = mock_db["cur"].execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]

        # Spot-check the SQL has the upsert clause
        assert "ON CONFLICT (run_id, niche_id) DO UPDATE" in sql
        # And the params carry the right values
        # (params is the tuple in order — see persist_run_cost INSERT)
        assert params[0] == "run_abc"
        assert params[1] == "gaming"
        assert params[2] == 0.5  # total_usd
        assert params[3] == 0.30  # llm_usd
        assert params[4] == 0.15  # image_usd
        assert params[5] == 0.05  # tts_usd

    def test_by_model_passed_explicitly_overrides_summary(self, mock_db):
        """Caller may want to override what's in summary["by_model"]
        (e.g. computed differently). Passed kwarg wins."""
        summary = {
            "total_usd": 0.1,
            "by_category": {"llm": 0.1},
            "by_model": {"WRONG": 99.0},
        }
        persist_run_cost(
            run_id="r1",
            niche_id="gaming",
            summary=summary,
            by_model={"claude-haiku-4-5": 0.1},
        )
        params = mock_db["cur"].execute.call_args[0][1]
        # by_model is at index 9 in the INSERT — json.dumps'd
        by_model_str = params[9]
        assert "claude-haiku-4-5" in by_model_str
        assert "WRONG" not in by_model_str

    def test_minimal_summary_persists_with_zero_defaults(self, mock_db):
        """A summary with just total_usd (no by_category) still writes
        a row — the niche shows up in the dashboard with zeros in the
        category cells. Better than silently missing."""
        ok = persist_run_cost(run_id="r1", niche_id="anime", summary={"total_usd": 1.23})
        assert ok is True


class TestFailureSafety:
    def test_db_exception_returns_false_does_not_raise(self, monkeypatch):
        """The cron/RunReport calls this; raising would abort the
        run-report write. Must swallow and return False."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect", side_effect=RuntimeError("connection refused")):
            ok = persist_run_cost(
                run_id="r1",
                niche_id="gaming",
                summary={"total_usd": 0.5, "by_category": {"llm": 0.5}},
            )
            assert ok is False

    def test_non_dict_by_category_does_not_crash(self, mock_db):
        """A future shape-drift in CostAccumulator.summary() shouldn't
        crash the persist call — fall back to zeros."""
        summary = {
            "total_usd": 1.0,
            "by_category": "not_a_dict",  # type: ignore[dict-item]
        }
        ok = persist_run_cost(run_id="r1", niche_id="gaming", summary=summary)
        # Still wrote (with category zeros)
        assert ok is True


class TestAccumulatorByModelProperty:
    """The new CostAccumulator.by_model property is what fuels the
    per-model JSONB column. Pin its behavior alongside this module
    since the data flow is tightly coupled."""

    def test_by_model_groups_entries_by_model_name(self):
        from genlab_core.intelligence.cost_accumulator import CostAccumulator

        acc = CostAccumulator(run_id="r1")
        acc.record_llm("claude-haiku-4-5", input_tokens=1000, output_tokens=500)
        acc.record_llm("claude-haiku-4-5", input_tokens=2000, output_tokens=1000)
        acc.record_llm("gpt-4o-mini", input_tokens=500, output_tokens=200)

        by_model = acc.by_model
        assert "claude-haiku-4-5" in by_model
        assert "gpt-4o-mini" in by_model
        # 2 haiku calls combined > 1 gpt-4o-mini call
        assert by_model["claude-haiku-4-5"] > by_model["gpt-4o-mini"]

    def test_summary_includes_by_model(self):
        from genlab_core.intelligence.cost_accumulator import CostAccumulator

        acc = CostAccumulator(run_id="r1")
        acc.record_llm("claude-haiku-4-5", input_tokens=100, output_tokens=50)
        summary = acc.summary()
        assert "by_model" in summary
        assert "claude-haiku-4-5" in summary["by_model"]
