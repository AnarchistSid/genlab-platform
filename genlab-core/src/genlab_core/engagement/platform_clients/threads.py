"""Threads API engagement client.

Auth: THREADS_ACCESS_TOKEN from env (falls back to META_ACCESS_TOKEN).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def post_threads_reply(thread_id: str, text: str) -> bool:
    """Reply to a Threads post.

    Returns True if reply was posted, False on failure.
    """
    try:
        import requests

        token = os.environ.get(
            "THREADS_ACCESS_TOKEN",
            os.environ.get("META_ACCESS_TOKEN", ""),
        )
        if not token:
            logger.warning("Threads: no access token set — skipping reply")
            return False

        resp = requests.post(
            f"https://graph.threads.net/v1.0/{thread_id}/replies",
            data={"text": text, "access_token": token},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Threads: posted reply to thread %s", thread_id)
        return True
    except Exception as e:
        logger.warning("Threads: failed to reply to %s: %s", thread_id, e)
        return False
