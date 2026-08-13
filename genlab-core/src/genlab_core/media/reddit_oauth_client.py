"""Reddit OAuth client stub (Task #113, 2026-08-13).

Reason for existing: today's `fetch_reddit_clips` uses public RSS which
succeeds only ~20% of the time from datacenter IPs (per-IP rate limits
even with Mozilla UA). Reddit OAuth via PRAW unlocks 600 requests/min
with proper app credentials — enough for the full ~28-subreddit sweep
without 429s.

This module is a STUB. It reads env vars and returns a PRAW client
if all credentials are present, None otherwise. The consumer
(fetch_reddit_clips migration) is deferred — this stub exists so:

  1. Operator has a clear one-place-to-look for what env vars they
     need to populate.
  2. Feature flag `GENLAB_REDDIT_OAUTH_ENABLED` centralizes the
     rollout switch.
  3. Future PRAW migration doesn't need to invent the env-var naming
     convention.

## Operator setup (one-time)

  1. Visit https://www.reddit.com/prefs/apps
  2. Click "create app" → choose type "script"
     - name: GenLab (or anything)
     - redirect URI: http://localhost (unused for script apps)
  3. Reddit issues a client_id (14-char) + client_secret (27-char)
  4. Add to /opt/genlab/.env:
       REDDIT_CLIENT_ID=<14-char>
       REDDIT_CLIENT_SECRET=<27-char>
       REDDIT_USER_AGENT="GenLab/1.0 by /u/<your_reddit_username>"
       GENLAB_REDDIT_OAUTH_ENABLED=1
  5. Restart genlab-pipeline-* timers (or wait for next fire).

Without step 4-5, this module returns None on every call → existing
RSS-based fetcher continues to be the sole path.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def is_oauth_enabled() -> bool:
    """True when both the enable flag AND all 3 required env vars
    are present. False otherwise (RSS fetcher path stays authoritative)."""
    if os.environ.get("GENLAB_REDDIT_OAUTH_ENABLED", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        return False
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        if not os.environ.get(var, "").strip():
            logger.warning(
                "[RedditOAuth] GENLAB_REDDIT_OAUTH_ENABLED=1 but %s is "
                "empty — falling back to RSS path. See "
                "reddit_oauth_client.py docstring for setup steps.",
                var,
            )
            return False
    return True


def get_reddit_client() -> Any | None:
    """Return a `praw.Reddit` instance if OAuth is fully configured,
    None otherwise. Late-imports praw so the module loads cheaply
    (and doesn't require praw installed until operator activates)."""
    if not is_oauth_enabled():
        return None
    try:
        import praw  # type: ignore
    except ImportError:
        logger.warning(
            "[RedditOAuth] praw not installed but GENLAB_REDDIT_OAUTH_ENABLED=1. "
            "Add `praw>=7.7` to genlab-core's dependencies."
        )
        return None
    try:
        client = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
            check_for_updates=False,
        )
        # Read-only mode: no auth flow, no login state persistence
        client.read_only = True
        return client
    except Exception as exc:
        logger.warning("[RedditOAuth] PRAW client init failed: %s", exc)
        return None


def fetch_subreddit_top(subreddit_name: str, time_filter: str = "day", limit: int = 15) -> list[dict[str, Any]]:
    """Fetch top N posts from a subreddit via PRAW. Returns empty list
    on any failure or when OAuth isn't configured.

    Placeholder for the future consumer wire. Once fetch_reddit_clips
    is migrated, it will call this instead of hitting RSS. Returns
    dicts shaped like RSS entries so migration is drop-in."""
    client = get_reddit_client()
    if client is None:
        return []
    try:
        subreddit = client.subreddit(subreddit_name)
        posts = []
        for submission in subreddit.top(time_filter=time_filter, limit=limit):
            posts.append({
                "title": submission.title,
                "url": submission.url,
                "permalink": f"https://www.reddit.com{submission.permalink}",
                "created_utc": submission.created_utc,
                "score": submission.score,
                "is_video": bool(getattr(submission, "is_video", False)),
                "media_url": (
                    submission.media.get("reddit_video", {}).get("fallback_url", "")
                    if submission.media else ""
                ),
                "subreddit": subreddit_name,
            })
        return posts
    except Exception as exc:
        logger.warning(
            "[RedditOAuth] fetch failed for r/%s: %s (600 rpm quota shared "
            "across process — check if another client is spamming)",
            subreddit_name, exc,
        )
        return []
