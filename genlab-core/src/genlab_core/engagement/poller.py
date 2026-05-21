"""Polling fallback for comment sources that lack webhook push.

YouTube: poll every 30 minutes (1800s, 1 API unit per poll -- cheap).
X/Twitter: poll every 15 minutes (180 req/15min budget).
Threads: poll every 10 minutes (Threads API rate limit: 200 req/hr).

Moved from genlab_core.platform.engagement_poller in Sprint 24 (2026-03-10).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Polling intervals in seconds
YOUTUBE_POLL_INTERVAL = 1800  # 30 minutes (saves ~2,400 quota units/day)
TWITTER_POLL_INTERVAL = 900  # 15 minutes
THREADS_POLL_INTERVAL = 600  # 10 minutes


async def poll_youtube_comments(niche_id: str, channel_id: str) -> list[dict]:
    """Fetch new comments on recent YouTube videos via Data API v3.

    Note: This function is ``async def`` for compatibility with the
    ``run_engagement_poller.py`` asyncio runner, but internally uses
    synchronous ``requests`` calls. A future refactor should migrate
    to ``httpx.AsyncClient`` for true async I/O.

    Uses per-video polling (videoId) instead of channel-wide polling
    (allThreadsRelatedToChannelId) which requires channel-owner OAuth scope.

    Auth strategy: prefers YOUTUBE_API_KEY (Data API key, works for all
    public read operations) over OAuth. Falls back to OAuth only if no
    API key is available.

    Flow:
      1. Resolve auth (API key or OAuth token)
      2. Get recent video IDs from channel uploads playlist
      3. Poll commentThreads per video using videoId parameter
    """
    import os

    logger.debug("[POLLER] YouTube poll for %s (channel=%s)", niche_id, channel_id)

    api_key = os.environ.get("YOUTUBE_API_KEY", "") or os.environ.get("YOUTUBE_DATA_API_KEY", "")

    if not api_key:
        # Fall back to OAuth if no API key
        client_id = os.environ.get("YOUTUBE_CLIENT_ID", "")
        client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
        if not all([client_id, client_secret, refresh_token]):
            logger.warning("[POLLER] YouTube credentials not set — skipping poll")
            return []

    try:
        import requests as _requests

        # Step 1: Resolve auth — API key param or OAuth bearer header
        auth_params: dict[str, str] = {}
        headers: dict[str, str] = {}
        if api_key:
            auth_params["key"] = api_key
        else:
            token_resp = _requests.post(
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
            headers["Authorization"] = f"Bearer {token_resp.json()['access_token']}"

        # Step 2: Get recent video IDs from the uploads playlist
        # YouTube convention: uploads playlist ID = "UU" + channel_id[2:]
        uploads_playlist = "UU" + channel_id[2:]
        playlist_resp = _requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "playlistId": uploads_playlist,
                "part": "contentDetails",
                "maxResults": 10,
                **auth_params,
            },
            headers=headers or None,
            timeout=15,
        )
        if playlist_resp.status_code == 403:
            error_body = playlist_resp.json().get("error", {}).get("message", "")
            if "quota" in error_body.lower():
                logger.warning(
                    "[POLLER] YouTube quota exceeded for %s — skipping until reset", niche_id
                )
                return []
            logger.warning("[POLLER] YouTube 403 for %s: %s", niche_id, error_body[:100])
            return []
        playlist_resp.raise_for_status()
        video_ids = [
            item["contentDetails"]["videoId"]
            for item in playlist_resp.json().get("items", [])
            if "contentDetails" in item and "videoId" in item["contentDetails"]
        ]

        if not video_ids:
            logger.debug("[POLLER] YouTube: no recent uploads for %s", niche_id)
            return []

        # Step 3: Poll comments per video using videoId
        comments: list[dict] = []
        skipped_self = 0
        for video_id in video_ids:
            try:
                resp = _requests.get(
                    "https://www.googleapis.com/youtube/v3/commentThreads",
                    params={
                        "videoId": video_id,
                        "part": "snippet",
                        "order": "time",
                        "maxResults": 20,
                        **auth_params,
                    },
                    headers=headers or None,
                    timeout=15,
                )
                if resp.status_code == 403:
                    error_msg = resp.json().get("error", {}).get("message", "")
                    if "quota" in error_msg.lower():
                        logger.warning("[POLLER] YouTube quota exceeded — stopping comment poll")
                        break
                    # Comments disabled on this video
                    logger.debug("[POLLER] YouTube: comments disabled on %s", video_id)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.debug("[POLLER] YouTube: comment fetch failed for %s: %s", video_id, e)
                continue

            for item in data.get("items", []):
                snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                author_id = snippet.get("authorChannelId", {}).get("value", "")
                if author_id == channel_id:
                    skipped_self += 1
                    continue
                comments.append(
                    {
                        "platform": "youtube",
                        "post_id": video_id,
                        "comment_id": item.get("id", ""),
                        "author_id": author_id,
                        "author_name": snippet.get("authorDisplayName", ""),
                        "text": snippet.get("textOriginal", ""),
                        "is_question": "?" in snippet.get("textOriginal", ""),
                    }
                )

        if skipped_self:
            logger.debug(
                "[POLLER] YouTube: skipped %d self-comments for %s", skipped_self, niche_id
            )
        logger.info(
            "[POLLER] YouTube: fetched %d comments across %d videos for %s",
            len(comments),
            len(video_ids),
            niche_id,
        )
        return comments

    except Exception as e:
        logger.warning("[POLLER] YouTube poll failed for %s: %s", niche_id, e)
        return []


async def poll_twitter_mentions(niche_id: str, user_id: str) -> list[dict]:
    """Fetch new @mentions via X API v2 GET /users/{id}/mentions.

    Uses existing X credentials from env vars via tweepy.
    Returns list of raw mention dicts ready for InboundComment conversion.
    """
    import os

    logger.debug("[POLLER] X/Twitter poll for %s (user=%s)", niche_id, user_id)

    api_key = os.environ.get("X_API_KEY", "")
    api_secret = os.environ.get("X_API_SECRET", "")
    access_token = os.environ.get("X_ACCESS_TOKEN", "")
    access_secret = os.environ.get("X_ACCESS_SECRET", "")

    if not all([api_key, api_secret, access_token, access_secret]):
        logger.warning("[POLLER] X/Twitter credentials not set — skipping poll")
        return []

    try:
        import tweepy

        client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )

        try:
            resp = client.get_users_mentions(
                id=user_id,
                max_results=50,
                tweet_fields=["author_id", "created_at", "in_reply_to_user_id"],
                expansions=["author_id"],
            )
        except tweepy.Unauthorized:
            # Track consecutive 401s — suppress logging after 5 to avoid log spam
            _twitter_401_key = f"_twitter_401_{niche_id}"
            _count = getattr(poll_twitter_mentions, _twitter_401_key, 0) + 1
            setattr(poll_twitter_mentions, _twitter_401_key, _count)
            if _count <= 5:
                logger.error(
                    "[POLLER] X/Twitter 401 Unauthorized for %s (attempt %d) — "
                    "OAuth tokens may be expired or revoked. Regenerate at "
                    "developer.twitter.com and update X_ACCESS_TOKEN / X_ACCESS_SECRET "
                    "in .env. Suppressing further 401 logs after 5 consecutive failures.",
                    niche_id,
                    _count,
                )
            return []
        except tweepy.Forbidden:
            logger.warning(
                "[POLLER] X/Twitter 403 Forbidden for %s — mentions endpoint may "
                "require elevated access (Basic plan or higher)",
                niche_id,
            )
            return []

        mentions: list[dict] = []
        # Build author lookup from includes
        author_map: dict[str, str] = {}
        if resp.includes and "users" in resp.includes:
            for user in resp.includes["users"]:
                author_map[user.id] = user.username

        skipped_self = 0
        if resp.data:
            for tweet in resp.data:
                if str(tweet.author_id) == user_id:
                    skipped_self += 1
                    continue
                mentions.append(
                    {
                        "platform": "x_twitter",
                        "post_id": str(tweet.id),
                        "comment_id": str(tweet.id),
                        "author_id": str(tweet.author_id),
                        "author_name": author_map.get(tweet.author_id, ""),
                        "text": tweet.text,
                        "is_question": "?" in tweet.text,
                    }
                )

        if skipped_self:
            logger.debug(
                "[POLLER] X/Twitter: skipped %d self-mentions for %s", skipped_self, niche_id
            )
        logger.info("[POLLER] X/Twitter: fetched %d mentions for %s", len(mentions), niche_id)
        return mentions

    except Exception as e:
        logger.warning("[POLLER] X/Twitter poll failed for %s: %s", niche_id, e)
        return []


async def poll_threads_comments(niche_id: str, user_id: str) -> list[dict]:
    """Fetch replies on recent Threads posts via graph.threads.net.

    Uses THREADS_ACCESS_TOKEN (or META_ACCESS_TOKEN fallback) from env vars.
    Returns list of raw comment dicts ready for InboundComment conversion.

    Two-step poll:
      1. GET /{user_id}/threads → recent media IDs
      2. GET /{media_id}/replies → replies per post (paginated)
    """
    logger.debug("[POLLER] Threads poll for %s (user=%s)", niche_id, user_id)

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, resolved_user_id = resolve_threads_credentials(niche_id)
    if not token:
        logger.warning("[POLLER] Threads credentials not set for %s — skipping poll", niche_id)
        return []
    # Use resolved user_id if caller didn't provide one
    if resolved_user_id and not user_id:
        user_id = resolved_user_id

    try:
        import requests as _requests

        base = "https://graph.threads.net/v1.0"

        # Step 0: Resolve our own username for self-reply filtering
        # (user_id is numeric but replies contain usernames)
        own_username = ""
        try:
            me_resp = _requests.get(
                f"{base}/me",
                params={"fields": "username", "access_token": token},
                timeout=10,
            )
            if me_resp.ok:
                own_username = me_resp.json().get("username", "")
        except Exception:
            pass  # fall back to empty — self-replies won't be filtered

        # Step 1: Fetch recent posts
        media_resp = _requests.get(
            f"{base}/{user_id}/threads",
            params={
                "fields": "id",
                "limit": 25,
                "access_token": token,
            },
            timeout=15,
        )
        media_resp.raise_for_status()
        media_ids = [item["id"] for item in media_resp.json().get("data", [])]

        if not media_ids:
            logger.debug("[POLLER] Threads: no recent posts for %s", niche_id)
            return []

        # Step 2: Fetch replies for each post
        comments: list[dict] = []
        skipped_self = 0
        for media_id in media_ids:
            replies_resp = _requests.get(
                f"{base}/{media_id}/replies",
                params={
                    "fields": "id,text,username,timestamp",
                    "access_token": token,
                },
                timeout=15,
            )
            if replies_resp.status_code != 200:
                logger.debug(
                    "[POLLER] Threads: replies fetch failed for %s (HTTP %d)",
                    media_id,
                    replies_resp.status_code,
                )
                continue

            for reply in replies_resp.json().get("data", []):
                reply_username = reply.get("username", "")
                # Filter out own replies (compare username to username)
                if own_username and reply_username == own_username:
                    skipped_self += 1
                    continue
                comments.append(
                    {
                        "platform": "threads",
                        "post_id": media_id,
                        "comment_id": reply.get("id", ""),
                        "author_id": reply_username,
                        "author_name": reply_username,
                        "text": reply.get("text", ""),
                        "is_question": "?" in reply.get("text", ""),
                    }
                )

        if skipped_self:
            logger.debug("[POLLER] Threads: skipped %d self-replies for %s", skipped_self, niche_id)
        logger.info("[POLLER] Threads: fetched %d comments for %s", len(comments), niche_id)
        return comments

    except Exception as e:
        logger.warning("[POLLER] Threads poll failed for %s: %s", niche_id, e)
        return []


async def poll_facebook_comments(niche_id: str, page_id: str) -> list[dict]:
    """Fetch comments on recent Facebook Page posts via graph.facebook.com.

    Uses per-niche FB_PAGE_ACCESS_TOKEN from env vars.
    Returns list of raw comment dicts ready for InboundComment conversion.

    Two-step poll:
      1. GET /{page_id}/posts → recent post IDs
      2. GET /{post_id}/comments → comments per post
    """

    logger.debug("[POLLER] Facebook poll for %s (page=%s)", niche_id, page_id)

    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, resolved_page_id = resolve_fb_credentials(niche_id)
    if not token:
        logger.warning("[POLLER] Facebook credentials not set for %s — skipping", niche_id)
        return []

    try:
        import requests as _requests

        base = "https://graph.facebook.com/v21.0"

        # Step 1: Fetch recent posts
        posts_resp = _requests.get(
            f"{base}/{resolved_page_id or page_id}/posts",
            params={
                "fields": "id",
                "limit": 10,
                "access_token": token,
            },
            timeout=15,
        )
        if posts_resp.status_code == 400:
            logger.warning("[POLLER] Facebook 400 for %s — token may be invalid", niche_id)
            return []
        posts_resp.raise_for_status()
        post_ids = [item["id"] for item in posts_resp.json().get("data", [])]

        if not post_ids:
            logger.debug("[POLLER] Facebook: no recent posts for %s", niche_id)
            return []

        # Step 2: Fetch comments per post
        comments: list[dict] = []
        own_page_id = resolved_page_id or page_id
        skipped_self = 0

        for fb_post_id in post_ids:
            try:
                resp = _requests.get(
                    f"{base}/{fb_post_id}/comments",
                    params={
                        "fields": "id,from,message,created_time",
                        "limit": 50,
                        "access_token": token,
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception as e:
                logger.debug("[POLLER] Facebook comment fetch failed for %s: %s", fb_post_id, e)
                continue

            for comment in data.get("data", []):
                author = comment.get("from", {})
                author_id = author.get("id", "")
                # Skip own replies
                if author_id == own_page_id:
                    skipped_self += 1
                    continue

                comments.append(
                    {
                        "platform": "facebook",
                        "post_id": fb_post_id,
                        "comment_id": comment.get("id", ""),
                        "author_id": author_id,
                        "author_name": author.get("name", ""),
                        "text": comment.get("message", ""),
                        "is_question": "?" in comment.get("message", ""),
                    }
                )

        if skipped_self:
            logger.debug(
                "[POLLER] Facebook: skipped %d self-comments for %s", skipped_self, niche_id
            )
        logger.info("[POLLER] Facebook: fetched %d comments for %s", len(comments), niche_id)
        return comments

    except Exception as e:
        logger.warning("[POLLER] Facebook poll failed for %s: %s", niche_id, e)
        return []
