"""Tests for the FetchInsights pipeline stage."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from genlab_core.pipeline.stages.fetch_insights import (
    FetchInsights,
    normalize_publishing_metrics,
)


def _make_pub_record(post_id, platform, niche_id, hours_ago=12, metrics_fetched=""):
    pub_dt = datetime.now(UTC) - timedelta(hours=hours_ago)
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

    def test_writes_raw_metrics_back_to_publishing_analytics(self):
        """Fetched metrics must be persisted to publishing_analytics columns,
        not just the metrics_fetched timestamp (else views/likes stay 0)."""
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
        stage._fetch_platform = MagicMock(return_value={"views": 500, "likes": 40, "comments": 7})
        stage.execute(context)

        mock_client.publishing_analytics.update.assert_called_once()
        _rec_id, payload = mock_client.publishing_analytics.update.call_args[0]
        assert payload["views"] == 500
        assert payload["likes"] == 40
        assert payload["comments"] == 7
        assert payload["metrics_fetched"]  # timestamp still set

    def test_skips_already_fetched(self):
        """Posts with metrics_fetched set should be skipped."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record(
                "yt_abc",
                "youtube",
                "gaming",
                hours_ago=12,
                metrics_fetched="2026-03-16T12:00:00+00:00",
            ),
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

        stage.execute(context)

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
        pub_dt = datetime.now(UTC) - timedelta(hours=12)
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


class TestNormalizePublishingMetrics:
    """Per-platform metric keys must collapse onto canonical columns."""

    def test_youtube_keys(self):
        out = normalize_publishing_metrics({"views": 500, "likes": 40, "comments": 7})
        assert out == {"views": 500, "likes": 40, "comments": 7, "shares": 0, "saves": 0}

    def test_instagram_keys(self):
        # IG uses reach (->views) and saved (->saves)
        out = normalize_publishing_metrics(
            {"reach": 1200, "likes": 90, "comments": 12, "shares": 3, "saved": 25}
        )
        assert out["views"] == 1200
        assert out["saves"] == 25
        assert out["shares"] == 3

    def test_facebook_keys(self):
        # FB uses reactions (->likes), has no views
        out = normalize_publishing_metrics({"reactions": 55, "comments": 4, "shares": 8})
        assert out["likes"] == 55
        assert out["views"] == 0
        assert out["shares"] == 8

    def test_twitter_keys(self):
        # X uses impressions (->views), retweets (->shares), replies (->comments)
        out = normalize_publishing_metrics(
            {"impressions": 3000, "likes": 20, "retweets": 6, "replies": 2}
        )
        assert out["views"] == 3000
        assert out["shares"] == 6
        assert out["comments"] == 2

    def test_non_numeric_and_missing_safe(self):
        out = normalize_publishing_metrics({"views": None, "likes": "oops"})
        assert out == {"views": 0, "likes": 0, "comments": 0, "shares": 0, "saves": 0}
