"""Tests for genlab_core.platforms.media_provider_health.

Auto-rotation for CDN upload providers. When a provider fails, mark
it unhealthy for a cool-down window so subsequent uploads skip it
proactively — instead of retrying every publisher fire and wasting
the failed provider's timeout budget.
"""

from __future__ import annotations

import time

from unittest.mock import patch


class TestHealthCheck:
    def setup_method(self):
        from genlab_core.platforms import media_provider_health

        media_provider_health.reset_all()

    def test_absence_of_evidence_is_healthy(self):
        from genlab_core.platforms.media_provider_health import is_provider_healthy

        assert is_provider_healthy("tmpfiles") is True

    def test_empty_provider_is_healthy(self):
        from genlab_core.platforms.media_provider_health import is_provider_healthy

        assert is_provider_healthy("") is True

    def test_mark_unhealthy_hides_provider(self):
        from genlab_core.platforms.media_provider_health import (
            is_provider_healthy,
            mark_provider_unhealthy,
        )

        mark_provider_unhealthy("tmpfiles", "500 gateway timeout")
        assert is_provider_healthy("tmpfiles") is False

    def test_cooldown_expiry_auto_recovers(self, monkeypatch):
        """When the cooldown period has passed, is_provider_healthy
        must clear the unhealthy state and return True."""
        from genlab_core.platforms import media_provider_health

        with patch.object(time, "time", return_value=1000.0):
            media_provider_health.mark_provider_unhealthy(
                "litterbox", "test", cooldown_s=60.0
            )

        # 30s in — still unhealthy
        with patch.object(time, "time", return_value=1030.0):
            assert media_provider_health.is_provider_healthy("litterbox") is False

        # 60.1s in — cooldown expired
        with patch.object(time, "time", return_value=1060.1):
            assert media_provider_health.is_provider_healthy("litterbox") is True


class TestSnapshot:
    def setup_method(self):
        from genlab_core.platforms import media_provider_health

        media_provider_health.reset_all()

    def test_healthy_snapshot(self):
        from genlab_core.platforms.media_provider_health import snapshot

        snap = snapshot("tmpfiles")
        assert snap.is_healthy is True
        assert snap.cool_down_remaining_s == 0.0
        assert snap.last_failure_reason == ""

    def test_unhealthy_snapshot_carries_reason(self):
        from genlab_core.platforms import media_provider_health

        media_provider_health.mark_provider_unhealthy(
            "tmpfiles", "unexpected content-type"
        )
        snap = media_provider_health.snapshot("tmpfiles")
        assert snap.is_healthy is False
        assert snap.cool_down_remaining_s > 0.0
        assert "unexpected content-type" in snap.last_failure_reason

    def test_reason_truncated_at_200_chars(self):
        from genlab_core.platforms import media_provider_health

        long_reason = "x" * 500
        media_provider_health.mark_provider_unhealthy("tmpfiles", long_reason)
        snap = media_provider_health.snapshot("tmpfiles")
        assert len(snap.last_failure_reason) <= 200


class TestManualClear:
    def setup_method(self):
        from genlab_core.platforms import media_provider_health

        media_provider_health.reset_all()

    def test_clear_provider_marks_healthy(self):
        from genlab_core.platforms import media_provider_health

        media_provider_health.mark_provider_unhealthy("tmpfiles", "test")
        assert media_provider_health.is_provider_healthy("tmpfiles") is False
        media_provider_health.clear_provider("tmpfiles")
        assert media_provider_health.is_provider_healthy("tmpfiles") is True


class TestCdnUploadWire:
    """Source-grep pin: cdn_upload.upload_to_cdn_full must consult
    is_provider_healthy AND call mark_provider_unhealthy on failures.
    Otherwise the health tracking is dead code."""

    def test_upload_to_cdn_full_uses_health_module(self):
        import inspect

        from genlab_core.platforms import cdn_upload

        src = inspect.getsource(cdn_upload.upload_to_cdn_full)
        assert "is_provider_healthy" in src, (
            "upload_to_cdn_full must call is_provider_healthy on each "
            "tier — otherwise cool-down state is ignored."
        )
        assert "mark_provider_unhealthy" in src, (
            "upload_to_cdn_full must call mark_provider_unhealthy on "
            "failed provider attempts — otherwise nothing ever gets "
            "cooled down."
        )

    def test_all_three_tiers_get_health_gate(self):
        """Each of Tier 1/2/3 must have both an is_healthy check and
        a mark_unhealthy fallback path."""
        import inspect

        from genlab_core.platforms import cdn_upload

        src = inspect.getsource(cdn_upload.upload_to_cdn_full)
        # At least 3 is_provider_healthy occurrences (one per tier)
        assert src.count("is_provider_healthy(") >= 3
        # At least 3 mark_provider_unhealthy occurrences
        assert src.count("mark_provider_unhealthy(") >= 3
