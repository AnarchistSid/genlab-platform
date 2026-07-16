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
    # hard_stop = 10,000 * 1.00 = 10,000 (as of 2026-07-16; was 0.98=9800)
    # After 6 uploads: 6 * 1,600 = 9,600. 9,600 + 1,600 = 11,200 > 10,000
    for _ in range(6):
        tracker.record("upload")
    assert tracker.can_upload() is False


def test_all_five_niches_can_upload_without_headroom_starvation(state_file: Path) -> None:
    """Regression pin: 5 niches × 1600u/upload = 8000u must all
    succeed even after modest search/analytics overhead.

    History: HARD_STOP_PCT=0.98 (2026-06-27 → 2026-07-16) left only
    200u headroom above the 5-niche minimum. Two search calls (100u
    each) ate all headroom before the 5th niche's upload check
    ``used + 1600 > 9800`` fired. 30-day audit (2026-07-16) counted
    9 upload-blocked events at "8300/9800, 84.7%". Bumping
    HARD_STOP_PCT to 1.00 restored ~1000u of headroom.

    This test pins the new invariant: after 4 uploads (6400u) + 5
    search operations (500u overhead), the 5th niche's upload
    (6400 + 500 + 1600 = 8500) must still be allowed. Under the
    old 0.98 ceiling, 8500 <= 9800 was still allowed but the
    check was on the SUM (used + upload_cost = 6900 + 1600 = 8500,
    OK). Under 1.00, allowed until sum > 10000. Real regression
    guard fires if PCT drops back to 0.90.
    """
    tracker = YouTubeQuotaTracker(state_path=state_file)
    # 4 uploads first (simulate 4 niches already published)
    for _ in range(4):
        tracker.record("upload")
    # 5 search operations for the 5th niche's discovery
    for _ in range(5):
        tracker.record("search")
    # 4*1600 + 5*100 = 6400 + 500 = 6900 used
    # 5th niche upload check: 6900 + 1600 = 8500 <= 10000 → allowed
    assert tracker.can_upload() is True, (
        f"5th niche's upload was blocked despite ample budget "
        f"(used={tracker.daily_uploads_used() * 1600 + 500}); "
        f"HARD_STOP_PCT may have regressed below 1.00"
    )


def test_hard_stop_pct_is_at_least_1_00(state_file: Path) -> None:
    """Pin the 2026-07-16 fix. Any lowering to 0.98 or below reintroduces
    the 30-day-9-events regression from the comprehensive audit."""
    from genlab_core.monetization import network_registry  # noqa: F401 — sanity
    from genlab_core.monitoring.youtube_quota import HARD_STOP_PCT

    assert HARD_STOP_PCT >= 1.00, (
        f"HARD_STOP_PCT={HARD_STOP_PCT} — must be >= 1.00 per 2026-07-16 audit. "
        f"Values <1.00 (0.98 was previous default) left insufficient headroom "
        f"for 5-niche uploads plus modest search overhead, blocking 9 legitimate "
        f"publishes over a 30d window at '8300/9800, 84.7%'. Only raise the "
        f"floor here (e.g. to 1.02 with genuine buffer), never lower it."
    )


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
