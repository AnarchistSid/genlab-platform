"""Tests for the FetchInsights pipeline stage."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from genlab_core.pipeline.stages.fetch_insights import FetchInsights


def _make_pub_record(post_id, platform, niche_id, hours_ago=12, metrics_fetched=""):
    pub_dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "id": f"rec_{post_id}",
        "fields": {
            "post_id": post_id,
            "platform": platform,
            "niche_id": niche_id,
            "published_at": pub_dt.isoformat(),
            "metrics_fetched": metrics_fetched,
        },
    }


class TestFetchInsightsSharePoint:
    def test_fetches_previously_published_posts(self):
        """FetchInsights should query SharePoint for posts published 6h-7d ago."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc123", "youtube", "gaming", hours_ago=12),
            _make_pub_record("ig_def456", "instagram", "gaming", hours_ago=24),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],  # Empty — current run has no published posts
            "niche_config": {},
        }

        stage = FetchInsights()
        # Mock _fetch_platform to simulate successful API calls
        stage._fetch_platform = MagicMock(return_value={"views": 100, "likes": 10})
        result = stage.execute(context)

        stats = result["run_stats"]["insights"]
        # Should have fetched 2 posts from SharePoint, not skipped everything
        assert stats["fetched"] == 2
        assert stats["skipped"] == 0
        # Should have queried SharePoint
        mock_client.publishing_analytics.all.assert_called_once()

    def test_skips_already_fetched(self):
        """Posts with metrics_fetched set should be skipped."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc", "youtube", "gaming", hours_ago=12,
                             metrics_fetched="2026-03-16T12:00:00+00:00"),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)
        assert result["run_stats"]["insights"]["skipped"] == 1

    def test_skips_too_young(self):
        """Posts published less than 6h ago should be skipped."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc", "youtube", "gaming", hours_ago=2),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)
        assert result["run_stats"]["insights"]["skipped"] == 1

    def test_no_backlog_client_returns_context(self):
        """Without a backlog_client, stage should be a no-op."""
        context = {"niche_id": "gaming", "stories": []}
        stage = FetchInsights()
        result = stage.execute(context)
        assert result is context

    def test_skips_too_old(self):
        """Posts published more than 7 days ago should be skipped."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc", "youtube", "gaming", hours_ago=200),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)
        assert result["run_stats"]["insights"]["skipped"] == 1

    def test_marks_fetched_in_sharepoint(self):
        """After successful fetch, should mark record with metrics_fetched timestamp."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc123", "youtube", "gaming", hours_ago=12),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        # Mock _fetch_platform to return metrics
        stage._fetch_platform = MagicMock(return_value={"views": 100, "likes": 10})

        result = stage.execute(context)

        # Should have called publishing_analytics.update to mark as fetched
        mock_client.publishing_analytics.update.assert_called_once()
        call_args = mock_client.publishing_analytics.update.call_args
        assert call_args[0][0] == "rec_yt_abc123"
        assert "metrics_fetched" in call_args[0][1]

    def test_query_exception_returns_context_with_error(self):
        """If SharePoint query fails, return context with error stats."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.side_effect = Exception("SP down")

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)

        stats = result["run_stats"]["insights"]
        assert stats["errors"] == 1
        assert stats["fetched"] == 0

    def test_skips_records_without_post_id(self):
        """Records missing post_id should be skipped."""
        mock_client = MagicMock()
        pub_dt = datetime.now(timezone.utc) - timedelta(hours=12)
        mock_client.publishing_analytics.all.return_value = [
            {
                "id": "rec_no_post",
                "fields": {
                    "post_id": "",
                    "platform": "youtube",
                    "niche_id": "gaming",
                    "published_at": pub_dt.isoformat(),
                    "metrics_fetched": "",
                },
            },
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)
        assert result["run_stats"]["insights"]["skipped"] == 1
