"""Facebook comment reply client for the engagement engine.

Uses Meta Graph API via graph.facebook.com to reply to Facebook Page comments.
"""
from __future__ import annotations

import logging

import requests

from genlab_core.publishing.niche_credentials import resolve_meta_credentials

logger = logging.getLogger(__name__)


class FacebookReplyClient:
    """Post replies to Facebook comments via Meta Graph API."""

    BASE_URL = "https://graph.facebook.com/v21.0"

    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Reply to a Facebook comment.

        Args:
            comment_id: The comment ID to reply to.
            text: Reply text.
            niche_id: Niche identifier for credential resolution.

        Returns:
            True if the reply was posted successfully.
        """
        creds = resolve_meta_credentials(niche_id)
        access_token = creds.get("fb_access_token", "")

        if not access_token:
            logger.warning(
                "[FB Reply] No access token for niche %s", niche_id
            )
            return False

        try:
            resp = requests.post(
                f"{self.BASE_URL}/{comment_id}/comments",
                data={
                    "message": text,
                    "access_token": access_token,
                },
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("[FB Reply] Posted reply to %s for niche %s", comment_id, niche_id)
            return True

        except Exception:
            logger.exception("[FB Reply] Failed to post reply to %s", comment_id)
            return False
