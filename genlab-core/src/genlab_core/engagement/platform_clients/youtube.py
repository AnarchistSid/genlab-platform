"""YouTube Data API v3 engagement client.

Uses raw HTTP requests with OAuth2 token refresh (same pattern as
engagement poller). No google-auth/googleapiclient dependency required.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://www.googleapis.com/youtube/v3"


def _get_access_token() -> str:
    """Refresh the OAuth2 access token using env credentials."""
    resp = requests.post(
        _TOKEN_URL,
        data={
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def post_youtube_reply(video_id: str, parent_id: str, text: str) -> bool:
    """Post a reply to a YouTube comment via Data API v3.

    Returns True if reply was posted, False on failure.
    """
    try:
        token = _get_access_token()
        resp = requests.post(
            f"{_API_BASE}/comments",
            params={"part": "snippet"},
            json={
                "snippet": {
                    "parentId": parent_id,
                    "textOriginal": text,
                }
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("YouTube: posted reply to comment %s on video %s", parent_id, video_id)
        return True
    except Exception as e:
        logger.warning("YouTube: failed to post reply to %s: %s", parent_id, e)
        return False


def like_youtube_comment(comment_id: str) -> None:
    """Like a YouTube comment via the Data API v3."""
    try:
        token = _get_access_token()
        resp = requests.post(
            f"{_API_BASE}/comments/setModerationStatus",
            params={
                "id": comment_id,
                "moderationStatus": "published",
                "banAuthor": False,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("YouTube: liked comment %s", comment_id)
    except Exception as e:
        logger.warning("YouTube: failed to like comment %s: %s", comment_id, e)
