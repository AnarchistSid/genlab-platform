"""Dramatiq tasks for the engagement engine.

Priority tiers require separate actor definitions — Dramatiq does NOT support
overriding queue_name at send time (send_with_options ignores it silently).

HIGH   -> questions, first-hour comments (< 3600s old)
NORMAL -> genuine reactions, compliments
LOW    -> late comments (> 24h), simple emoji reactions

To run workers:
    NICHE_ID=gaming AGENT_ROOT=/path/to/CriticalRush \
        dramatiq genlab_core.engagement.tasks \
            --queues engagement_high engagement_normal engagement_low \
            --processes 2 --threads 4

For tests, set DRAMATIQ_TEST=1 to use StubBroker (no Redis required).
"""
from __future__ import annotations

import logging
import os

import dramatiq

logger = logging.getLogger(__name__)

# ── Broker selection ──────────────────────────────────────────────────────────
if os.environ.get("DRAMATIQ_TEST", "0") == "1":
    from dramatiq.brokers.stub import StubBroker

    _broker = StubBroker()
else:
    from dramatiq.brokers.redis import RedisBroker

    _broker = RedisBroker(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
    )

dramatiq.set_broker(_broker)


def _engagement_retry_when(retries: int, exception: BaseException) -> bool:
    """retry_when callback shared by all engagement actors.

    Rate-limited messages (raised as ``dramatiq.Retry(delay=N)`` by
    comment_processor when the token bucket is empty) bounce on a
    delay matched to the per-platform refill rate; those retries do
    NOT count against the small max_retries cap because they're
    "waiting on infra," not "this message is broken."

    Real exceptions (network, persona generation, etc.) still cap at
    3 retries so a permanently-bad message doesn't churn forever.

    Why this exists: dramatiq's default ``Retries`` middleware counts
    every retry including those produced by ``dramatiq.Retry``. With
    max_retries=3 and Threads' 20-min token refill, a rate-limited
    message DLQ'd after ~1h regardless of whether we actually got
    the token. 2026-05-21 forensics: 888 errors / 0 successes in 2h
    was the visible symptom of that pattern.
    """
    if isinstance(exception, dramatiq.Retry) and exception.delay is not None:
        # 24 × 20-min Threads backoff = 8 hours of patience before
        # we admit the platform is stuck and give up on the message.
        return retries < 24
    # Genuine error path: same shape as the prior default.
    return retries < 3


# ── High priority: questions + first-hour comments ───────────────────────────
@dramatiq.actor(
    queue_name="engagement_high",
    max_retries=3,
    retry_when=_engagement_retry_when,
    time_limit=120_000,
)
def reply_to_comment_high(event: dict) -> None:
    """Reply to a high-priority comment (question or first hour)."""
    from genlab_core.engagement.comment_processor import process_reply_event

    process_reply_event(event)


# ── Normal priority: genuine reactions, compliments ──────────────────────────
@dramatiq.actor(
    queue_name="engagement_normal",
    max_retries=3,
    retry_when=_engagement_retry_when,
    time_limit=120_000,
)
def reply_to_comment_normal(event: dict) -> None:
    """Reply to a normal-priority comment (genuine reaction, compliment)."""
    from genlab_core.engagement.comment_processor import process_reply_event

    process_reply_event(event)


# ── Low priority: late comments, emoji-only reactions ────────────────────────
@dramatiq.actor(
    queue_name="engagement_low",
    max_retries=2,
    retry_when=_engagement_retry_when,
    time_limit=60_000,
)
def reply_to_comment_low(event: dict) -> None:
    """Reply to a low-priority comment (late or minimal engagement value)."""
    from genlab_core.engagement.comment_processor import process_reply_event

    process_reply_event(event)


# ── Like action (no LLM needed) ─────────────────────────────────────────────
@dramatiq.actor(
    queue_name="engagement_normal",
    max_retries=2,
    retry_when=_engagement_retry_when,
    time_limit=30_000,
)
def like_comment(event: dict) -> None:
    """Like a positive comment. Simpler than replying — no LLM required."""
    from genlab_core.engagement.comment_processor import process_like_event

    process_like_event(event)
