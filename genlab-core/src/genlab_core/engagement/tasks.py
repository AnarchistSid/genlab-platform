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


# ── High priority: questions + first-hour comments ───────────────────────────
@dramatiq.actor(queue_name="engagement_high", max_retries=3, time_limit=120_000)
def reply_to_comment_high(event: dict) -> None:
    """Reply to a high-priority comment (question or first hour)."""
    from genlab_core.engagement.comment_processor import process_reply_event

    process_reply_event(event)


# ── Normal priority: genuine reactions, compliments ──────────────────────────
@dramatiq.actor(queue_name="engagement_normal", max_retries=3, time_limit=120_000)
def reply_to_comment_normal(event: dict) -> None:
    """Reply to a normal-priority comment (genuine reaction, compliment)."""
    from genlab_core.engagement.comment_processor import process_reply_event

    process_reply_event(event)


# ── Low priority: late comments, emoji-only reactions ────────────────────────
@dramatiq.actor(queue_name="engagement_low", max_retries=2, time_limit=60_000)
def reply_to_comment_low(event: dict) -> None:
    """Reply to a low-priority comment (late or minimal engagement value)."""
    from genlab_core.engagement.comment_processor import process_reply_event

    process_reply_event(event)


# ── Like action (no LLM needed) ─────────────────────────────────────────────
@dramatiq.actor(queue_name="engagement_normal", max_retries=2, time_limit=30_000)
def like_comment(event: dict) -> None:
    """Like a positive comment. Simpler than replying — no LLM required."""
    from genlab_core.engagement.comment_processor import process_like_event

    process_like_event(event)
