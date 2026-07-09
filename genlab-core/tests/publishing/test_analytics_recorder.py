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

    # ── Task #625: post_id normalization pins ─────────────────────

    def test_post_id_normalized_via_canonical_helper(self):
        """Task #625 (2026-07-09) — audit follow-up to #624/#748.

        Pre-#625 this site used an inline ``f"{platform}:{post_id}"``
        which shared #748's no-idempotence-check class of bug. The
        fix routes through ``cache.post_id_norm.normalize_post_id``.
        """
        mock_client = MagicMock()
        record_publish(
            mock_client,
            niche_id="gaming",
            platform="youtube",
            status="SUCCESS",
            post_url="https://youtube.com/shorts/BlZaT02z0lw",
        )
        fields = mock_client.publishing_analytics.create.call_args[0][0]
        # Bare id extraction → gets prefixed with platform
        assert fields["post_id"] == "youtube:BlZaT02z0lw"

    def test_post_id_empty_url_writes_empty_string(self):
        """Preserve pre-#625 behavior for the missing-url case: the
        composite key stays ``""`` (not ``"youtube:"``) so downstream
        callers can distinguish "no post_id available" from a real
        composite key."""
        mock_client = MagicMock()
        record_publish(
            mock_client,
            niche_id="gaming",
            platform="youtube",
            status="FAILED",
        )
        fields = mock_client.publishing_analytics.create.call_args[0][0]
        assert fields["post_id"] == ""

    def test_uses_canonical_normalizer(self):
        """Grep-style module-level pin: analytics_recorder MUST
        import the canonical helper at module top. If a future edit
        re-inlines the ``f"{platform}:{post_id}"`` expression and
        drops the import, this pin fails and forces the reviewer
        to look at why."""
        import genlab_core.publishing.analytics_recorder as ar

        assert hasattr(ar, "normalize_post_id"), (
            "analytics_recorder must import normalize_post_id at "
            "module top. Do not re-inline the composite-key "
            "expression — task #625 documented this class of bug."
        )
