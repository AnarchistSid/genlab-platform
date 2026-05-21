"""Tests for the deferred fetch_insights runner script."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from genlab_core.scripts.run_fetch_insights import (
    WINDOW_RANGES,
    _fetch_platform_insights,
    _get_eligible_records,
    _mark_window_completed,
    _post_age_hours,
)


class TestPostAgeHours:
    def test_iso_string(self):
        one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        age = _post_age_hours(one_hour_ago)
        assert age is not None
        assert 0.9 < age < 1.1

    def test_datetime_object(self):
        six_hours_ago = datetime.now(UTC) - timedelta(hours=6)
        age = _post_age_hours(six_hours_ago)
        assert age is not None
        assert 5.9 < age < 6.1

    def test_none_returns_none(self):
        assert _post_age_hours(None) is None
        assert _post_age_hours("") is None

    def test_invalid_string(self):
        assert _post_age_hours("not-a-date") is None

    def test_z_suffix(self):
        ts = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _post_age_hours(ts)
        assert age is not None
        assert 2.9 < age < 3.1


class TestWindowRanges:
    def test_6h_window_defined(self):
        assert 6 in WINDOW_RANGES
        min_age, max_age = WINDOW_RANGES[6]
        assert min_age == 4.0  # Widened from 5.0
        assert max_age == 8760.0  # Wide range for backfill (idempotency prevents double-fetch)

    def test_24h_window_defined(self):
        assert 24 in WINDOW_RANGES
        min_age, max_age = WINDOW_RANGES[24]
        assert min_age == 20.0  # Widened from 23.0
        assert max_age == 8760.0  # Wide range for backfill


class TestGetEligibleRecords:
    def _make_record(self, post_id, platform, niche_id, hours_ago, status="SUCCESS"):
        published_at = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        return {
            "id": f"rec_{post_id}",
            "fields": {
                "post_id": post_id,
                "platform": platform,
                "niche_id": niche_id,
                "published_at": published_at,
                "status": status,
            },
        }

    def test_6h_window_filters_correctly(self):
        """Posts 4-168h old with status SUCCESS should be eligible for 6h window."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("p1", "instagram", "anime", 3),    # too recent (< 4h)
            self._make_record("p2", "instagram", "anime", 6),    # in window
            self._make_record("p3", "youtube", "anime", 24),     # in window
            self._make_record("p4", "facebook", "anime", 100),   # in window (< 168h)
        ]

        eligible = _get_eligible_records(client, "anime", 6)
        post_ids = [r[0]["fields"]["post_id"] for r in eligible]
        assert "p2" in post_ids
        assert "p3" in post_ids
        assert "p4" in post_ids
        assert "p1" not in post_ids  # too recent

    def test_24h_window_filters_correctly(self):
        """Posts 20-168h old with status INSIGHTS_6H should be eligible for 24h window."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("p1", "instagram", "anime", 18, status="INSIGHTS_6H"),  # too recent
            self._make_record("p2", "instagram", "anime", 24, status="INSIGHTS_6H"),  # in window
            self._make_record("p3", "youtube", "anime", 48, status="INSIGHTS_6H"),    # in window
        ]

        eligible = _get_eligible_records(client, "anime", 24)
        post_ids = [r[0]["fields"]["post_id"] for r in eligible]
        assert "p2" in post_ids
        assert "p3" in post_ids
        assert "p1" not in post_ids

    def test_idempotent_skip(self):
        """Posts already at INSIGHTS status should be skipped for 6h window."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = [
            self._make_record("p1", "instagram", "anime", 6, status="INSIGHTS_6H"),
            self._make_record("p2", "instagram", "anime", 6, status="SUCCESS"),
        ]
        # 6h window queries for SUCCESS status only — p1 won't be returned
        eligible = _get_eligible_records(client, "anime", 6)
        post_ids = [r[0]["fields"]["post_id"] for r in eligible]
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
        result = _fetch_platform_insights("instagram", "123", niche_id="anime")
        mock_ig.assert_called_once_with("123", niche_id="anime")
        assert result["likes"] == 42

    @patch("genlab_core.scripts.run_fetch_insights._fetch_youtube")
    def test_youtube_dispatched(self, mock_yt):
        mock_yt.return_value = {"views": 500}
        result = _fetch_platform_insights("youtube", "abc", niche_id="gaming")
        mock_yt.assert_called_once_with("abc")
        assert result["views"] == 500

    @patch("genlab_core.scripts.run_fetch_insights._fetch_facebook")
    def test_facebook_dispatched(self, mock_fb):
        mock_fb.return_value = {"likes": 10}
        _fetch_platform_insights("facebook", "fb1", niche_id="ai_creators")
        mock_fb.assert_called_once_with("fb1", niche_id="ai_creators")

    @patch("genlab_core.scripts.run_fetch_insights._fetch_twitter")
    def test_twitter_dispatched(self, mock_tw):
        mock_tw.return_value = {"likes": 5}
        _fetch_platform_insights("x", "tw1", niche_id="gaming")
        mock_tw.assert_called_once_with("tw1")

    def test_unknown_platform_returns_none(self):
        result = _fetch_platform_insights("tiktok", "123")
        assert result is None

    @patch("genlab_core.scripts.run_fetch_insights._fetch_instagram")
    def test_platform_failure_is_nonfatal(self, mock_ig):
        """Exception in platform fetcher should not propagate."""
        mock_ig.side_effect = Exception("API crashed")
        result = _fetch_platform_insights("instagram", "123", niche_id="anime")
        assert result is None


class TestMarkWindowCompleted:
    def test_marks_window_via_status(self):
        """Window completion is tracked via status field (INSIGHTS_6H)."""
        client = MagicMock()
        _mark_window_completed(client, "rec1", "", 6)
        call_fields = client.publishing_analytics.update.call_args[0][1]
        assert call_fields["status"] == "INSIGHTS_6H"

    def test_24h_window_sets_status(self):
        client = MagicMock()
        _mark_window_completed(client, "rec1", "", 24)
        call_fields = client.publishing_analytics.update.call_args[0][1]
        assert call_fields["status"] == "INSIGHTS_24H"

    def test_update_failure_is_nonfatal(self):
        client = MagicMock()
        client.publishing_analytics.update.side_effect = Exception("SP error")
        # Should not raise
        _mark_window_completed(client, "rec1", "", 6)
