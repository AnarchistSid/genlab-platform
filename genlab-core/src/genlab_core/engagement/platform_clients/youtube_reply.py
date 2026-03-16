"""YouTube comment reply client for the engagement engine.

Uses YouTube Data API v3 comments.insert to reply to comments.
Requires OAuth credentials (API key alone is not sufficient for writes).
"""
from __future__ import annotations

import logging

import requests

from genlab_core.publishing.niche_credentials import resolve_youtube_credentials

logger = logging.getLogger(__name__)


class YouTubeReplyClient:
    """Post replies to YouTube comments via Data API v3."""

    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Reply to a YouTube comment.

        Args:
            comment_id: The parent comment thread ID.
            text: Reply text.
            niche_id: Niche identifier for credential resolution.

        Returns:
            True if the reply was posted successfully.
        """
        creds = resolve_youtube_credentials(niche_id)
        client_id = creds.get("client_id", "")
        client_secret = creds.get("client_secret", "")
        refresh_token = creds.get("refresh_token", "")

        if not all([client_id, client_secret, refresh_token]):
            logger.warning(
                "[YT Reply] Missing OAuth credentials for niche %s", niche_id
            )
            return False

        try:
            # Step 1: Exchange refresh token for access token
            token_resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            # Step 2: Insert comment reply
            resp = requests.post(
                "https://www.googleapis.com/youtube/v3/comments",
                params={"part": "snippet"},
                json={
                    "snippet": {
                        "parentId": comment_id,
                        "textOriginal": text,
                    }
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("[YT Reply] Posted reply to %s for niche %s", comment_id, niche_id)
            return True

        except Exception:
            logger.exception("[YT Reply] Failed to post reply to %s", comment_id)
            return False
