"""Tests for _twitch_auth.py -- token management and startup validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from niches.gaming.tools._twitch_auth import (
    TwitchAuthError,
    TwitchTokenManager,
    validate_twitch_token,
)


class TestMissingCredentials:
    def test_empty_client_id_raises_auth_error(self):
        mgr = TwitchTokenManager("", "some_secret")
        with pytest.raises(TwitchAuthError, match="credentials not configured"):
            mgr.get_token()

    def test_empty_client_secret_raises_auth_error(self):
        mgr = TwitchTokenManager("some_id", "")
        with pytest.raises(TwitchAuthError, match="credentials not configured"):
            mgr.get_token()


class TestNetworkFailure:
    def test_network_error_raises_auth_error_and_clears_token(self):
        """A network error must raise TwitchAuthError and wipe the cached token."""
        mgr = TwitchTokenManager("id", "secret")
        # Seed a stale token to verify it gets cleared
        mgr._token = "stale_token"
        mgr._expiry = 0.0  # expired, so get_token will try to refresh

        with patch("niches.gaming.tools._twitch_auth.requests.post",
                    side_effect=ConnectionError("DNS failure")):
            with pytest.raises(TwitchAuthError, match="token refresh failed"):
                mgr.get_token()

        assert mgr._token is None, "Stale token must be cleared on failure"
        assert mgr._expiry == 0.0

    def test_http_401_raises_auth_error(self):
        """An HTTP 401 (invalid creds) must surface as TwitchAuthError."""
        mgr = TwitchTokenManager("id", "secret")

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")

        with patch("niches.gaming.tools._twitch_auth.requests.post",
                    return_value=mock_resp):
            with pytest.raises(TwitchAuthError, match="token refresh failed"):
                mgr.get_token()


class TestTokenCaching:
    def test_cached_token_returned_without_network_call(self):
        """A valid cached token must be returned without hitting the network."""
        mgr = TwitchTokenManager("id", "secret")
        mgr._token = "cached_token"
        mgr._expiry = 9999999999.0  # far future

        with patch("niches.gaming.tools._twitch_auth.requests.post") as mock_post:
            token = mgr.get_token()
            mock_post.assert_not_called()

        assert token == "cached_token"


class TestValidateHelper:
    def test_validate_twitch_token_delegates_to_manager(self):
        """validate_twitch_token is a convenience wrapper around TwitchTokenManager."""
        with patch.object(TwitchTokenManager, "get_token", return_value="tok_123") as mock:
            result = validate_twitch_token("id", "secret")
            mock.assert_called_once()
            assert result == "tok_123"

    def test_validate_twitch_token_propagates_auth_error(self):
        """validate_twitch_token must propagate TwitchAuthError, not swallow it."""
        with pytest.raises(TwitchAuthError):
            validate_twitch_token("", "")
