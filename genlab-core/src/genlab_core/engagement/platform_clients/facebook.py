"""Facebook Graph API engagement client.

Auth: META_ACCESS_TOKEN from env (EAA Page Token — never refresh).
Same infrastructure as Instagram (both use Meta Graph API).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def post_facebook_reply(comment_id: str, text: str) -> bool:
    """Reply to a Facebook comment via Graph API.

    Returns True if reply was posted, False on failure.
    """
    try:
        import requests

        token = os.environ["META_ACCESS_TOKEN"]
        resp = requests.post(
            f"https://graph.facebook.com/v21.0/{comment_id}/comments",
            data={"message": text, "access_token": token},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Facebook: posted reply to comment %s", comment_id)
        return True
    except Exception as e:
        logger.warning("Facebook: failed to reply to %s: %s", comment_id, e)
        return False
