"""Tests for YouTube Analytics API client."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from genlab_core.analytics.youtube_analytics_client import (
    YouTubeAnalyticsClient,
    YouTubeAnalyticsMetrics,
)


@pytest.fixture
def mock_service():
    return MagicMock()


@pytest.fixture
def client(mock_service):
    return YouTubeAnalyticsClient(mock_service, channel_id="UCtest123")


class TestYouTubeAnalyticsClient:
    def test_skips_videos_published_less_than_48h_ago(self, client):
        recent = datetime.now(timezone.utc) - timedelta(hours=10)
        result = client.fetch_video_metrics("vid123", published_at=recent)
        assert result is None

    def test_parses_metrics_from_api_response(self, client, mock_service):
        old = datetime.now(timezone.utc) - timedelta(hours=72)
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
                {"name": "estimatedMinutesWatched"},
                {"name": "averageViewDuration"},
                {"name": "averageViewPercentage"},
                {"name": "likes"},
                {"name": "comments"},
                {"name": "shares"},
                {"name": "subscribersGained"},
            ],
            "rows": [["vid123", 1500, 320.5, 45.2, 62.1, 80, 12, 5, 3]],
        }

        result = client.fetch_video_metrics("vid123", published_at=old, window_label="72h")
        assert result is not None
        assert result.views == 1500
        assert result.watch_minutes == 320.5
        assert result.avg_view_duration_s == 45.2
        assert result.avg_view_pct == 62.1
        assert result.subscribers_gained == 3
        assert result.window_label == "72h"

    def test_returns_none_on_api_error(self, client, mock_service):
        old = datetime.now(timezone.utc) - timedelta(hours=72)
        mock_service.reports().query().execute.side_effect = Exception("API error")

        result = client.fetch_video_metrics("vid123", published_at=old)
        assert result is None

    def test_returns_none_when_no_rows_in_response(self, client, mock_service):
        old = datetime.now(timezone.utc) - timedelta(hours=72)
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [],
            "rows": [],
        }

        result = client.fetch_video_metrics("vid123", published_at=old)
        assert result is None

    def test_handles_missing_metric_fields_gracefully(self, client, mock_service):
        old = datetime.now(timezone.utc) - timedelta(hours=72)
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [{"name": "video"}, {"name": "views"}],
            "rows": [["vid123", 100]],
        }

        result = client.fetch_video_metrics("vid123", published_at=old)
        assert result is not None
        assert result.views == 100
        assert result.watch_minutes == 0.0  # missing → default
        assert result.subscribers_gained == 0

    def test_naive_datetime_treated_as_utc(self, client, mock_service):
        """Naive datetimes should be treated as UTC, not rejected."""
        old = datetime.now() - timedelta(hours=72)  # naive
        mock_service.reports().query().execute.return_value = {
            "columnHeaders": [{"name": "video"}, {"name": "views"}],
            "rows": [["vid123", 50]],
        }
        result = client.fetch_video_metrics("vid123", published_at=old)
        assert result is not None
