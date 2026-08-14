"""Phase 3.A session 3 consumer-wire pins (2026-08-14) — competitor
context into Strategist prompt.

The wire has two parts:

* ``PostgresStateCollector._competitor_context(niche_id)`` reads
  the top-5 rows from ``competitor_content_deltas`` where
  ``delta_ratio >= 5.0`` AND ``our_reference_view_count >= 10``
  (thin-baseline exclusion). Gated by
  ``GENLAB_COMPETITOR_CONTEXT_ENABLED`` — flag off returns [].

* ``prompts._format_competitor_context(rows)`` renders the section
  for the Strategist prompt. Returns explicit flag-off / cold-start
  lines when rows empty; renders top-5 with delta ratios when
  populated.

Pins:

* Flag off → collector returns [] regardless of DB state
* Flag off → prompt renders "(flag disabled)" line
* Flag on + no rows → prompt renders "(no competitor rows yet)" line
* Flag on + rows → prompt renders each row's label/views/ratio/title
* Collector fail-open on DB error — still returns []
* Thin-baseline rows excluded by collector SQL contract (delta_ratio
  math would be misleading with our_reference < 10)
"""
from __future__ import annotations

from unittest.mock import MagicMock


class TestCompetitorContextFormatter:
    """The prompt formatter is a pure function — tests it in
    isolation without needing the DB-backed StateCollector."""

    def test_flag_off_renders_flag_disabled_line(self, monkeypatch):
        from genlab_core.intelligence.prompts import _format_competitor_context

        monkeypatch.delenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", raising=False)
        out = _format_competitor_context([
            {"competitor_label": "MKBHD", "view_count": 4_000_000, "delta_ratio": 200.0,
             "title": "hi"},
        ])
        assert "flag disabled" in out

    def test_flag_off_ignores_rows(self, monkeypatch):
        """Even with real rows, flag-off means the LLM sees the
        disabled line — never the raw data."""
        from genlab_core.intelligence.prompts import _format_competitor_context

        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "0")
        out = _format_competitor_context([
            {"competitor_label": "MKBHD", "view_count": 4_000_000, "delta_ratio": 200.0,
             "title": "SECRET"},
        ])
        assert "SECRET" not in out
        assert "flag disabled" in out

    def test_flag_on_empty_renders_cold_start_line(self, monkeypatch):
        from genlab_core.intelligence.prompts import _format_competitor_context

        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "1")
        out = _format_competitor_context([])
        assert "no competitor rows yet" in out

    def test_flag_on_renders_row_details(self, monkeypatch):
        from genlab_core.intelligence.prompts import _format_competitor_context

        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "1")
        rows = [
            {
                "competitor_label": "MKBHD",
                "view_count": 4_635_853,
                "delta_ratio": 309.0,
                "title": "Samsung Z Fold 8 Review",
            },
            {
                "competitor_label": "Fireship",
                "view_count": 904_302,
                "delta_ratio": 60.0,
                "title": "Rust actually",
            },
        ]
        out = _format_competitor_context(rows)
        assert "MKBHD" in out
        assert "Samsung Z Fold 8" in out
        assert "309.0x" in out
        assert "Fireship" in out
        assert "4,635,853" in out  # formatted with commas

    def test_flag_true_string_case_insensitive(self, monkeypatch):
        from genlab_core.intelligence.prompts import _format_competitor_context

        for val in ("1", "true", "True", "YES", "yes"):
            monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", val)
            out = _format_competitor_context([])
            assert "no competitor rows yet" in out, f"failed for {val}"


class TestCompetitorContextCollector:
    """The collector wraps SQL — mock the connection at the sql
    boundary rather than spinning up postgres."""

    def _mock_conn(self, fetchall_return):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = fetchall_return
        return conn

    def test_flag_off_returns_empty(self, monkeypatch):
        from genlab_core.intelligence.state_collector import PostgresStateCollector

        monkeypatch.delenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", raising=False)
        conn = self._mock_conn([
            {"competitor_channel_label": "MKBHD", "competitor_video_id": "vid",
             "competitor_title": "t", "competitor_view_count": 1,
             "delta_ratio": 100.0, "our_reference_view_count": 100},
        ])
        collector = PostgresStateCollector(conn)
        result = collector._competitor_context("ai_creators")
        assert result == []
        # SQL was never executed when flag off
        conn.execute.assert_not_called()

    def test_flag_on_returns_normalized_rows(self, monkeypatch):
        from genlab_core.intelligence.state_collector import PostgresStateCollector

        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "1")
        conn = self._mock_conn([
            {
                "competitor_channel_label": "MKBHD",
                "competitor_video_id": "vid1",
                "competitor_title": "Samsung Z Fold 8",
                "competitor_view_count": 4_635_853,
                "delta_ratio": 309.0,
                "our_reference_view_count": 15_000,
            },
        ])
        collector = PostgresStateCollector(conn)
        result = collector._competitor_context("ai_creators")
        assert len(result) == 1
        assert result[0]["competitor_label"] == "MKBHD"
        assert result[0]["delta_ratio"] == 309.0
        assert result[0]["view_count"] == 4_635_853

    def test_db_error_returns_empty(self, monkeypatch):
        """Fail-open: any DB error yields empty list (LLM sees
        flag-on cold-start line instead of a crash)."""
        from genlab_core.intelligence.state_collector import PostgresStateCollector

        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "1")
        conn = MagicMock()
        conn.execute.side_effect = Exception("table missing")
        collector = PostgresStateCollector(conn)
        result = collector._competitor_context("ai_creators")
        assert result == []
        # rollback fires so subsequent queries aren't blocked
        conn.rollback.assert_called()

    def test_sql_excludes_thin_baseline(self, monkeypatch):
        """Contract pin: SQL must exclude our_reference < 10 so a
        broken metric collector doesn't feed 4-million-x ratios into
        strategist proposals as if they were real reach gaps."""
        from genlab_core.intelligence.state_collector import PostgresStateCollector

        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "1")
        conn = self._mock_conn([])
        collector = PostgresStateCollector(conn)
        collector._competitor_context("ai_creators")
        # Verify SQL includes the thin-baseline exclusion clause
        sql = conn.execute.call_args[0][0]
        assert "our_reference_view_count >= 10" in sql
        assert "delta_ratio >= 5.0" in sql


class TestCompetitorContextInFullState:
    """Pin the top-level state dict includes competitor_context key
    so downstream prompt formatting doesn't KeyError."""

    def test_full_collect_includes_key(self, monkeypatch):
        from datetime import date
        from genlab_core.intelligence.state_collector import PostgresStateCollector

        monkeypatch.delenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", raising=False)
        # Every method fails → all values None/empty, but the key
        # must still appear
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        collector = PostgresStateCollector(conn)
        state = collector.collect("ai_creators", date(2026, 8, 14))
        assert "competitor_context" in state
        assert state["competitor_context"] == []
