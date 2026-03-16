"""X/Twitter reply client for the engagement engine.

Uses X API v2 via tweepy to post reply tweets.
OAuth 1.0a credentials required (api_key, api_secret, access_token, access_secret).
"""
from __future__ import annotations

import logging

import tweepy

from genlab_core.publishing.niche_credentials import resolve_twitter_credentials

logger = logging.getLogger(__name__)


class TwitterReplyClient:
    """Post replies to X/Twitter mentions via API v2."""

    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Reply to a tweet.

        Args:
            comment_id: The tweet ID to reply to.
            text: Reply text (max 280 chars).
            niche_id: Niche identifier for credential resolution.

        Returns:
            True if the reply was posted successfully.
        """
        creds = resolve_twitter_credentials(niche_id)
        api_key = creds.get("api_key", "")
        api_secret = creds.get("api_secret", "")
        access_token = creds.get("access_token", "")
        access_secret = creds.get("access_secret", "")

        if not all([api_key, api_secret, access_token, access_secret]):
            logger.warning(
                "[X Reply] Missing OAuth credentials for niche %s", niche_id
            )
            return False

        try:
            client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=access_token,
                access_token_secret=access_secret,
            )
            response = client.create_tweet(
                text=text,
                in_reply_to_tweet_id=comment_id,
            )
            tweet_id = response.data.get("id", "") if response.data else ""
            logger.info(
                "[X Reply] Posted reply %s to %s for niche %s",
                tweet_id, comment_id, niche_id,
            )
            return True

        except tweepy.Forbidden:
            logger.warning(
                "[X Reply] 403 Forbidden for niche %s — may require elevated access",
                niche_id,
            )
            return False
        except Exception:
            logger.exception("[X Reply] Failed to post reply to %s", comment_id)
            return False
