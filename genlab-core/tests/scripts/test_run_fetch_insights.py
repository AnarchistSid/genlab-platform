"""Tests for the deferred fetch_insights runner script."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

from genlab_core.scripts.run_fetch_insights import (
    _post_age_hours,
    _get_eligible_records,
    _fetch_platform_insights,
    _mark_window_completed,
    fetch_insights_for_window,
    WINDOW_RANGES,
)


class TestPostAgeHours:
    def test_iso_string(self):
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        age = _post_age_hours(one_hour_ago)
        assert age is not None
        assert 0.9 < age < 1.1

    def test_datetime_object(self):
        six_hours_ago = datetime.now(timezone.utc) - timedelta(hours=6)
        age = _post_age_hours(six_hours_ago)
        assert age is not None
        assert 5.9 < age < 6.1

    def test_none_returns_none(self):
        assert _post_age_hours(None) is None
        assert _post_age_hours("") is None

    def test_invalid_string(self):
        assert _post_age_hours("not-a-date") is None

    def test_z_suffix(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _post_age_hours(ts)
        assert age is not None
        assert 2.9 < age < 3.1


class TestWindowRanges:
    def test_6h_window_defined(self):
        assert 6 in WINDOW_RANGES
        min_age, max_age = WINDOW_RANGES[6]
        assert min_age == 5.0
        assert max_age == 7.0

    def test_24h_window_defined(self):
        assert 24 in WINDOW_RANGES
        min_age, max_age = WINDOW_RANGES[24]
        assert min_age == 23.0
        assert max_age == 25.0


class TestGetEligibleRecords:
    def _make_record(self, post_id, platform, niche_id, hours_ago, windows_completed=""):
        published_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        return {
            "id": f"rec_{post_id}",
            "fields": {
                "post_id": post_id,
                "platform": platform,
                "niche_id": niche_id,
                "published_at": published_at,
                "status": "SUCCESS",
                "insight_windows_completed": windows_completed,
            },
        }

    def test_6h_window_filters_correctly(self):
        """Only posts 5-7h old should be eligible for 6h window."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("p1", "instagram", "anime", 4),    # too recent
            self._make_record("p2", "instagram", "anime", 6),    # in window
            self._make_record("p3", "youtube", "anime", 6.5),    # in window
            self._make_record("p4", "facebook", "anime", 8),     # too old
            self._make_record("p5", "instagram", "anime", 24),   # way too old
        ]

        eligible = _get_eligible_records(client, "anime", 6)
        post_ids = [r[0]["fields"]["post_id"] for r in eligible]
        assert "p2" in post_ids
        assert "p3" in post_ids
        assert "p1" not in post_ids
        assert "p4" not in post_ids
        assert "p5" not in post_ids

    def test_24h_window_filters_correctly(self):
        """Only posts 23-25h old should be eligible for 24h window."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("p1", "instagram", "anime", 22),   # too recent
            self._make_record("p2", "instagram", "anime", 24),   # in window
            self._make_record("p3", "youtube", "anime", 23.5),   # in window
            self._make_record("p4", "facebook", "anime", 26),    # too old
        ]

        eligible = _get_eligible_records(client, "anime", 24)
        post_ids = [r[0]["fields"]["post_id"] for r in eligible]
        assert "p2" in post_ids
        assert "p3" in post_ids
        assert "p1" not in post_ids
        assert "p4" not in post_ids

    def test_idempotent_skip(self):
        """Posts with window already completed should be skipped."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("p1", "instagram", "anime", 6, windows_completed="6h"),
            self._make_record("p2", "instagram", "anime", 6, windows_completed=""),
        ]

        eligible = _get_eligible_records(client, "anime", 6)
        post_ids = [r[0]["fields"]["post_id"] for r in eligible]
        assert "p1" not in post_ids
        assert "p2" in post_ids

    def test_no_post_id_skipped(self):
        """Records without post_id should be skipped."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("", "instagram", "anime", 6),
        ]
        eligible = _get_eligible_records(client, "anime", 6)
        assert len(eligible) == 0

    def test_query_failure_returns_empty(self):
        """API failure should return empty list, not crash."""
        client = MagicMock()
        client.publishing_analytics.all.side_effect = Exception("Network error")
        eligible = _get_eligible_records(client, "anime", 6)
        assert eligible == []


class TestFetchPlatformInsights:
    @patch("genlab_core.scripts.run_fetch_insights._fetch_instagram")
    def test_instagram_dispatched(self, mock_ig):
        mock_ig.return_value = {"likes": 42, "reach": 100}
        result = _fetch_platform_insights("instagram", "123")
        mock_ig.assert_called_once_with("123")
        assert result["likes"] == 42

    @patch("genlab_core.scripts.run_fetch_insights._fetch_youtube")
    def test_youtube_dispatched(self, mock_yt):
        mock_yt.return_value = {"views": 500}
        result = _fetch_platform_insights("youtube", "abc")
        mock_yt.assert_called_once_with("abc")
        assert result["views"] == 500

    @patch("genlab_core.scripts.run_fetch_insights._fetch_facebook")
    def test_facebook_dispatched(self, mock_fb):
        mock_fb.return_value = {"shares": 10}
        result = _fetch_platform_insights("facebook", "fb1")
        mock_fb.assert_called_once_with("fb1")

    @patch("genlab_core.scripts.run_fetch_insights._fetch_twitter")
    def test_twitter_dispatched(self, mock_tw):
        mock_tw.return_value = {"likes": 5}
        result = _fetch_platform_insights("x", "tw1")
        mock_tw.assert_called_once_with("tw1")

    def test_unknown_platform_returns_none(self):
        result = _fetch_platform_insights("tiktok", "123")
        assert result is None

    @patch("genlab_core.scripts.run_fetch_insights._fetch_instagram")
    def test_platform_failure_is_nonfatal(self, mock_ig):
        """Exception in platform fetcher should not propagate."""
        mock_ig.side_effect = Exception("API crashed")
        result = _fetch_platform_insights("instagram", "123")
        assert result is None


class TestMarkWindowCompleted:
    def test_marks_first_window(self):
        client = MagicMock()
        _mark_window_completed(client, "rec1", "", 6)
        call_fields = client.publishing_analytics.update.call_args[0][1]
        assert "6h" in call_fields["insight_windows_completed"]

    def test_appends_to_existing(self):
        client = MagicMock()
        _mark_window_completed(client, "rec1", "6h", 24)
        call_fields = client.publishing_analytics.update.call_args[0][1]
        assert "6h,24h" == call_fields["insight_windows_completed"]

    def test_update_failure_is_nonfatal(self):
        client = MagicMock()
        client.publishing_analytics.update.side_effect = Exception("SP error")
        # Should not raise
        _mark_window_completed(client, "rec1", "", 6)


class TestFetchInsightsForWindow:
    @patch("genlab_core.scripts.run_fetch_insights._load_env_for_niche")
    @patch("genlab_core.scripts.run_fetch_insights.BacklogClient")
    @patch("genlab_core.scripts.run_fetch_insights._get_eligible_records")
    def test_dry_run_skips_api_calls(self, mock_eligible, MockClient, mock_env):
        mock_eligible.return_value = [
            ({"id": "r1", "fields": {"post_id": "p1", "platform": "instagram",
             "published_at": datetime.now(timezone.utc).isoformat(),
             "candidate_id": "c1"}}, "window_6h"),
        ]

        stats = fetch_insights_for_window("anime", 6, dry_run=True)
        assert stats["dry_run"] is True
        assert stats["fetched"] == 0
        # Should NOT have called upsert_analytics
        MockClient.return_value.upsert_analytics.assert_not_called()

    @patch("genlab_core.scripts.run_fetch_insights._load_env_for_niche")
    @patch("genlab_core.scripts.run_fetch_insights.BacklogClient")
    @patch("genlab_core.scripts.run_fetch_insights._get_eligible_records")
    @patch("genlab_core.scripts.run_fetch_insights._fetch_platform_insights")
    @patch("genlab_core.scripts.run_fetch_insights._mark_window_completed")
    @patch("genlab_core.scripts.run_fetch_insights.time")
    def test_fetches_and_writes(self, mock_time, mock_mark, mock_fetch, mock_eligible, MockClient, mock_env):
        published_at = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        mock_eligible.return_value = [
            ({"id": "r1", "fields": {
                "post_id": "p1", "platform": "instagram",
                "published_at": published_at, "candidate_id": "c1",
                "niche_id": "anime", "insight_windows_completed": "",
                "blueprint": [],
            }}, "window_6h"),
        ]
        mock_fetch.return_value = {"likes": 42, "reach": 100, "engagement": 50}

        stats = fetch_insights_for_window("anime", 6, dry_run=False)
        assert stats["fetched"] == 1
        assert stats["errors"] == 0
        MockClient.return_value.upsert_analytics.assert_called_once()
        mock_mark.assert_called_once()

    @patch("genlab_core.scripts.run_fetch_insights._load_env_for_niche")
    @patch("genlab_core.scripts.run_fetch_insights.BacklogClient")
    @patch("genlab_core.scripts.run_fetch_insights._get_eligible_records")
    @patch("genlab_core.scripts.run_fetch_insights._fetch_platform_insights")
    @patch("genlab_core.scripts.run_fetch_insights.time")
    def test_platform_failure_counted_as_error(self, mock_time, mock_fetch, mock_eligible, MockClient, mock_env):
        published_at = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        mock_eligible.return_value = [
            ({"id": "r1", "fields": {
                "post_id": "p1", "platform": "instagram",
                "published_at": published_at, "candidate_id": "c1",
                "niche_id": "anime", "insight_windows_completed": "",
            }}, "window_6h"),
        ]
        mock_fetch.return_value = None  # platform failure

        stats = fetch_insights_for_window("anime", 6, dry_run=False)
        assert stats["errors"] == 1
        assert stats["fetched"] == 0


class TestPerformanceLearnerTrigger:
    @patch("genlab_core.scripts.run_fetch_insights._load_env_for_niche")
    @patch("genlab_core.scripts.run_fetch_insights.BacklogClient")
    @patch("genlab_core.scripts.run_fetch_insights._get_eligible_records")
    @patch("genlab_core.scripts.run_fetch_insights._fetch_platform_insights")
    @patch("genlab_core.scripts.run_fetch_insights._mark_window_completed")
    @patch("genlab_core.scripts.run_fetch_insights._trigger_performance_learner")
    @patch("genlab_core.scripts.run_fetch_insights.time")
    def test_24h_triggers_performance_learner(
        self, mock_time, mock_learner, mock_mark, mock_fetch, mock_eligible, MockClient, mock_env
    ):
        """24h window should trigger performance_learner after fetching."""
        published_at = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        mock_eligible.return_value = [
            ({"id": "r1", "fields": {
                "post_id": "p1", "platform": "instagram",
                "published_at": published_at, "candidate_id": "c1",
                "niche_id": "anime", "insight_windows_completed": "",
                "blueprint": [],
            }}, "window_24h"),
        ]
        mock_fetch.return_value = {"likes": 10, "reach": 50, "engagement": 15}

        # Call main() with window=24
        from genlab_core.scripts.run_fetch_insights import main
        with patch("sys.argv", ["prog", "--niche-id", "anime", "--window", "24"]):
            main()

        mock_learner.assert_called_once_with("anime")
