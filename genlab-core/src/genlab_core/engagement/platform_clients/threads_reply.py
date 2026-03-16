"""Threads reply client for the engagement engine.

Uses the Threads API (graph.threads.net) for posting replies.
Two-step process: create reply container, then publish it.
"""
from __future__ import annotations

import logging

import requests

from genlab_core.publishing.niche_credentials import resolve_threads_credentials

logger = logging.getLogger(__name__)


class ThreadsReplyClient:
    """Post replies to Threads comments via Threads API."""

    BASE_URL = "https://graph.threads.net/v1.0"

    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Reply to a Threads post/comment.

        Threads API uses a two-step process:
        1. Create a reply container (media_type=TEXT, reply_to_id)
        2. Publish the container

        Args:
            comment_id: The post/comment ID to reply to.
            text: Reply text.
            niche_id: Niche identifier for credential resolution.

        Returns:
            True if the reply was posted successfully.
        """
        access_token, user_id = resolve_threads_credentials(niche_id)

        if not access_token or not user_id:
            logger.warning(
                "[Threads Reply] Missing credentials for niche %s", niche_id
            )
            return False

        try:
            # Step 1: Create reply container
            create_resp = requests.post(
                f"{self.BASE_URL}/{user_id}/threads",
                data={
                    "media_type": "TEXT",
                    "text": text,
                    "reply_to_id": comment_id,
                    "access_token": access_token,
                },
                timeout=10,
            )
            create_resp.raise_for_status()
            container_id = create_resp.json().get("id")

            if not container_id:
                logger.warning("[Threads Reply] No container ID returned for %s", comment_id)
                return False

            # Step 2: Publish the container
            publish_resp = requests.post(
                f"{self.BASE_URL}/{user_id}/threads_publish",
                data={
                    "creation_id": container_id,
                    "access_token": access_token,
                },
                timeout=10,
            )
            publish_resp.raise_for_status()
            logger.info(
                "[Threads Reply] Posted reply to %s for niche %s",
                comment_id, niche_id,
            )
            return True

        except Exception:
            logger.exception("[Threads Reply] Failed to post reply to %s", comment_id)
            return False
