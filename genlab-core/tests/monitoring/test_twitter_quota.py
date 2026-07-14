"""Pin tests for X/Twitter free-tier quota tracker (commit c-todo, 2026-07-14).

Guards these invariants:
  * Fresh state: counter starts at 0
  * record_publish increments monotonically
  * can_publish returns False when cap approached
  * Month rollover auto-resets
  * All state errors fail-open (never block a legitimate publish)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from genlab_core.monitoring.twitter_quota import (
    FREE_TIER_MONTHLY_CAP,
    PUBLISH_LIMIT,
    TwitterQuotaTracker,
)


@pytest.fixture
def tracker(tmp_path: Path) -> TwitterQuotaTracker:
    return TwitterQuotaTracker(state_path=tmp_path / "twitter_quota.json")


class TestFreshState:
    def test_new_state_starts_at_zero(self, tracker):
        allowed, remaining = tracker.can_publish()
        assert allowed is True
        assert remaining == PUBLISH_LIMIT

    def test_fresh_state_has_current_month(self, tracker):
        state = tracker.current_state()
        current_month = datetime.now(UTC).strftime("%Y-%m")
        assert state["month"] == current_month
        assert state["used"] == 0


class TestRecordPublish:
    def test_increment_by_one(self, tracker):
        used = tracker.record_publish()
        assert used == 1
        assert tracker.current_state()["used"] == 1

    def test_increment_by_thread_count(self, tracker):
        used = tracker.record_publish(count=5)
        assert used == 5

    def test_monotonic(self, tracker):
        assert tracker.record_publish() == 1
        assert tracker.record_publish() == 2
        assert tracker.record_publish(count=3) == 5


class TestCanPublishBoundary:
    def test_can_publish_at_zero(self, tracker):
        allowed, remaining = tracker.can_publish()
        assert allowed
        assert remaining == PUBLISH_LIMIT

    def test_cannot_publish_at_limit(self, tracker):
        tracker.record_publish(count=PUBLISH_LIMIT)
        allowed, remaining = tracker.can_publish()
        assert allowed is False
        assert remaining == 0

    def test_cannot_publish_when_over_limit(self, tracker):
        # Cost bumps the caller into over-cap territory
        tracker.record_publish(count=PUBLISH_LIMIT - 1)
        allowed, remaining = tracker.can_publish(cost=5)
        # 479 + 5 = 484 > 480 → blocked
        assert allowed is False
        assert remaining == 1

    def test_engagement_headroom_preserved(self, tracker):
        """Publish cap is 480 not 500 — leaves 20 units for engagement
        replies which also count against the app-level free-tier cap."""
        assert PUBLISH_LIMIT == FREE_TIER_MONTHLY_CAP - 20


class TestMonthRollover:
    def test_rollover_resets_counter(self, tracker, tmp_path):
        # Write a state file with last month
        state_file = tmp_path / "twitter_quota.json"
        state_file.write_text(
            json.dumps({"month": "2025-01", "used": 400})
        )
        # Reading now should detect rollover and reset
        state = tracker.current_state()
        current_month = datetime.now(UTC).strftime("%Y-%m")
        assert state["month"] == current_month
        assert state["used"] == 0

    def test_explicit_reset(self, tracker):
        tracker.record_publish(count=100)
        assert tracker.current_state()["used"] == 100
        tracker.reset_for_month(month_key="2099-01")
        state = tracker.current_state()
        # reset_for_month writes 2099-01; next _read_state() sees
        # rollover (current month != 2099-01) and resets AGAIN.
        # Net effect: counter = 0 with current month.
        assert state["used"] == 0


class TestFailOpen:
    def test_corrupt_state_file_returns_fresh(self, tmp_path):
        state_file = tmp_path / "twitter_quota.json"
        state_file.write_text("this is not valid json {")
        tracker = TwitterQuotaTracker(state_path=state_file)
        state = tracker.current_state()
        assert state["used"] == 0

    def test_missing_dir_returns_fresh(self, tmp_path):
        state_file = tmp_path / "nonexistent-subdir" / "twitter_quota.json"
        tracker = TwitterQuotaTracker(state_path=state_file)
        state = tracker.current_state()
        assert state["used"] == 0

    def test_state_wrong_shape_returns_fresh(self, tmp_path):
        state_file = tmp_path / "twitter_quota.json"
        state_file.write_text(json.dumps(["not", "a", "dict"]))
        tracker = TwitterQuotaTracker(state_path=state_file)
        state = tracker.current_state()
        assert state["used"] == 0
