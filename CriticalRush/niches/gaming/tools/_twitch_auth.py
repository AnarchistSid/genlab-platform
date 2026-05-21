"""Shared Twitch OAuth2 token manager.

Used by both the IGDB client and the Twitch clip fetcher
so we don't duplicate the client_credentials OAuth logic.

Usage:
    manager = TwitchTokenManager(client_id, client_secret)
    token = manager.get_token()

    # Startup validation (fail-fast before pipeline work begins)
    validate_twitch_token(client_id, client_secret)
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


class TwitchAuthError(RuntimeError):
    """Raised when Twitch/IGDB authentication fails."""


class TwitchTokenManager:
    """Manage Twitch/IGDB OAuth2 client_credentials tokens with caching."""

    TOKEN_URL = "https://id.twitch.tv/oauth2/token"

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expiry: float = 0.0

    def get_token(self) -> str:
        """Return a valid access token, refreshing if needed.

        Caches the token until 60 seconds before expiry.
        Raises RuntimeError if credentials are empty.
        """
        if not self._client_id or not self._client_secret:
            raise TwitchAuthError(
                "Twitch credentials not configured: set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET"
            )

        if self._token and time.time() < self._expiry:
            return self._token

        logger.debug("[TwitchAuth] Requesting new token")
        try:
            resp = requests.post(
                self.TOKEN_URL,
                params={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            # Clear stale token so subsequent calls don't silently reuse it
            self._token = None
            self._expiry = 0.0
            raise TwitchAuthError(
                f"Twitch/IGDB token refresh failed: {e}. "
                "Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET env vars. "
                "Pipeline cannot continue without valid credentials."
            ) from e

        self._token = data["access_token"]
        self._expiry = time.time() + data.get("expires_in", 3600) - 60
        logger.debug("[TwitchAuth] Token acquired, expires in %ds", data.get("expires_in", 0))
        return self._token


def validate_twitch_token(client_id: str, client_secret: str) -> str:
    """Startup validation: fetch a token and return it, or raise TwitchAuthError.

    Call this at pipeline startup to fail fast before doing any real work.
    """
    mgr = TwitchTokenManager(client_id, client_secret)
    return mgr.get_token()
