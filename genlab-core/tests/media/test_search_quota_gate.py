"""R-28: the 100-unit search.list must be gated against a SHARED cross-process
daily budget, so the 5 niche processes can't each independently blow the
10k/day YouTube quota.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.media.trending_video_fetcher import TrendingVideoFetcher
from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "youtube_quota.json"


# ── YouTubeQuotaTracker.can_afford ───────────────────────────────────


def test_can_afford_respects_ceiling(state_file: Path) -> None:
    # daily_quota=200 -> hard_stop = int(200 * 0.98) = 196.
    t = YouTubeQuotaTracker(state_path=state_file, daily_quota=200)
    assert t.can_afford("search") is True  # 0 + 100 <= 196
    t.record("search")  # used = 100
    assert t.can_afford("search") is False  # 100 + 100 = 200 > 196


def test_can_afford_unknown_op_is_free(state_file: Path) -> None:
    t = YouTubeQuotaTracker(state_path=state_file)
    assert t.can_afford("not_a_real_op") is True


def test_search_cost_is_100(state_file: Path) -> None:
    t = YouTubeQuotaTracker(state_path=state_file)
    assert t.record("search") == 100


# ── _search_recent gate ──────────────────────────────────────────────


def test_search_skipped_when_quota_exhausted() -> None:
    fetcher = TrendingVideoFetcher("fake-key")
    fetcher._session = MagicMock()  # must NOT be hit
    stub = MagicMock()
    stub.can_afford.return_value = False

    with patch(
        "genlab_core.media.trending_video_fetcher._get_persistent_quota",
        return_value=stub,
    ):
        result = fetcher._search_recent(
            query="anime fight scenes",
            niche_id="anime",
            published_after=datetime.now(UTC),
        )

    assert result == []
    stub.can_afford.assert_called_once_with("search")
    fetcher._session.get.assert_not_called()


def test_search_proceeds_when_quota_available() -> None:
    # When the gate passes but the API errors, we still get the normal []
    # (graceful) — and crucially the gate did NOT short-circuit.
    fetcher = TrendingVideoFetcher("fake-key")
    stub = MagicMock()
    stub.can_afford.return_value = True

    with (
        patch(
            "genlab_core.media.trending_video_fetcher._get_persistent_quota",
            return_value=stub,
        ),
        # Bypass the shared circuit breaker's cross-test state — just call through.
        patch(
            "genlab_core.media.trending_video_fetcher.YOUTUBE_CB.call",
            side_effect=lambda fn: fn(),
        ),
        patch.object(fetcher._session, "get", side_effect=RuntimeError("network down")) as mock_get,
    ):
        result = fetcher._search_recent(
            query="anime fight scenes",
            niche_id="anime",
            published_after=datetime.now(UTC),
        )

    assert result == []  # graceful failure
    stub.can_afford.assert_called_once_with("search")
    mock_get.assert_called_once()  # the gate let it through to the API
