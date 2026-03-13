"""Instagram Graph API engagement client.

Auth: META_ACCESS_TOKEN from env (EAA Page Token — never refresh).
ALWAYS use graph.facebook.com (NEVER graph.instagram.com).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def post_instagram_reply(media_id: str, comment_id: str, text: str) -> bool:
    """Reply to an Instagram comment via Graph API.

    Returns True if reply was posted, False on failure.
    """
    try:
        import requests

        token = os.environ["META_ACCESS_TOKEN"]
        resp = requests.post(
            f"https://graph.facebook.com/v21.0/{comment_id}/replies",
            data={"message": text, "access_token": token},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(
            "Instagram: posted reply to comment %s on media %s",
            comment_id,
            media_id,
        )
        return True
    except Exception as e:
        logger.warning("Instagram: failed to reply to %s: %s", comment_id, e)
        return False


def like_instagram_comment(comment_id: str) -> None:
    """Like an Instagram comment via Graph API."""
    try:
        import requests

        token = os.environ["META_ACCESS_TOKEN"]
        resp = requests.post(
            f"https://graph.facebook.com/v21.0/{comment_id}/likes",
            data={"access_token": token},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Instagram: liked comment %s", comment_id)
    except Exception as e:
        logger.warning("Instagram: failed to like comment %s: %s", comment_id, e)
