"""Engagement-specific rate limiter wrapping core TokenBucket."""

from __future__ import annotations

import logging

from genlab_core.ratelimit.token_bucket import TokenBucket

logger = logging.getLogger(__name__)


class EngagementRateLimiter:
    """Per-platform rate limiting for engagement actions.

    Uses one TokenBucket per platform. The rate caps dict maps platform
    names to max actions per hour.
    """

    def __init__(self, rate_caps: dict[str, int]) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        for platform, actions_per_hour in rate_caps.items():
            rate = actions_per_hour / 3600.0  # tokens per second
            # R-77: start EMPTY. A full bucket let a freshly-started worker fire
            # an entire hour's quota of replies in one burst (spam → platform
            # flags). With initial_tokens=0 the worker earns its first token
            # through elapsed time; the engagement pipeline already jitters
            # replies, so first-reply latency is not critical.
            self._buckets[platform] = TokenBucket(
                rate=rate, burst=float(actions_per_hour), initial_tokens=0.0
            )

    def acquire(self, platform: str) -> bool:
        """Try to acquire a rate limit token. Returns False if rate-limited."""
        bucket = self._buckets.get(platform)
        if bucket is None:
            # R-77: fail CLOSED. An unknown platform has no configured cap;
            # allowing it would be an unbounded, unthrottled reply channel.
            logger.warning(
                "EngagementRateLimiter: no bucket for platform %s — denying (fail-closed)",
                platform,
            )
            return False
        return bucket.try_acquire()
