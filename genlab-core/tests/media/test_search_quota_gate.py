"""R-28 + R-37: search.list must be gated against a SHARED cross-process daily
budget (so the 5 niche processes can't each blow the 10k/day quota), and fetches
must be served from a 6h disk cache on re-runs (0 quota, 0 network).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.media.trending_video_fetcher import (
    TrendingVideo,
    TrendingVideoFetcher,
    _video_from_cacheable,
    _video_to_cacheable,
)
from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

_FETCHER_MOD = "genlab_core.media.trending_video_fetcher"


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "youtube_quota.json"


def _miss_cache() -> MagicMock:
    """A cache stub that always misses."""
    c = MagicMock()
    c.get.return_value = None
    return c


def _sample_video(vid: str = "abc123") -> TrendingVideo:
    return TrendingVideo(
        video_id=vid,
        title="Wild anime fight",
        channel_name="Studio X",
        channel_id="UC123",
        published_at=datetime(2026, 5, 23, 10, 0, tzinfo=UTC),
        view_count=10_000,
        like_count=500,
        duration_seconds=45,
        thumbnail_url="https://img/thumb.jpg",
        niche_id="anime",
        search_query="anime fight scenes",
        view_velocity=1234.5,
        download_url="https://www.youtube.com/watch?v=abc123",
        is_official_channel=True,
        license="youtube",
    )


# ── R-28: YouTubeQuotaTracker.can_afford ─────────────────────────────


def test_can_afford_respects_ceiling(state_file: Path) -> None:
    # 2026-07-21: HARD_STOP_PCT bumped 0.98 → 1.00 on 2026-07-16 (see
    # youtube_quota.py:88 history comment). Test updated to match.
    # daily_quota=200 -> hard_stop = int(200 * 1.00) = 200.
    t = YouTubeQuotaTracker(state_path=state_file, daily_quota=200)
    assert t.can_afford("search") is True  # 0 + 100 <= 200
    t.record("search")  # used = 100
    assert t.can_afford("search") is True  # 100 + 100 = 200 exactly, not >
    t.record("search")  # used = 200
    assert t.can_afford("search") is False  # 200 + 100 > 200


def test_can_afford_unknown_op_is_free(state_file: Path) -> None:
    t = YouTubeQuotaTracker(state_path=state_file)
    assert t.can_afford("not_a_real_op") is True


def test_search_cost_is_100(state_file: Path) -> None:
    t = YouTubeQuotaTracker(state_path=state_file)
    assert t.record("search") == 100


# ── R-28: _search_recent quota gate ──────────────────────────────────


def test_search_skipped_when_quota_exhausted() -> None:
    fetcher = TrendingVideoFetcher("fake-key")
    fetcher._session = MagicMock()  # must NOT be hit
    stub = MagicMock()
    stub.can_afford.return_value = False

    with (
        patch(f"{_FETCHER_MOD}._get_fetch_cache", return_value=_miss_cache()),
        patch(f"{_FETCHER_MOD}._get_persistent_quota", return_value=stub),
    ):
        result = fetcher._search_recent(
            query="anime fight scenes",
            niche_id="anime",
            published_after=datetime.now(UTC),
        )

    assert result == []
    # PR #537: fetcher now passes niche_id alongside "search".
    stub.can_afford.assert_called_once_with("search", niche_id="anime")
    fetcher._session.get.assert_not_called()


def test_search_proceeds_when_quota_available() -> None:
    # Gate passes but the API errors -> normal graceful [] AND the gate did not
    # short-circuit (the request was attempted).
    fetcher = TrendingVideoFetcher("fake-key")
    stub = MagicMock()
    stub.can_afford.return_value = True

    with (
        patch(f"{_FETCHER_MOD}._get_fetch_cache", return_value=_miss_cache()),
        patch(f"{_FETCHER_MOD}._get_persistent_quota", return_value=stub),
        patch(f"{_FETCHER_MOD}.YOUTUBE_CB.call", side_effect=lambda fn: fn()),
        patch.object(fetcher._session, "get", side_effect=RuntimeError("network down")) as mock_get,
    ):
        result = fetcher._search_recent(
            query="anime fight scenes",
            niche_id="anime",
            published_after=datetime.now(UTC),
        )

    assert result == []
    # PR #537: fetcher now passes niche_id alongside "search".
    stub.can_afford.assert_called_once_with("search", niche_id="anime")
    mock_get.assert_called_once()


# ── R-37: disk cache ─────────────────────────────────────────────────


def test_search_cache_hit_skips_api_and_gate() -> None:
    fetcher = TrendingVideoFetcher("fake-key")
    fetcher._session = MagicMock()
    quota = MagicMock()

    cache = MagicMock()
    cache.get.return_value = [_video_to_cacheable(_sample_video())]

    with (
        patch(f"{_FETCHER_MOD}._get_fetch_cache", return_value=cache),
        patch(f"{_FETCHER_MOD}._get_persistent_quota", return_value=quota),
    ):
        result = fetcher._search_recent(
            query="anime fight scenes",
            niche_id="anime",
            published_after=datetime.now(UTC),
        )

    assert len(result) == 1
    assert result[0].video_id == "abc123"
    fetcher._session.get.assert_not_called()  # no API call on a hit
    quota.can_afford.assert_not_called()  # gate not even consulted on a hit


def test_video_cache_roundtrip_is_json_safe() -> None:
    import json

    v = _sample_video()
    d = _video_to_cacheable(v)
    json.dumps(d)  # must be JSON-serializable (datetime -> ISO string)

    v2 = _video_from_cacheable(d)
    assert v2.video_id == v.video_id
    assert v2.published_at == v.published_at
    assert v2.view_velocity == v.view_velocity
