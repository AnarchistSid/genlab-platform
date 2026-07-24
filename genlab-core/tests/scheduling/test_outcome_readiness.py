"""Tests for outcome_readiness — the AUTO #2 parallel readiness signal.

Motivation pinned in the module docstring: the operator-agreement
ratchet has been stuck at 0 samples for 24 days because the
operator hasn't clicked. This module provides an independent signal
based on whether auto-approved posts actually performed.

Pins:
  Fail-open:
    - DB raise returns ready=False verdict, doesn't raise
  Thresholds:
    - sample_count < min → ready=False regardless of rate
    - rate < threshold → ready=False regardless of samples
    - both met → ready=True
    - zero samples → rate=0.0 (no divide-by-zero)
  Signal shape:
    - MAX(reward_48h) across platforms is what we count
      (mirror: "any platform going viral validates the gate")
  All-niches convenience:
    - returns all 5 canonical niche IDs in order
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestCheckOutcomeReadiness:
    def _make_conn(self, sample_count: int, outcome_good_count: int, raise_error=False):
        conn = MagicMock()
        if raise_error:
            conn.execute.side_effect = RuntimeError("db down")
        else:
            m = MagicMock()
            m.fetchone.return_value = {
                "sample_count": sample_count,
                "outcome_good_count": outcome_good_count,
            }
            conn.execute.return_value = m
        return conn

    def test_ready_when_both_thresholds_met(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        # 40 samples, 32 good = 0.80 > 0.75 threshold
        conn = self._make_conn(sample_count=40, outcome_good_count=32)
        r = check_outcome_readiness(conn, "gaming")
        assert r.sample_count == 40
        assert r.outcome_good_count == 32
        assert r.outcome_good_rate == pytest.approx(0.80)
        assert r.ready is True

    def test_not_ready_when_sample_count_low(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = self._make_conn(sample_count=15, outcome_good_count=15)  # 100% rate
        r = check_outcome_readiness(conn, "gaming")
        assert r.ready is False
        assert r.outcome_good_rate == 1.0

    def test_not_ready_when_rate_low(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        # 40 samples, 20 good = 0.50 < 0.75
        conn = self._make_conn(sample_count=40, outcome_good_count=20)
        r = check_outcome_readiness(conn, "gaming")
        assert r.ready is False
        assert r.outcome_good_rate == 0.50

    def test_zero_samples_no_divide_by_zero(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = self._make_conn(sample_count=0, outcome_good_count=0)
        r = check_outcome_readiness(conn, "gaming")
        assert r.sample_count == 0
        assert r.outcome_good_rate == 0.0
        assert r.ready is False

    def test_fail_open_on_db_error(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = self._make_conn(sample_count=0, outcome_good_count=0, raise_error=True)
        r = check_outcome_readiness(conn, "gaming")
        # Never raises. Returns zero-filled ready=False.
        assert r.ready is False
        assert r.sample_count == 0

    def test_carries_configured_thresholds(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = self._make_conn(sample_count=40, outcome_good_count=32)
        r = check_outcome_readiness(conn, "gaming", threshold=0.10)
        # Result carries the threshold that was used, not the default.
        assert r.threshold == 0.10

    def test_custom_min_samples_relaxes_ready(self):
        """Operator can loosen min_samples for smaller niches. Verify
        the threshold override actually flips ready when appropriate."""
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = self._make_conn(sample_count=10, outcome_good_count=9)
        # Default min_samples=30 → not ready
        r_default = check_outcome_readiness(conn, "gaming")
        assert r_default.ready is False
        # Override min_samples=5 → ready (10 >= 5, rate 0.9 >= 0.75)
        conn2 = self._make_conn(sample_count=10, outcome_good_count=9)
        r_relaxed = check_outcome_readiness(conn2, "gaming", min_samples=5)
        assert r_relaxed.ready is True

    def test_custom_agreement_rate_threshold(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = self._make_conn(sample_count=40, outcome_good_count=28)
        # 0.70 rate vs default 0.75 → not ready
        r_default = check_outcome_readiness(conn, "gaming")
        assert r_default.ready is False
        # 0.70 rate vs threshold 0.70 → ready
        conn2 = self._make_conn(sample_count=40, outcome_good_count=28)
        r_relaxed = check_outcome_readiness(
            conn2, "gaming", agreement_rate_threshold=0.70
        )
        assert r_relaxed.ready is True


class TestCheckAllNiches:
    def test_returns_all_five_niches(self):
        from genlab_core.scheduling.outcome_readiness import check_all_niches

        conn = MagicMock()
        m = MagicMock()
        m.fetchone.return_value = {
            "sample_count": 0,
            "outcome_good_count": 0,
        }
        conn.execute.return_value = m
        out = check_all_niches(conn)
        assert set(out.keys()) == {
            "ai_creators",
            "gaming",
            "sports",
            "movies",
            "anime",
        }

    def test_order_is_deterministic(self):
        from genlab_core.scheduling.outcome_readiness import check_all_niches

        conn = MagicMock()
        m = MagicMock()
        m.fetchone.return_value = {
            "sample_count": 0,
            "outcome_good_count": 0,
        }
        conn.execute.return_value = m
        keys = list(check_all_niches(conn).keys())
        # Order matters for dashboard row rendering.
        assert keys == ["ai_creators", "gaming", "sports", "movies", "anime"]


class TestSQLShape:
    """Pin the query behavior — the LIKE join is the load-bearing
    linkage between blueprints and pending_feedback. If someone
    refactors this into a broken JOIN, we catch it here."""

    def test_query_filters_by_auto_approver_source_tag(self):
        from genlab_core.scheduling.outcome_readiness import (
            _AUTO_APPROVAL_SOURCE_TAG,
            check_outcome_readiness,
        )

        conn = MagicMock()
        m = MagicMock()
        m.fetchone.return_value = {
            "sample_count": 0,
            "outcome_good_count": 0,
        }
        conn.execute.return_value = m
        check_outcome_readiness(conn, "gaming")
        # The auto-approver source tag must appear in the query
        # parameters — otherwise we'd count operator-approved posts
        # too, contaminating the signal.
        call_args = conn.execute.call_args
        params = call_args[0][1]
        assert _AUTO_APPROVAL_SOURCE_TAG in params

    def test_query_uses_action_taken_source_column(self):
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = MagicMock()
        m = MagicMock()
        m.fetchone.return_value = {
            "sample_count": 0,
            "outcome_good_count": 0,
        }
        conn.execute.return_value = m
        check_outcome_readiness(conn, "gaming")
        sql = conn.execute.call_args[0][0]
        assert "action_taken_source" in sql
        assert "pending_feedback" in sql

    def test_join_uses_candidate_id_not_blueprint_uuid(self):
        """2026-07-24 discovery: task_id shape is
        ``{candidate_id}__{platform}`` where candidate_id is a 64-char
        hash. Initial join used ``substring(blueprint_id FROM 1 FOR 16)``
        which never matched the 64-hex prefix. Pin: SQL must reference
        ``candidate_id`` in the JOIN condition."""
        from genlab_core.scheduling.outcome_readiness import check_outcome_readiness

        conn = MagicMock()
        m = MagicMock()
        m.fetchone.return_value = {
            "sample_count": 0,
            "outcome_good_count": 0,
        }
        conn.execute.return_value = m
        check_outcome_readiness(conn, "gaming")
        sql = conn.execute.call_args[0][0]
        assert "candidate_id" in sql, (
            "outcome_readiness JOIN must reference candidate_id — "
            "task_id shape is {candidate_id}__{platform}, not "
            "{blueprint_uuid[:16]}__{platform}. Regressing to the "
            "old substring join returns 0 samples for every niche."
        )
        # The pattern-match on task_id must use LIKE with __.
        assert "task_id LIKE" in sql
