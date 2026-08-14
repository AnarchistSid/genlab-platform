"""Pin Phase 4.E session 2 pool consumer:

  * reserve_top_pending: limit<=0 returns []
  * reserve_top_pending: DB error → []
  * reserve_top_pending: happy path returns ReservedIdea list
  * link_to_blueprint: DB error → False
  * release_reservation: only flips consumed-without-link rows
  * count_pool_status: returns dict with all three status keys
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.intelligence.ideation_pool_consumer import (
    ReservedIdea,
    count_pool_status,
    link_to_blueprint,
    release_reservation,
    reserve_top_pending,
)


class TestReserveTopPending:
    def test_zero_limit_returns_empty(self):
        conn = MagicMock()
        assert reserve_top_pending(conn, "gaming", limit=0) == []
        # SQL never fired
        conn.execute.assert_not_called()

    def test_negative_limit_returns_empty(self):
        conn = MagicMock()
        assert reserve_top_pending(conn, "gaming", limit=-1) == []

    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert reserve_top_pending(conn, "gaming") == []

    def test_returns_reserved_ideas(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {
                "id": "id-1", "niche_id": "gaming",
                "title": "Topic A", "hook_seed": "Hook A",
                "rationale": "trend match", "score": 0.8,
                "batch_id": "batch-1",
            },
        ]
        rows = reserve_top_pending(conn, "gaming", limit=1)
        assert len(rows) == 1
        assert isinstance(rows[0], ReservedIdea)
        assert rows[0].id == "id-1"
        assert rows[0].score == 0.8

    def test_returns_multiple_when_limit_higher(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"id": f"id-{i}", "niche_id": "gaming",
             "title": f"T{i}", "hook_seed": "", "rationale": "",
             "score": 0.5, "batch_id": "b-1"}
            for i in range(3)
        ]
        rows = reserve_top_pending(conn, "gaming", limit=3)
        assert len(rows) == 3


class TestLinkToBlueprint:
    def test_db_error_returns_false(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert link_to_blueprint(conn, "idea-1", "bp-1") is False

    def test_success_returns_true(self):
        conn = MagicMock()
        assert link_to_blueprint(conn, "idea-1", "bp-1") is True
        conn.commit.assert_called()


class TestReleaseReservation:
    def test_success_when_row_updated(self):
        conn = MagicMock()
        conn.execute.return_value.rowcount = 1
        assert release_reservation(conn, "idea-1") is True

    def test_returns_false_when_no_row_matched(self):
        """Row already linked to a blueprint → can't release."""
        conn = MagicMock()
        conn.execute.return_value.rowcount = 0
        assert release_reservation(conn, "idea-1") is False

    def test_db_error_returns_false(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert release_reservation(conn, "idea-1") is False


class TestCountPoolStatus:
    def test_returns_all_three_keys(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"status": "pending", "n": 12},
            {"status": "consumed", "n": 3},
        ]
        counts = count_pool_status(conn, "gaming")
        assert counts["pending"] == 12
        assert counts["consumed"] == 3
        # Missing status defaults to 0
        assert counts["expired"] == 0

    def test_db_error_returns_zeros(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        counts = count_pool_status(conn, "gaming")
        assert counts == {"pending": 0, "consumed": 0, "expired": 0}

    def test_unknown_status_ignored(self):
        """A row with unexpected status key doesn't crash — just
        doesn't get counted."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"status": "pending", "n": 5},
            {"status": "weird_status", "n": 999},
        ]
        counts = count_pool_status(conn, "gaming")
        assert counts["pending"] == 5
        assert counts["expired"] == 0
