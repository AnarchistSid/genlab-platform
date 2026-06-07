"""Tests for :mod:`genlab_core.publishing.preflight`.

Lives next to its module home (paralleling the PR 4/N extraction in
the publish_all_platforms decomposition). The existing
``test_publish_all_platforms.TestPreflightTokenRefresh`` class covers
the orchestrator integration; this file focuses on the per-platform
branches of ``resolve_client_kwargs`` that the integration tests don't
fully exercise.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.publishing.preflight import (
    preflight_token_refresh,
    resolve_client_kwargs,
)

# ---------------------------------------------------------------------------
# resolve_client_kwargs — one branch per platform
# ---------------------------------------------------------------------------


class TestResolveClientKwargsInstagram:
    @patch("genlab_core.publishing.preflight.resolve_meta_credentials")
    def test_returns_kwargs_when_creds_complete(self, mock_creds) -> None:
        mock_creds.return_value = {"ig_access_token": "tok", "ig_user_id": "u123"}
        assert resolve_client_kwargs("instagram", "gaming") == {
            "access_token": "tok",
            "ig_user_id": "u123",
        }

    @patch("genlab_core.publishing.preflight.resolve_meta_credentials")
    def test_returns_none_when_token_missing(self, mock_creds) -> None:
        mock_creds.return_value = {"ig_access_token": "", "ig_user_id": "u123"}
        assert resolve_client_kwargs("instagram", "gaming") is None

    @patch("genlab_core.publishing.preflight.resolve_meta_credentials")
    def test_returns_none_when_user_id_missing(self, mock_creds) -> None:
        mock_creds.return_value = {"ig_access_token": "tok", "ig_user_id": ""}
        assert resolve_client_kwargs("instagram", "gaming") is None


class TestResolveClientKwargsFacebook:
    @patch("genlab_core.publishing.preflight.resolve_fb_credentials")
    def test_returns_kwargs_when_creds_complete(self, mock_creds) -> None:
        mock_creds.return_value = ("tok", "page123")
        assert resolve_client_kwargs("facebook", "gaming") == {
            "access_token": "tok",
            "page_id": "page123",
        }

    @patch("genlab_core.publishing.preflight.resolve_fb_credentials")
    def test_returns_none_when_either_missing(self, mock_creds) -> None:
        mock_creds.return_value = ("", "page123")
        assert resolve_client_kwargs("facebook", "gaming") is None
        mock_creds.return_value = ("tok", "")
        assert resolve_client_kwargs("facebook", "gaming") is None


class TestResolveClientKwargsYouTube:
    @patch("genlab_core.publishing.preflight.resolve_niche_env", return_value="")
    @patch("genlab_core.publishing.preflight.resolve_youtube_credentials")
    def test_full_creds_returns_full_kwargs(self, mock_creds, _mock_env) -> None:
        mock_creds.return_value = {
            "refresh_token": "rt",
            "client_id": "cid",
            "client_secret": "csec",
        }
        result = resolve_client_kwargs("youtube", "gaming")
        assert result == {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rt",
        }

    @patch(
        "genlab_core.publishing.preflight.resolve_niche_env",
        return_value="UCxxx",
    )
    @patch("genlab_core.publishing.preflight.resolve_youtube_credentials")
    def test_expected_channel_added_when_resolvable(self, mock_creds, _mock_env) -> None:
        """When ``{PREFIX}_YT_CHANNEL_ID`` is set, expected_channel_id flows
        into the kwargs. This is the cross-channel-publish guard for YT —
        the client refuses to upload if the resolved channel ID doesn't match."""
        mock_creds.return_value = {
            "refresh_token": "rt",
            "client_id": "cid",
            "client_secret": "csec",
        }
        result = resolve_client_kwargs("youtube", "gaming")
        assert result["expected_channel_id"] == "UCxxx"

    @patch("genlab_core.publishing.preflight.resolve_niche_env", return_value="")
    @patch("genlab_core.publishing.preflight.resolve_youtube_credentials")
    def test_missing_refresh_token_returns_none(self, mock_creds, _mock_env) -> None:
        mock_creds.return_value = {"refresh_token": "", "client_id": "cid", "client_secret": "csec"}
        assert resolve_client_kwargs("youtube", "gaming") is None

    @patch.dict("os.environ", {"YOUTUBE_CLIENT_ID": "env_cid"}, clear=False)
    @patch("genlab_core.publishing.preflight.resolve_niche_env", return_value="")
    @patch("genlab_core.publishing.preflight.resolve_youtube_credentials")
    def test_client_id_falls_back_to_env(self, mock_creds, _mock_env) -> None:
        """Per-niche creds can omit client_id/secret — the global
        YOUTUBE_CLIENT_ID env var fills it in. Avoids forcing every niche
        to re-declare the same Aspire OAuth app credentials."""
        mock_creds.return_value = {
            "refresh_token": "rt",
            "client_id": "",
            "client_secret": "csec",
        }
        with patch.dict("os.environ", {"YOUTUBE_CLIENT_ID": "env_cid"}, clear=False):
            result = resolve_client_kwargs("youtube", "gaming")
        assert result["client_id"] == "env_cid"


class TestResolveClientKwargsXTwitter:
    @patch("genlab_core.publishing.preflight.resolve_twitter_credentials")
    def test_full_creds_pass_through(self, mock_creds) -> None:
        mock_creds.return_value = {
            "api_key": "k",
            "api_secret": "s",
            "access_token": "at",
            "access_token_secret": "ats",
        }
        assert resolve_client_kwargs("x_twitter", "gaming") == mock_creds.return_value

    @patch("genlab_core.publishing.preflight.resolve_twitter_credentials")
    def test_any_empty_value_returns_none(self, mock_creds) -> None:
        """X needs all 4 OAuth1.0a credentials — partial is no good
        because authentication fails atomically."""
        mock_creds.return_value = {
            "api_key": "k",
            "api_secret": "",
            "access_token": "at",
            "access_token_secret": "ats",
        }
        assert resolve_client_kwargs("x_twitter", "gaming") is None


class TestResolveClientKwargsThreads:
    @patch("genlab_core.publishing.preflight.resolve_threads_credentials")
    def test_returns_kwargs_when_complete(self, mock_creds) -> None:
        mock_creds.return_value = ("tok", "u123")
        assert resolve_client_kwargs("threads", "gaming") == {
            "access_token": "tok",
            "user_id": "u123",
        }

    @patch("genlab_core.publishing.preflight.resolve_threads_credentials")
    def test_returns_none_when_either_missing(self, mock_creds) -> None:
        mock_creds.return_value = ("", "u123")
        assert resolve_client_kwargs("threads", "gaming") is None


class TestResolveClientKwargsUnknownPlatform:
    def test_unknown_returns_empty_dict(self) -> None:
        """Pluggable for new platforms — empty kwargs lets a new registry
        entry be tested before its env-var schema is declared. NOT ``None``
        (which would skip the platform); the empty dict is "construct the
        client without per-niche kwargs"."""
        assert resolve_client_kwargs("myspace", "gaming") == {}


# ---------------------------------------------------------------------------
# preflight_token_refresh — orchestration of the Threads-only path
# ---------------------------------------------------------------------------


class TestPreflightTokenRefresh:
    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs")
    def test_only_threads_triggers_refresh(self, mock_kwargs, mock_get_client) -> None:
        mock_kwargs.return_value = {"access_token": "t", "user_id": "u"}
        mock_get_client.return_value = MagicMock()
        preflight_token_refresh("gaming", ["instagram", "youtube", "facebook", "x"])
        mock_get_client.assert_not_called()

    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs")
    def test_threads_in_list_triggers_refresh(self, mock_kwargs, mock_get_client) -> None:
        mock_kwargs.return_value = {"access_token": "t", "user_id": "u"}
        client = MagicMock()
        mock_get_client.return_value = client
        preflight_token_refresh("gaming", ["instagram", "threads"])
        mock_get_client.assert_called_once_with("threads", access_token="t", user_id="u")
        client.refresh_token_if_needed.assert_called_once()

    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs", return_value=None)
    def test_missing_creds_logs_warning_no_crash(self, mock_kwargs, mock_get_client) -> None:
        """A dark-channel-with-no-creds must NOT block other platforms;
        log a WARNING and continue."""
        preflight_token_refresh("gaming", ["threads"])
        mock_get_client.assert_not_called()

    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs")
    def test_refresh_exception_swallowed(self, mock_kwargs, mock_get_client) -> None:
        """An exception raised by the Threads client's refresh hook must
        NEVER propagate — publish proceeds (per-platform publish will surface
        a real auth failure as a SKIP regardless)."""
        mock_kwargs.return_value = {"access_token": "t", "user_id": "u"}
        client = MagicMock()
        client.refresh_token_if_needed.side_effect = RuntimeError("network down")
        mock_get_client.return_value = client
        preflight_token_refresh("gaming", ["threads"])  # must not raise

    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs")
    def test_client_without_refresh_hook_is_silent(self, mock_kwargs, mock_get_client) -> None:
        """If a future Threads client variant drops ``refresh_token_if_needed``,
        preflight must skip silently rather than AttributeError."""
        mock_kwargs.return_value = {"access_token": "t", "user_id": "u"}
        client = MagicMock(spec=[])  # no attributes — refresh_token_if_needed missing
        mock_get_client.return_value = client
        preflight_token_refresh("gaming", ["threads"])  # must not raise

    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs")
    def test_empty_platform_list_does_nothing(self, mock_kwargs, mock_get_client) -> None:
        preflight_token_refresh("gaming", [])
        mock_get_client.assert_not_called()
        mock_kwargs.assert_not_called()

    @patch("genlab_core.publishing.preflight.get_client")
    @patch("genlab_core.publishing.preflight.resolve_client_kwargs")
    @pytest.mark.parametrize("alias", ["twitter", "x", "yt", "fb", "tt", "ig"])
    def test_legacy_platform_aliases_resolved_via_to_registry_id(
        self, mock_kwargs, mock_get_client, alias: str
    ) -> None:
        """The preflight loop uses to_registry_id() — legacy aliases must
        route to their canonical IDs. None of these aliases map to
        ``threads``, so none triggers get_client(). This catches a future
        alias bug where a new platform-name silently maps to ``threads``
        (or vice versa)."""
        preflight_token_refresh("gaming", [alias])
        mock_get_client.assert_not_called()
