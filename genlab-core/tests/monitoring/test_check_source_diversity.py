"""Pin `check_source_diversity`.

Would have caught the 2026-07-13 → 2026-08-13 gaming outage where
100% of blueprints came from `twitch_trending` because YT tiers were
silently bot-blocked (30 days of single-source degradation).

## Rules pinned

  * ≥3 blueprints over 48h from a single source → WARNING
  * Multi-source (≥2 distinct sources) → no alert
  * < 3 blueprints → no alert (too small to be diagnostic)
  * DB error → no alert (fail-open)
  * No DATABASE_URL → no alert
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.monitoring.checks.pipeline import check_source_diversity


def _mock_cur(rows):
    """Build a mock DB cursor that returns `rows` from fetchall."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=None)
    return cur


def _mock_conn(rows):
    conn = MagicMock()
    conn.cursor.return_value = _mock_cur(rows)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    return conn


class TestSourceDiversityCollapsed:
    def test_all_twitch_fires_warning(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        rows = [("twitch_trending", 42)]  # 42 blueprints, 1 source
        with patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mock_conn(rows),
        ):
            alerts = check_source_diversity("gaming")
        assert len(alerts) == 1
        assert alerts[0].check == "source_diversity_collapsed"
        assert alerts[0].severity == "warning"
        assert alerts[0].details["only_source"] == "twitch_trending"
        assert alerts[0].details["total_48h"] == 42
        assert "twitch_trending" in alerts[0].message

    def test_two_sources_no_alert(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        rows = [("twitch_trending", 30), ("youtube_trending", 10)]
        with patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mock_conn(rows),
        ):
            alerts = check_source_diversity("gaming")
        assert alerts == []

    def test_two_blueprints_below_diagnostic_threshold(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        rows = [("twitch_trending", 2)]  # < 3, not diagnostic
        with patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mock_conn(rows),
        ):
            alerts = check_source_diversity("gaming")
        assert alerts == []

    def test_exactly_three_from_one_source_fires(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        rows = [("twitch_trending", 3)]
        with patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mock_conn(rows),
        ):
            alerts = check_source_diversity("gaming")
        assert len(alerts) == 1

    def test_empty_rows_no_alert(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        with patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            return_value=_mock_conn([]),
        ):
            alerts = check_source_diversity("gaming")
        assert alerts == []


class TestFailOpen:
    def test_no_dsn_returns_empty(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        alerts = check_source_diversity("gaming")
        assert alerts == []

    def test_pg_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        with patch(
            "genlab_core.monitoring.checks.pipeline.pg_connect",
            side_effect=RuntimeError("db down"),
        ):
            alerts = check_source_diversity("gaming")
        assert alerts == []


class TestExported:
    """The new check must be reachable via the health_monitor facade so
    the orchestrator picks it up in `run_all_checks`."""

    def test_reachable_from_facade(self):
        from genlab_core.monitoring import health_monitor
        assert "check_source_diversity" in health_monitor.__all__
        assert callable(health_monitor.check_source_diversity)
