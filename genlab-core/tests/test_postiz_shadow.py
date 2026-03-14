"""Tests for PostizClient and ShadowPublisher."""
import os
from unittest.mock import MagicMock, patch
from genlab_core.platforms.postiz import (
    PostizClient, PublishResult, ShadowPublisher,
)


class TestShadowPublisher:
    def test_disabled_by_default(self):
        """Shadow mode is off when POSTIZ_SHADOW_MODE is not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POSTIZ_SHADOW_MODE", None)
            shadow = ShadowPublisher()
            assert not shadow.enabled

    def test_primary_runs_even_when_shadow_disabled(self):
        primary_fn = MagicMock(return_value="primary_ok")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POSTIZ_SHADOW_MODE", None)
            shadow = ShadowPublisher()
            result = shadow.publish_with_shadow(primary_fn, platform="ig")
            assert result["primary"] == "primary_ok"
            assert result["shadow"] is None
            primary_fn.assert_called_once()

    def test_shadow_failure_does_not_affect_primary(self):
        """Shadow errors are swallowed — primary result is returned."""
        primary_fn = MagicMock(return_value="primary_ok")
        mock_client = MagicMock(spec=PostizClient)
        mock_client.health_check.return_value = True
        mock_client.publish.side_effect = RuntimeError("boom")

        with patch.dict(os.environ, {"POSTIZ_SHADOW_MODE": "true"}):
            shadow = ShadowPublisher(postiz_client=mock_client)
            result = shadow.publish_with_shadow(
                primary_fn, platform="ig", integration_id="test_ig",
            )
            assert result["primary"] == "primary_ok"
            assert result["shadow"] is None  # Exception swallowed

    def test_shadow_success_returns_publish_result(self):
        primary_fn = MagicMock(return_value="primary_ok")
        mock_client = MagicMock(spec=PostizClient)
        mock_client.health_check.return_value = True
        mock_client.publish.return_value = PublishResult(
            platform="instagram", success=True, post_id="p123"
        )

        with patch.dict(os.environ, {"POSTIZ_SHADOW_MODE": "true"}):
            shadow = ShadowPublisher(postiz_client=mock_client)
            result = shadow.publish_with_shadow(
                primary_fn, platform="instagram",
                integration_id="ig_123", caption="test",
            )
            assert result["primary"] == "primary_ok"
            assert result["shadow"].success
            assert result["shadow"].post_id == "p123"

    def test_disabled_when_postiz_unreachable(self):
        mock_client = MagicMock(spec=PostizClient)
        mock_client.health_check.return_value = False

        with patch.dict(os.environ, {"POSTIZ_SHADOW_MODE": "true"}):
            shadow = ShadowPublisher(postiz_client=mock_client)
            assert not shadow.enabled

    def test_no_shadow_without_integration_id(self):
        """Shadow publish is skipped if no integration_id provided."""
        primary_fn = MagicMock(return_value="ok")
        mock_client = MagicMock(spec=PostizClient)
        mock_client.health_check.return_value = True

        with patch.dict(os.environ, {"POSTIZ_SHADOW_MODE": "true"}):
            shadow = ShadowPublisher(postiz_client=mock_client)
            result = shadow.publish_with_shadow(primary_fn, platform="ig")
            assert result["shadow"] is None
            mock_client.publish.assert_not_called()
