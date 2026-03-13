"""X/Twitter API v2 engagement client.

Auth: tweepy with X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
from env. Same pattern as Content Scraper's twitter_client.py.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _get_twitter_client():
    """Build tweepy Client using OAuth 1.0a credentials from env."""
    import tweepy

    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_SECRET"],
    )


def post_x_reply(tweet_id: str, text: str) -> bool:
    """Post a reply tweet via X API v2.

    Returns True if reply was posted, False on failure.
    """
    try:
        client = _get_twitter_client()
        client.create_tweet(text=text, in_reply_to_tweet_id=tweet_id)
        logger.info("X/Twitter: posted reply to tweet %s", tweet_id)
        return True
    except Exception as e:
        logger.warning("X/Twitter: failed to post reply to %s: %s", tweet_id, e)
        return False


def like_x_tweet(tweet_id: str) -> None:
    """Like a tweet via X API v2."""
    try:
        client = _get_twitter_client()
        client.like(tweet_id)
        logger.info("X/Twitter: liked tweet %s", tweet_id)
    except Exception as e:
        logger.warning("X/Twitter: failed to like tweet %s: %s", tweet_id, e)
