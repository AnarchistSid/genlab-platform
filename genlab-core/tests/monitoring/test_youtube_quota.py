"""Tests for genlab_core.monitoring.youtube_quota."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from genlab_core.monitoring.youtube_quota import (
    DAILY_QUOTA,
    PACIFIC,
    UPLOAD_COST,
    YouTubeQuotaTracker,
)


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    """Return a temporary state-file path (does not exist yet)."""
    return tmp_path / "youtube_quota.json"


# ── core behaviour ─────────────────────────────────────────────────────


def test_record_increments_by_operation_cost(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    used = tracker.record("upload")
    assert used == UPLOAD_COST  # 1,600

    used = tracker.record("thumbnail_set")
    assert used == UPLOAD_COST + 50

    used = tracker.record("comment_list", count=3)
    assert used == UPLOAD_COST + 50 + 3


def test_can_upload_false_when_near_limit(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    # Burn enough quota that one more upload would exceed the hard stop.
    # hard_stop = 10,000 * 0.90 = 9,000
    # After 5 uploads: 5 * 1,600 = 8,000.  8,000 + 1,600 = 9,600 > 9,000
    for _ in range(5):
        tracker.record("upload")
    assert tracker.can_upload() is False


def test_can_upload_true_when_fresh(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    assert tracker.can_upload() is True


def test_maybe_reset_clears_on_new_day(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    tracker.record("upload")
    assert tracker.daily_uploads_used() == 1

    # Simulate the state being from "yesterday" by patching the persisted date.
    yesterday = (datetime.now(tz=PACIFIC) - timedelta(days=1)).strftime("%Y-%m-%d")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["reset_date"] = yesterday
    state_file.write_text(json.dumps(data), encoding="utf-8")

    # Force a fresh load so _reset_date picks up the stale value.
    tracker2 = YouTubeQuotaTracker(state_path=state_file)
    # Any public method triggers _maybe_reset, which should detect the date
    # change and zero out counters.
    assert tracker2.daily_uploads_used() == 0
    assert tracker2.status()["used"] == 0


def test_status_returns_correct_fields(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    tracker.record("upload")
    tracker.record("channel_list", count=2)

    st = tracker.status()
    assert st["used"] == UPLOAD_COST + 2
    assert st["remaining"] == DAILY_QUOTA - (UPLOAD_COST + 2)
    assert st["upload_count"] == 1
    assert "pct_used" in st
    assert "reset_date" in st


def test_persists_to_disk(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    tracker.record("upload")
    tracker.record("playlist_insert", count=2)

    # Create a second tracker reading from the same file.
    tracker2 = YouTubeQuotaTracker(state_path=state_file)
    st = tracker2.status()
    assert st["used"] == UPLOAD_COST + 100  # 1,600 + 2*50
    assert st["upload_count"] == 1


def test_record_unknown_operation_raises(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    with pytest.raises(ValueError, match="Unknown operation"):
        tracker.record("delete_channel")


def test_daily_uploads_used_counts_only_uploads(state_file: Path) -> None:
    tracker = YouTubeQuotaTracker(state_path=state_file)
    tracker.record("upload", count=3)
    tracker.record("comment_list", count=100)
    assert tracker.daily_uploads_used() == 3
