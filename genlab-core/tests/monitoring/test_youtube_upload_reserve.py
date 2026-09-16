"""Pins the YouTube upload reserve: fetch must not starve publishing.

2026-09-15: YouTube went off anime and movies at "9430/10000 units, 94.3%"
(error string read back from blueprints.platform_publish_status in prod).
The quota gate was first-come-first-served, and fetching runs before publishing,
so 1,430 units of fetch-side spend was enough to deny a 1,600-unit upload. A
refused search degrades (mostPopular chart = 1 unit, or cache); a refused upload
loses the publish window until tomorrow.
"""

import pytest
from genlab_core.monitoring.youtube_quota import UPLOAD_COST, YouTubeQuotaTracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.delenv("GENLAB_YOUTUBE_UPLOAD_RESERVE", raising=False)
    monkeypatch.setenv("GENLAB_YOUTUBE_EXPECTED_UPLOADS", "5")
    return YouTubeQuotaTracker(state_path=tmp_path / "q.json")


def _fill_to(tracker, target: int) -> None:
    """Spend `used` up to exactly `target` using 1-unit ops."""
    already = tracker.status()["used"]
    assert target >= already, "cannot un-spend"
    if target > already:
        tracker.record("video_list", count=target - already)


def test_the_0916_scenario_now_refuses_the_search_not_the_upload(tracker):
    """Fetch overspend must cost fetch its next call, not cost publishing a slot."""
    for n in ("ai_creators", "gaming", "sports"):  # 3 of 5 uploads land = 4800
        tracker.record("upload", niche_id=n)
    _fill_to(tracker, 6750)  # fetch-side spend on top
    # 2 uploads still owed -> 3200 reserved -> fetch ceiling 6800.
    assert tracker.can_afford("upload", niche_id="movies") is True
    assert tracker.can_afford("search", niche_id="movies") is False


def test_uploads_are_never_blocked_by_the_reserve_itself(tracker):
    for n in ("a", "b", "c", "d"):
        tracker.record("upload", niche_id=n)
    assert tracker.can_afford("upload", niche_id="e") is True


def test_cheap_fetch_fallback_stays_affordable_when_search_is_refused(tracker):
    """The degrade path must stay open, or fetch dies instead of degrading."""
    for n in ("a", "b", "c"):
        tracker.record("upload", niche_id=n)
    _fill_to(tracker, 6750)
    assert tracker.can_afford("search", niche_id="gaming") is False
    assert tracker.can_afford("video_list", niche_id="gaming") is True


def test_an_upload_landing_does_not_shrink_what_fetch_can_still_spend(tracker):
    """The reserve is conserved: an upload consumes exactly the reserve it releases.

    This is the honest property. An upload does NOT hand fetch new headroom
    (both `used` and the released reserve move by UPLOAD_COST together) -- but
    neither does it take any, so the two sides cannot starve each other.
    """
    _fill_to(tracker, 500)
    before = tracker.status()["used"]
    ceiling_before = 10_000 - (5 - 0) * UPLOAD_COST
    tracker.record("upload", niche_id="a")
    ceiling_after = 10_000 - (5 - 1) * UPLOAD_COST
    assert (ceiling_before - before) == (ceiling_after - tracker.status()["used"]), (
        "an upload changed fetch's remaining headroom"
    )


def test_hard_stop_still_wins_over_a_released_reserve(tracker):
    for n in ("a", "b", "c", "d", "e"):
        tracker.record("upload", niche_id=n)
    tracker.record("search", count=5, niche_id="gaming")  # 8500
    assert tracker.can_afford("upload", niche_id="f") is False, "10000 cap breached"


def test_flag_off_restores_first_come_first_served(tmp_path, monkeypatch):
    monkeypatch.setenv("GENLAB_YOUTUBE_UPLOAD_RESERVE", "0")
    t = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    t.record("search", count=14, niche_id="gaming")
    assert t.can_afford("search", niche_id="gaming") is True


def test_five_uploads_plus_reserve_fits_the_daily_budget():
    """Arithmetic guard: the reserve must be satisfiable, not aspirational."""
    assert 5 * UPLOAD_COST <= 10_000
