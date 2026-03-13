"""Tests for ShadowPublisher wiring — Sprint 22 Job 2.

Validates that:
1. Shadow comparison events are logged as parseable JSON
2. Primary always executes before shadow
3. Shadow failures never propagate
4. Primary results are never affected by shadow failures
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.platform.postiz_client import PublishResult, ShadowPublisher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _LogCapture(logging.Handler):
    """Minimal log handler that captures records for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def messages(self) -> List[str]:
        return [self.format(r) for r in self.records]


def _make_shadow_publisher(
    *,
    enabled: bool = True,
    postiz_client: Any = None,
) -> ShadowPublisher:
    """Create a ShadowPublisher with controlled enabled/client state."""
    sp = ShadowPublisher.__new__(ShadowPublisher)
    sp._enabled = enabled
    sp._postiz = postiz_client
    return sp


def _make_publish_result(
    *,
    success: bool = True,
    post_id: str = "shadow_123",
    error: str = "",
    platform: str = "instagram",
) -> PublishResult:
    return PublishResult(
        platform=platform,
        success=success,
        post_id=post_id,
        error=error,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestShadowPublisherLogsComparisonEvent:
    """test_shadow_publisher_logs_comparison_event — verify JSON log emitted."""

    def test_shadow_publisher_logs_comparison_event(self) -> None:
        handler = _LogCapture()
        shadow_logger = logging.getLogger("genlab.shadow_eval")
        shadow_logger.addHandler(handler)
        shadow_logger.setLevel(logging.DEBUG)
        try:
            mock_postiz = MagicMock()
            mock_postiz.publish.return_value = _make_publish_result(success=True)

            sp = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz)

            sp.publish_with_shadow(
                lambda: "primary_post_id_42",
                platform="instagram",
                caption="test caption",
                content_id="cid_001",
                integration_id="ig_test",
            )

            # At least one shadow_compare event must be in the log
            shadow_msgs = [m for m in handler.messages if "shadow_compare" in m]
            assert len(shadow_msgs) >= 1, f"Expected shadow_compare log, got: {handler.messages}"
        finally:
            shadow_logger.removeHandler(handler)


class TestShadowPublishCallsPrimaryFirst:
    """test_shadow_publish_calls_primary_first — primary executes before shadow."""

    def test_shadow_publish_calls_primary_first(self) -> None:
        call_order: List[str] = []

        def primary_fn() -> str:
            call_order.append("primary")
            return "primary_result"

        mock_postiz = MagicMock()

        def shadow_publish(**kwargs: Any) -> PublishResult:
            call_order.append("shadow")
            return _make_publish_result(success=True)

        mock_postiz.publish.side_effect = shadow_publish

        sp = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz)

        result = sp.publish_with_shadow(
            primary_fn,
            platform="instagram",
            caption="test",
            content_id="cid_002",
            integration_id="ig_test",
        )

        assert call_order[0] == "primary", f"Primary must run first, got: {call_order}"
        assert result["primary"] == "primary_result"


class TestShadowFailsSilentlyWhenPostizUnavailable:
    """test_shadow_fails_silently_when_postiz_unavailable."""

    def test_shadow_fails_silently_when_postiz_unavailable(self) -> None:
        """Shadow failure (Postiz raises) must not propagate to caller."""
        mock_postiz = MagicMock()
        mock_postiz.publish.side_effect = ConnectionError("Postiz is down")

        sp = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz)

        # Must NOT raise
        result = sp.publish_with_shadow(
            lambda: "primary_ok",
            platform="instagram",
            caption="test",
            content_id="cid_003",
            integration_id="ig_test",
        )

        assert result["primary"] == "primary_ok"
        assert result["shadow"] is None  # shadow failed, so None

    def test_shadow_disabled_no_postiz_call(self) -> None:
        """When shadow is disabled, Postiz is never called."""
        mock_postiz = MagicMock()
        sp = _make_shadow_publisher(enabled=False, postiz_client=mock_postiz)

        result = sp.publish_with_shadow(
            lambda: "primary_ok",
            platform="instagram",
            caption="test",
            content_id="cid_004",
            integration_id="ig_test",
        )

        mock_postiz.publish.assert_not_called()
        assert result["primary"] == "primary_ok"
        assert result["shadow"] is None


class TestShadowJsonLogFormatIsParseable:
    """test_shadow_json_log_format_is_parseable — logged JSON can be parsed."""

    def test_shadow_json_log_format_is_parseable(self) -> None:
        handler = _LogCapture()
        shadow_logger = logging.getLogger("genlab.shadow_eval")
        shadow_logger.addHandler(handler)
        shadow_logger.setLevel(logging.DEBUG)
        try:
            mock_postiz = MagicMock()
            mock_postiz.publish.return_value = _make_publish_result(
                success=True, post_id="p_999",
            )

            sp = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz)

            sp.publish_with_shadow(
                lambda: "primary_id_77",
                platform="youtube",
                caption="yt test",
                content_id="cid_005",
                integration_id="yt_test",
            )

            shadow_msgs = [m for m in handler.messages if "shadow_compare" in m]
            assert shadow_msgs, "No shadow_compare log found"

            parsed = json.loads(shadow_msgs[0])
            assert parsed["event"] == "shadow_compare"
            assert parsed["platform"] == "youtube"
            assert parsed["content_id"] == "cid_005"
            assert parsed["primary_ok"] is True
            assert parsed["shadow_ok"] is True
            assert parsed["shadow_post_id"] == "p_999"
            assert parsed["shadow_error"] is None
        finally:
            shadow_logger.removeHandler(handler)

    def test_shadow_json_log_captures_failure(self) -> None:
        handler = _LogCapture()
        shadow_logger = logging.getLogger("genlab.shadow_eval")
        shadow_logger.addHandler(handler)
        shadow_logger.setLevel(logging.DEBUG)
        try:
            mock_postiz = MagicMock()
            mock_postiz.publish.return_value = _make_publish_result(
                success=False, post_id="", error="rate limited",
            )

            sp = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz)

            sp.publish_with_shadow(
                lambda: "primary_id_88",
                platform="twitter",
                caption="tw test",
                content_id="cid_006",
                integration_id="tw_test",
            )

            shadow_msgs = [m for m in handler.messages if "shadow_compare" in m]
            assert shadow_msgs, "No shadow_compare log found"

            parsed = json.loads(shadow_msgs[0])
            assert parsed["shadow_ok"] is False
            assert parsed["shadow_error"] == "rate limited"
        finally:
            shadow_logger.removeHandler(handler)


class TestShadowPrimaryResultUnchangedOnShadowFailure:
    """test_shadow_primary_result_unchanged_on_shadow_failure."""

    def test_shadow_primary_result_unchanged_on_shadow_failure(self) -> None:
        """Primary result must be identical whether shadow succeeds or fails."""
        mock_postiz_ok = MagicMock()
        mock_postiz_ok.publish.return_value = _make_publish_result(success=True)

        mock_postiz_fail = MagicMock()
        mock_postiz_fail.publish.side_effect = RuntimeError("Postiz exploded")

        primary_value = "primary_result_abc"

        sp_ok = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz_ok)
        result_ok = sp_ok.publish_with_shadow(
            lambda: primary_value,
            platform="instagram",
            caption="test",
            content_id="cid_007",
            integration_id="ig_test",
        )

        sp_fail = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz_fail)
        result_fail = sp_fail.publish_with_shadow(
            lambda: primary_value,
            platform="instagram",
            caption="test",
            content_id="cid_008",
            integration_id="ig_test",
        )

        assert result_ok["primary"] == result_fail["primary"] == primary_value

    def test_primary_none_result_preserved(self) -> None:
        """When primary returns None, shadow does not alter this."""
        mock_postiz = MagicMock()
        mock_postiz.publish.return_value = _make_publish_result(success=True)

        sp = _make_shadow_publisher(enabled=True, postiz_client=mock_postiz)
        result = sp.publish_with_shadow(
            lambda: None,
            platform="instagram",
            caption="test",
            content_id="cid_009",
            integration_id="ig_test",
        )

        assert result["primary"] is None
