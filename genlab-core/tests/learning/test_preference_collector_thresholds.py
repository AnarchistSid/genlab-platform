"""Pins for preference_collector threshold tuning + diagnostic logging.

Per the 2026-06-14 deep-dive correction: `preference_data` had 0 rows
because the original MIN_VIEWS=100 floor was higher than any of our
under-25-follower channels' actual reach (max real view = 220 across
1,267 INSIGHTS_168H rows). The retune to MIN_VIEWS=10 lets the
percentile-based top-25%/bottom-25% selection produce pairs from
current data without changing the algorithm.

This file pins:
  1. The new MIN_VIEWS value (10) — operator-visible threshold knob
  2. MIN_RATIO unchanged (1.5) — the actual preference signal
  3. Per-group diagnostic logging fires (the previous silent "0 pairs"
     made data sparsity invisible to the operator)

Pure module-level tests — no DB, no fixtures, no mocking heroics. The
INSERT path is exercised at the integration level on prod runs
(diagnostic logs surface the chain).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.learning import preference_collector


def test_min_views_lowered_to_match_channel_reach():
    """MIN_VIEWS=10 was the 2026-06-14 retune; reverting to 100 (the
    Sprint 68 default) re-blocks every niche on current channel sizes.
    If a future PR needs to raise this back, this test fails so the
    decision surfaces in review."""
    assert preference_collector.MIN_VIEWS == 10, (
        f"MIN_VIEWS={preference_collector.MIN_VIEWS} — lowered from 100 on "
        f"2026-06-14 to match real channel reach. See module docstring "
        f"for the threshold-tuning history."
    )


def test_min_ratio_unchanged_at_1_5():
    """The 1.5× ratio between chosen and rejected views is the real
    preference signal. Lowering it (e.g. to 1.2) would inject noise;
    raising it (e.g. to 2.0) would discard genuine signal on small
    samples. 1.5 sits in the sweet spot for cold-start channels."""
    assert preference_collector.MIN_RATIO == 1.5


@pytest.fixture
def mock_db(monkeypatch):
    """Patch psycopg.connect with a configurable mock so we can drive
    different query results into the collector and assert on its
    behavior + logging without touching a real DB."""
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_connect = MagicMock()
    # psycopg.connect returns a connection (NOT a context manager in
    # this module — the code uses bare conn.close() at the end)
    mock_connect.return_value = mock_conn

    monkeypatch.setattr("psycopg.connect", mock_connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")

    return {"cur": mock_cur, "conn": mock_conn, "connect": mock_connect}


def _make_row(niche, platform, blueprint_id, views, hook="hook", caption="cap", likes=0):
    """Build a query-row dict matching the dict_row factory shape."""
    return {
        "blueprint_id": blueprint_id,
        "niche_id": niche,
        "hook": hook,
        "caption": caption,
        "platform": platform,
        "views": views,
        "likes": likes,
        "view_count": views,
    }


class TestDiagnosticLogging:
    def test_zero_eligible_rows_logs_actionable_message(self, mock_db, caplog):
        """The silent '0 pairs' log made the threshold failure invisible.
        New behavior: an operator-actionable message naming the active
        threshold + the recovery path."""
        mock_db["cur"].fetchall.return_value = []  # no eligible rows

        with caplog.at_level(logging.INFO):
            result = preference_collector.collect_weekly_pairs(window_days=7)

        assert result == 0
        # The new log message names the threshold and the operator
        # action (refresh tokens / wait for more data)
        joined = " ".join(r.message for r in caplog.records)
        assert "min_views=10" in joined
        assert "window_days=7" in joined
        # Actionable recovery hint
        assert "refresh expired tokens" in joined.lower() or "more INSIGHTS_168H" in joined

    def test_per_group_diagnostic_fires_even_when_below_threshold(self, mock_db, caplog):
        """When a group has rows but fewer than 4, the per-group log
        still emits with status BELOW_THRESHOLD so the operator can
        see which (niche, platform) is closest to qualifying."""
        # 5 rows total — enough to pass the >=4 outer gate, but only
        # 3 in any single group → no group qualifies for pair-forming
        mock_db["cur"].fetchall.return_value = [
            _make_row("gaming", "instagram", f"bp{i}", 100 - i * 10) for i in range(3)
        ] + [
            _make_row("anime", "youtube", "bp_a", 50),
            _make_row("anime", "youtube", "bp_b", 30),
        ]

        with caplog.at_level(logging.INFO):
            preference_collector.collect_weekly_pairs()

        joined = "\n".join(r.message for r in caplog.records)
        # Each (niche, platform) group should appear in the diagnostic
        assert "gaming:instagram" in joined
        assert "anime:youtube" in joined
        # And explicitly labeled BELOW_THRESHOLD since neither has ≥4
        assert "BELOW_THRESHOLD" in joined

    def test_eligible_group_logs_attempted_and_skipped_counts(self, mock_db, caplog):
        """When a group has ≥4 rows, the post-pairing log surfaces
        attempted vs ratio_skipped vs dup_skipped vs pairs_created.
        Operator can grep this to see WHY no pairs were created from
        an eligible group (e.g. 'all 12 attempted but ratio < 1.5')."""
        # 4 gaming-IG rows where the ratio between top and bottom is
        # SUFFICIENT (top=100, bottom=10) → top-25=1, bottom-25=1 → 1 pair
        # with ratio=10.0 >> MIN_RATIO=1.5
        mock_db["cur"].fetchall.return_value = [
            _make_row("gaming", "instagram", "bp_top", 100, hook="winning"),
            _make_row("gaming", "instagram", "bp_mid1", 50),
            _make_row("gaming", "instagram", "bp_mid2", 30),
            _make_row("gaming", "instagram", "bp_bottom", 10, hook="losing"),
        ]
        # Duplicate-check query returns None (no existing pair) for all calls
        mock_db["cur"].fetchone.return_value = None

        with caplog.at_level(logging.INFO):
            result = preference_collector.collect_weekly_pairs()

        joined = "\n".join(r.message for r in caplog.records)
        assert "gaming:instagram: attempted=" in joined
        assert "pairs_created=" in joined
        # One pair should land (top=100 vs bottom=10, ratio=10x ≥ 1.5)
        assert result >= 1


class TestRatioSignalPreserved:
    def test_below_ratio_threshold_does_not_emit_pair(self, mock_db, caplog):
        """If top and bottom have similar view counts (ratio < 1.5),
        no pair forms even though both clear MIN_VIEWS. The 1.5×
        threshold is the actual preference signal."""
        # 4 rows with tight spread (12, 11, 11, 10) → ratio = 1.2 < 1.5
        mock_db["cur"].fetchall.return_value = [
            _make_row("gaming", "instagram", "bp1", 12),
            _make_row("gaming", "instagram", "bp2", 11),
            _make_row("gaming", "instagram", "bp3", 11),
            _make_row("gaming", "instagram", "bp4", 10),
        ]
        mock_db["cur"].fetchone.return_value = None

        result = preference_collector.collect_weekly_pairs()
        assert result == 0  # Nothing crossed MIN_RATIO=1.5

    def test_duplicate_pair_not_re_inserted(self, mock_db):
        """When the SELECT 1 FROM preference_data … returns a row
        (pair already exists), we skip re-insert. Pins the idempotent
        re-run behavior."""
        mock_db["cur"].fetchall.return_value = [
            _make_row("gaming", "instagram", "bp_top", 100),
            _make_row("gaming", "instagram", "bp_mid1", 50),
            _make_row("gaming", "instagram", "bp_mid2", 30),
            _make_row("gaming", "instagram", "bp_bot", 10),
        ]
        # SELECT 1 (dup check) returns a row — already exists
        mock_db["cur"].fetchone.return_value = (1,)

        result = preference_collector.collect_weekly_pairs()
        assert result == 0  # Duplicate skipped, nothing inserted


class TestErrorHandling:
    def test_db_error_caught_and_logged_returns_zero(self, monkeypatch, caplog):
        """An unexpected DB error must not crash the cron — return 0
        + log + exit cleanly. Otherwise a transient outage would mark
        the systemd unit as failed."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect", side_effect=RuntimeError("connection refused")):
            with caplog.at_level(logging.ERROR):
                result = preference_collector.collect_weekly_pairs()

        assert result == 0
        assert any("Collection failed" in r.message for r in caplog.records)
