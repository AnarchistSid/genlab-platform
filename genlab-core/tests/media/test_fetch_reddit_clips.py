"""Tests for the pipeline Reddit clip fetcher (media.fetch_reddit_clips).

Focus: the upvote→view-velocity calibration. Reddit upvotes and YouTube views
are different scales; the equivalence must keep Reddit clips comparable to (not
dominating) YouTube clips in the merged candidate ranking.
"""

from __future__ import annotations

import time

import pytest
from genlab_core.media.fetch_reddit_clips import _UPVOTE_VIEW_EQUIVALENCE, _normalise_post


def _video_post(score: int, age_hours: float = 1.0) -> dict:
    return {
        "title": "Insane last-second buzzer beater",
        "score": score,
        "num_comments": 50,
        "upvote_ratio": 0.96,
        "is_video": True,
        "media": {"reddit_video": {"fallback_url": "https://v.redd.it/abc", "duration": 30}},
        "permalink": "/r/nba/abc",
        "url": "https://v.redd.it/abc",
        "created_utc": time.time() - age_hours * 3600,
    }


class TestUpvoteCalibration:
    def test_equivalence_is_calibrated_down_from_100(self):
        # The fix: 40×, not the original 100× that let Reddit dominate.
        assert _UPVOTE_VIEW_EQUIVALENCE == 40.0

    def test_view_count_uses_equivalence(self):
        story = _normalise_post(_video_post(score=1000), "sports", "nba")
        assert story is not None
        # 1000 upvotes × 40 = 40,000 (was 100,000 under the old 100×).
        assert story["view_count"] == int(1000 * _UPVOTE_VIEW_EQUIVALENCE)

    def test_view_velocity_uses_equivalence(self):
        story = _normalise_post(_video_post(score=1000, age_hours=1.0), "sports", "nba")
        assert story is not None
        # ~ (1000 × 40) / 1h = 40,000/h — comparable to a strong YouTube clip,
        # not the ~100,000 the old 100× produced.
        assert story["view_velocity"] == pytest.approx(40000.0, rel=0.15)

    def test_below_soft_floor_dropped(self):
        assert _normalise_post(_video_post(score=50), "sports", "nba") is None
