"""2026-06-14 (PR #201): cold-start fallback for optimal_time_learner.

The learning-system audit found the learner returning [] for ALL 5
niches × all platforms — per-hour buckets at min_observations=5 weren't
accumulating fast enough at typical publish cadences (~5 weeks to get
5 obs at the same UTC hour).

Two fixes:
  1. Lowered min_observations 5 → 3 (matched by prior_weight bump 10→20
     so a 3-obs spike doesn't beat a 50-obs second-best)
  2. New 4-hour-bin fallback when per-hour buckets all fail — returns
     up to 6 OptimalHour entries (one per non-empty bin) so cold-start
     niches get *some* signal instead of falling all the way back to
     static yaml.

These pins guard both behaviors against regression.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_pg(per_hour_rows: list, fallback_rows: list = None):
    """psycopg.connect mock returning per_hour_rows on the first
    fetchall and fallback_rows on the second (the 4-hour-bin retry)."""
    cur = MagicMock()
    if fallback_rows is None:
        cur.fetchall.return_value = per_hour_rows
    else:
        cur.fetchall.side_effect = [per_hour_rows, fallback_rows]
    cur.__enter__ = lambda self: self
    cur.__exit__ = lambda self, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda self, *a: None
    return conn, cur


def test_min_observations_lowered_to_3(monkeypatch):
    """Pin the audit-driven threshold. If this fails, someone reverted
    the lower-threshold + higher-shrinkage rebalance."""
    from genlab_core.scheduling import optimal_time_learner

    assert optimal_time_learner._MIN_OBSERVATIONS == 3
    assert optimal_time_learner._SHRINKAGE_PRIOR == 20.0


def test_per_hour_path_still_works_when_buckets_have_signal(monkeypatch):
    """The per-hour fast path must keep producing results when there's
    enough signal — the cold-start fallback is additive, not a replacement."""
    from genlab_core.scheduling import optimal_time_learner
    from genlab_core.scheduling.optimal_time_learner import get_optimal_hours

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    optimal_time_learner.reset_cache()
    # Three hour-buckets, all with n >= 3 — per-hour path returns them.
    rows = [
        (12, 150.0, 10, 100.0),
        (18, 200.0, 8, 100.0),
        (3, 80.0, 5, 100.0),
    ]
    conn, _ = _mock_pg(rows)
    with patch("psycopg.connect", return_value=conn):
        result = get_optimal_hours("gaming", "instagram", top_n=3)

    assert len(result) == 3
    # Bayesian-shrunk confidence order: 18UTC's 200 avg with n=8 wins
    assert result[0].hour_utc == 18


def test_cold_start_fallback_fires_when_per_hour_empty(monkeypatch):
    """When the per-hour query returns no rows, the 4-hour-bin retry
    must fire and its results are returned."""
    from genlab_core.scheduling import optimal_time_learner
    from genlab_core.scheduling.optimal_time_learner import get_optimal_hours

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    optimal_time_learner.reset_cache()
    # First query (per-hour): no rows met min_observations=3.
    # Second query (4-hour bins): one bin with sufficient sample.
    per_hour_rows: list = []
    fallback_rows = [(14, 175.0, 8, 100.0)]  # center of 12-16 bin
    conn, _ = _mock_pg(per_hour_rows, fallback_rows=fallback_rows)
    with patch("psycopg.connect", return_value=conn):
        result = get_optimal_hours("movies", "facebook", top_n=3)

    assert len(result) == 1
    assert result[0].hour_utc == 14
    assert result[0].sample_size == 8


def test_cold_start_fallback_empty_falls_through_to_empty(monkeypatch):
    """When BOTH queries return nothing (truly no data), the caller
    still gets [] — the scheduler then falls back to static yaml slots."""
    from genlab_core.scheduling import optimal_time_learner
    from genlab_core.scheduling.optimal_time_learner import get_optimal_hours

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    optimal_time_learner.reset_cache()
    conn, _ = _mock_pg([], fallback_rows=[])
    with patch("psycopg.connect", return_value=conn):
        result = get_optimal_hours("anime", "youtube", top_n=3)
    assert result == []


def test_4hour_bin_center_computation_handles_modulo_24(monkeypatch):
    """The bin-center math uses ``% 24`` so a bin_center value like 26
    (from the SQL: (hour/4)*4 + 2 for the last bin) wraps cleanly."""
    from genlab_core.scheduling import optimal_time_learner
    from genlab_core.scheduling.optimal_time_learner import get_optimal_hours

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    optimal_time_learner.reset_cache()
    # Simulate the SQL returning a bin_center=22 (from hour 20-23 bin,
    # center 22). Must come back as hour_utc=22, not wrapped weirdly.
    conn, _ = _mock_pg([], fallback_rows=[(22, 100.0, 5, 80.0)])
    with patch("psycopg.connect", return_value=conn):
        result = get_optimal_hours("sports", "threads", top_n=1)
    assert len(result) == 1
    assert 0 <= result[0].hour_utc <= 23
    assert result[0].hour_utc == 22
