"""Tests for genlab_core.publishing.analytics_recorder."""
from unittest.mock import MagicMock

from genlab_core.publishing.analytics_recorder import record_publish


class TestRecordPublish:
    def test_success_record_writes_fields(self):
        mock_client = MagicMock()
        record_publish(
            mock_client,
            niche_id="gaming",
            platform="instagram",
            status="SUCCESS",
            post_url="https://example.com/post",
        )
        mock_client.publishing_analytics.create.assert_called_once()
        fields = mock_client.publishing_analytics.create.call_args[0][0]
        assert fields["status"] == "SUCCESS"
        assert fields["platform"] == "instagram"
        assert fields["niche_id"] == "gaming"
        assert fields["post_url"] == "https://example.com/post"

    def test_failure_record_includes_error_message(self):
        mock_client = MagicMock()
        record_publish(
            mock_client,
            niche_id="ai_creators",
            platform="youtube",
            status="FAILED",
            error_message="HTTPError: 403 Forbidden",
        )
        fields = mock_client.publishing_analytics.create.call_args[0][0]
        assert fields["status"] == "FAILED"
        assert fields["error_message"] == "HTTPError: 403 Forbidden"
        assert "error_log" not in fields  # must use error_message, not error_log

    def test_no_proxy_skips_silently(self):
        mock_client = MagicMock(spec=[])  # no publishing_analytics attr
        record_publish(mock_client, niche_id="gaming", platform="x", status="FAILED")
        # Should not raise

    def test_proxy_exception_does_not_raise(self):
        mock_client = MagicMock()
        mock_client.publishing_analytics.create.side_effect = RuntimeError("SP down")
        record_publish(mock_client, niche_id="gaming", platform="x", status="FAILED")
        # Should not raise
