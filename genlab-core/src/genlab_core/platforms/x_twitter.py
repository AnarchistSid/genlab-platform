"""X/Twitter API v2 platform client.

Auth: OAuth 1.0a via tweepy (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET).

Implements Publisher + Engageable + Trackable + HealthCheckable protocols.

tweepy is an optional dependency.  When it is not installed the module still
imports cleanly; any method that needs tweepy returns a graceful failure.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional tweepy import
# ---------------------------------------------------------------------------

try:
    import tweepy as _tweepy  # noqa: F401

    _TWEEPY_AVAILABLE = True
except ImportError:
    _tweepy = None  # type: ignore[assignment]
    _TWEEPY_AVAILABLE = False

_RATE_LIMIT_COOLDOWN = 3600  # seconds — 1 hour matches free-tier cadence
_VIDEO_EXTS = {".mp4", ".mov", ".gif"}

# Module-level rate-limit state — shared across instances so cooldown
# persists when publisher creates new XTwitterClient per niche (P10 fix)
_module_rate_limited: bool = False
_module_rate_limited_at: float = 0.0


class XTwitterClient:
    """X/Twitter API v2 client.

    Args:
        api_key:       X API key. Defaults to ``X_API_KEY`` env var.
        api_secret:    X API secret. Defaults to ``X_API_SECRET`` env var.
        access_token:  Access token. Defaults to ``X_ACCESS_TOKEN`` env var.
        access_secret: Access token secret. Defaults to ``X_ACCESS_SECRET`` env var.
    """

    platform_id = "x_twitter"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        access_secret: str | None = None,
    ) -> None:
        self._api_key: str = api_key or os.environ.get("X_API_KEY", "")
        self._api_secret: str = api_secret or os.environ.get("X_API_SECRET", "")
        self._access_token: str = access_token or os.environ.get("X_ACCESS_TOKEN", "")
        self._access_secret: str = access_secret or os.environ.get("X_ACCESS_SECRET", "")

        # Rate-limit state uses module-level globals so cooldown persists
        # across client instances (publisher creates new client per niche)

    # ------------------------------------------------------------------
    # Internal factory helpers — one fresh client per call (not thread-safe
    # to share a single tweepy.Client across threads).
    # ------------------------------------------------------------------

    def _get_client(self):
        """Return a fresh tweepy.Client (v2, OAuth 1.0a).

        Raises RuntimeError if tweepy is not installed.
        """
        if not _TWEEPY_AVAILABLE:
            raise RuntimeError("tweepy is not installed. Install it with: pip install tweepy")
        import tweepy  # local import keeps things tidy

        return tweepy.Client(
            consumer_key=self._api_key,
            consumer_secret=self._api_secret,
            access_token=self._access_token,
            access_token_secret=self._access_secret,
        )

    def _get_api_v1(self):
        """Return a tweepy.API instance (v1.1, for media_upload).

        Raises RuntimeError if tweepy is not installed.
        """
        if not _TWEEPY_AVAILABLE:
            raise RuntimeError("tweepy is not installed. Install it with: pip install tweepy")
        import tweepy

        auth = tweepy.OAuth1UserHandler(
            self._api_key,
            self._api_secret,
            self._access_token,
            self._access_secret,
        )
        return tweepy.API(auth)

    # ------------------------------------------------------------------
    # Exception classification helpers (needed to distinguish 403 vs 401 vs
    # 429 even when tweepy may be mocked in tests)
    # ------------------------------------------------------------------

    def _is_rate_limit_error(self, exc: BaseException) -> bool:
        """Return True if *exc* is a tweepy TooManyRequests (429)."""
        if _TWEEPY_AVAILABLE:
            import tweepy

            cls = getattr(tweepy, "TooManyRequests", None)
            if cls is not None and isinstance(cls, type) and isinstance(exc, cls):
                return True
        return False

    def _is_forbidden_error(self, exc: BaseException) -> bool:
        """Return True if *exc* is a tweepy Forbidden (403)."""
        if _TWEEPY_AVAILABLE:
            import tweepy

            cls = getattr(tweepy, "Forbidden", None)
            if cls is not None and isinstance(cls, type) and isinstance(exc, cls):
                return True
        return False

    def _is_unauthorized_error(self, exc: BaseException) -> bool:
        """Return True if *exc* is a tweepy Unauthorized (401)."""
        if _TWEEPY_AVAILABLE:
            import tweepy

            cls = getattr(tweepy, "Unauthorized", None)
            if cls is not None and isinstance(cls, type) and isinstance(exc, cls):
                return True
        return False

    # ------------------------------------------------------------------
    # Rate-limit guard (shared by publish + reply)
    # ------------------------------------------------------------------

    def _is_currently_rate_limited(self) -> bool:
        """Return True if we are in an active rate-limit cooldown window."""
        global _module_rate_limited, _module_rate_limited_at
        if not _module_rate_limited:
            return False
        elapsed = time.monotonic() - _module_rate_limited_at
        if elapsed > _RATE_LIMIT_COOLDOWN:
            logger.info(
                "X/Twitter: rate-limit cooldown expired (%.0fs elapsed) — resuming",
                elapsed,
            )
            _module_rate_limited = False
            return False
        remaining = int(_RATE_LIMIT_COOLDOWN - elapsed)
        logger.warning(
            "X/Twitter: rate-limited — %ds remaining in cooldown, skipping",
            remaining,
        )
        return True

    def _handle_rate_limit(self, exc: BaseException) -> None:
        """Record rate-limit state after a 429."""
        global _module_rate_limited, _module_rate_limited_at
        logger.warning(
            "X/Twitter: 429 Too Many Requests — entering %ds cooldown: %s",
            _RATE_LIMIT_COOLDOWN,
            exc,
        )
        _module_rate_limited = True
        _module_rate_limited_at = time.monotonic()

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload) -> Any:
        """Publish a tweet or thread.

        - If ``platform_specific.routing == "thread"``, posts each entry in
          ``thread_tweets`` as a reply chain; returns first tweet id.
        - Otherwise posts a single tweet, optionally with media.

        Returns a :class:`~genlab_core.platforms.models.PublishResult`.
        """
        from genlab_core.platforms.models import PublishResult, TwitterSpecific

        _fail = lambda msg: PublishResult(  # noqa: E731
            platform=self.platform_id, success=False, error=msg
        )

        if self._is_currently_rate_limited():
            return _fail("Rate limited — in cooldown, try again later")

        tw_specific = payload.platform_specific
        routing = "single"
        tweet_text = payload.caption
        thread_tweets: list[dict] = []

        if isinstance(tw_specific, TwitterSpecific):
            routing = tw_specific.routing
            if tw_specific.tweet_text:
                tweet_text = tw_specific.tweet_text
            thread_tweets = tw_specific.thread_tweets or []

        if routing == "thread":
            return self._publish_thread(payload, thread_tweets)

        # Single tweet
        if not tweet_text:
            return _fail("tweet_text is empty — nothing to publish")

        try:
            media_ids = self._upload_media_paths(payload.media_paths)
            tweet_id = self._post_single_tweet(
                text=tweet_text,
                media_ids=media_ids if media_ids else None,
            )
        except Exception as exc:
            logger.error("X/Twitter: publish error: %s", exc)
            return _fail(str(exc))

        if tweet_id is None:
            return _fail("create_tweet returned no id")

        # First-reply for affiliate link: X downranks tweets with external
        # URLs in the main body, so the standard creator pattern is to
        # post the link as a self-reply. Non-fatal if it fails.
        if payload.first_comment_text:
            try:
                self._post_single_tweet(
                    text=payload.first_comment_text,
                    reply_to=tweet_id,
                )
                logger.info(
                    "X/Twitter: posted affiliate first-reply under %s",
                    tweet_id,
                )
            except Exception as exc:
                logger.warning(
                    "X/Twitter: first-reply exception (non-fatal): %s",
                    exc,
                )

        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=tweet_id,
            post_url=f"https://twitter.com/i/web/status/{tweet_id}",
        )

    def _upload_media_paths(self, media_paths: list[Path]) -> list[str]:
        """Upload up to 4 media files; return list of media_id strings."""
        if not media_paths:
            return []

        api_v1 = self._get_api_v1()
        media_ids: list[str] = []

        for path in media_paths[:4]:
            path = Path(path)
            if not path.exists():
                logger.warning("X/Twitter: media file not found: %s", path)
                continue
            try:
                use_chunked = path.suffix.lower() in _VIDEO_EXTS
                media = api_v1.media_upload(str(path), chunked=use_chunked)
                media_ids.append(media.media_id_string)
                logger.debug(
                    "X/Twitter: uploaded media %s → %s (chunked=%s)",
                    path.name,
                    media.media_id_string,
                    use_chunked,
                )
            except Exception as exc:
                logger.error("X/Twitter: media upload failed for %s: %s", path.name, exc)

        return media_ids

    def _post_single_tweet(
        self,
        text: str,
        *,
        media_ids: list[str] | None = None,
        reply_to: str | None = None,
    ) -> str | None:
        """Call create_tweet; handle 429 by setting rate-limit flag."""
        client = self._get_client()
        kwargs: dict[str, Any] = {"text": text}
        if media_ids:
            kwargs["media_ids"] = media_ids[:4]
        if reply_to:
            kwargs["in_reply_to_tweet_id"] = reply_to

        try:
            response = client.create_tweet(**kwargs)
            return str(response.data["id"])
        except Exception as exc:
            if self._is_rate_limit_error(exc):
                self._handle_rate_limit(exc)
                return None
            raise

    def _publish_thread(self, payload, thread_tweets: list[dict]):
        """Post a thread (chain of reply tweets).

        Returns a PublishResult with post_id = first tweet id.
        """
        from genlab_core.platforms.models import PublishResult

        _fail = lambda msg: PublishResult(  # noqa: E731
            platform=self.platform_id, success=False, error=msg
        )

        if not thread_tweets:
            return _fail("routing='thread' but thread_tweets is empty")

        posted_ids: list[str] = []
        reply_to: str | None = None

        for i, tweet_data in enumerate(thread_tweets):
            text = tweet_data.get("text", "")
            if not text:
                logger.warning("X/Twitter: thread tweet %d has no text — skipping", i)
                continue

            # Images can be attached by index into payload.media_paths
            media_ids: list[str] = []
            for idx in tweet_data.get("image_indices", [])[:4]:
                if 0 <= idx < len(payload.media_paths):
                    ids = self._upload_media_paths([payload.media_paths[idx]])
                    media_ids.extend(ids)

            try:
                tweet_id = self._post_single_tweet(
                    text=text,
                    media_ids=media_ids if media_ids else None,
                    reply_to=reply_to,
                )
            except Exception as exc:
                logger.error("X/Twitter: thread broken at position %d: %s", i, exc)
                if posted_ids:
                    # Partial thread published — treat as failure
                    return _fail(
                        f"Thread partially published ({len(posted_ids)}/{len(thread_tweets)} "
                        f"tweets). Not retrying to avoid duplicates."
                    )
                return _fail(f"Thread publish error: {exc}")

            if tweet_id is None:
                # Rate limited or API error during thread
                if posted_ids:
                    return _fail(
                        f"Thread partially published ({len(posted_ids)}/{len(thread_tweets)} "
                        f"tweets). Not retrying to avoid duplicates."
                    )
                return _fail("Thread: first tweet returned no id")

            posted_ids.append(tweet_id)
            reply_to = tweet_id

        if not posted_ids:
            return _fail("Thread: no tweets were posted")

        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=posted_ids[0],
            post_url=f"https://twitter.com/i/web/status/{posted_ids[0]}",
            raw_response={"thread_tweet_ids": posted_ids},
        )

    # ------------------------------------------------------------------
    # Engageable protocol
    # ------------------------------------------------------------------

    def post_reply(self, parent_id: str, text: str, *, context_id: str = "") -> bool:
        """Post a reply to a tweet.

        Args:
            parent_id: The tweet ID to reply to.
            text: Reply text.
            context_id: Optional context (used for logging only).

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        if self._is_currently_rate_limited():
            logger.warning("X/Twitter: post_reply skipped — rate limited (context=%s)", context_id)
            return False

        try:
            client = self._get_client()
            client.create_tweet(text=text, in_reply_to_tweet_id=parent_id)
            logger.info("X/Twitter: replied to tweet %s (context=%s)", parent_id, context_id)
            return True
        except Exception as exc:
            if self._is_rate_limit_error(exc):
                self._handle_rate_limit(exc)
            else:
                logger.warning("X/Twitter: post_reply failed for tweet %s: %s", parent_id, exc)
            return False

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """Like a tweet.

        Args:
            target_id: The tweet ID to like.
            context_id: Optional context (used for logging only).

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        try:
            client = self._get_client()
            client.like(tweet_id=target_id)
            logger.info("X/Twitter: liked tweet %s (context=%s)", target_id, context_id)
            return True
        except Exception as exc:
            logger.warning("X/Twitter: like() failed for tweet %s: %s", target_id, exc)
            return False

    # ------------------------------------------------------------------
    # Trackable protocol
    # ------------------------------------------------------------------

    def get_metrics(self, tweet_id: str, published_at: datetime) -> Any | None:
        """Fetch public metrics for a tweet via tweet lookup.

        Args:
            tweet_id: The tweet to look up.
            published_at: When the tweet was published (not used in lookup,
                          kept for protocol compatibility).

        Returns:
            :class:`~genlab_core.platforms.models.PlatformMetrics` on success,
            ``None`` on any failure.
        """
        from genlab_core.platforms.models import PlatformMetrics

        try:
            client = self._get_client()
            response = client.get_tweet(
                tweet_id,
                tweet_fields=["public_metrics"],
            )
            if response.data is None:
                logger.warning("X/Twitter: get_metrics — no data returned for tweet %s", tweet_id)
                return None

            pm = response.data.public_metrics or {}
            impressions = pm.get("impression_count", 0)
            likes = pm.get("like_count", 0)
            replies = pm.get("reply_count", 0)
            retweets = pm.get("retweet_count", 0)

            return PlatformMetrics(
                views=impressions,
                likes=likes,
                comments=replies,
                shares=retweets,
                extra={
                    "impressions": impressions,
                    "retweets": retweets,
                },
            )
        except Exception as exc:
            logger.warning("X/Twitter: get_metrics failed for tweet %s: %s", tweet_id, exc)
            return None

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    def check_token_health(self) -> Any:
        """Verify Twitter credentials via GET /users/me.

        - HTTP 200 → valid.
        - HTTP 403 Forbidden → valid for free tier (free plan blocks /users/me
          but OAuth credentials are still correct).
        - HTTP 401 Unauthorized → invalid / expired token.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus`.
        """
        from genlab_core.platforms.models import TokenStatus

        _status = lambda valid, msg, **kw: TokenStatus(  # noqa: E731
            valid=valid,
            platform=self.platform_id,
            expires_at=None,
            needs_refresh=False,
            message=msg,
            **kw,
        )

        try:
            client = self._get_client()
            me = client.get_me()
            username = getattr(getattr(me, "data", None), "username", "unknown")
            return _status(
                True,
                f"Token valid — authenticated as @{username}",
            )
        except Exception as exc:
            # 403 Forbidden = free-tier plan; credentials are valid
            if self._is_forbidden_error(exc):
                return _status(
                    True,
                    "Token valid (403 — free tier plan restricts /users/me access)",
                )
            # 401 Unauthorized = bad credentials
            if self._is_unauthorized_error(exc):
                return _status(
                    False,
                    f"Token invalid — 401 Unauthorized: {exc}",
                )
            # Any other error type — classify by message text as a best-effort
            exc_str = str(exc).lower()
            if "403" in exc_str or "forbidden" in exc_str:
                return _status(
                    True,
                    "Token valid (403 — free tier plan restricts /users/me access)",
                )
            if "401" in exc_str or "unauthorized" in exc_str:
                return _status(
                    False,
                    f"Token invalid — 401 Unauthorized: {exc}",
                )
            return _status(
                False,
                f"Token check failed: {exc}",
            )
