"""Core engagement logic: receives an event dict from a Dramatiq task,
decides whether to reply/like, generates the reply, and posts it.

Idempotency is non-negotiable. The YouTube poller runs every 5 minutes.
Without a "already replied" check, the same comment receives multiple
replies before the rate limiter catches up. Uses a simple append-only
JSONL file keyed by (comment_id, platform) as the replied-set.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from pathlib import Path

from genlab_core.engagement.persona_schema import NichePersona
from genlab_core.engagement.persona_engine import PersonaEngine
from genlab_core.engagement.toxicity_gate import ToxicityGate
from genlab_core.engagement.spam_filter import is_spam
from genlab_core.engagement.timing import human_delay
from genlab_core.engagement.rate_limiter import EngagementRateLimiter
from genlab_core.utils.env import get_agent_root

logger = logging.getLogger(__name__)

# Per-platform reply rate caps (actions per hour).
RATE_CAPS: dict[str, int] = {
    "instagram": 20,
    "youtube": 10,
    "facebook": 20,
    "x_twitter": 4,    # 50/day / ~12 active hours
    "threads": 3,      # 15/day / ~5 active session hours
}

_rate_limiter = EngagementRateLimiter(RATE_CAPS)


def _get_backlog_client():
    """Lazily load BacklogClient. Returns None if not configured."""
    try:
        from genlab_core.http.backlog_client import BacklogClient
        return BacklogClient()
    except Exception:
        return None


def _get_agent_root() -> Path:
    """Resolve AGENT_ROOT — delegates to shared utility."""
    return get_agent_root()


def _replied_set_path() -> Path:
    """Path to the append-only idempotency log."""
    return _get_agent_root() / ".engagement_replied.jsonl"


def _has_replied(comment_id: str, platform: str) -> bool:
    """Check if we have already replied to this (comment_id, platform) pair.

    Uses a line-scan of the JSONL file. For the current post volume
    (hundreds of comments/day) the file scan is fast enough.
    """
    path = _replied_set_path()
    if not path.exists():
        return False
    needle = json.dumps({"c": comment_id, "p": platform})
    try:
        with open(path) as f:
            return any(line.strip() == needle for line in f)
    except OSError:
        return False


def _mark_replied(comment_id: str, platform: str) -> None:
    """Append an idempotency record. Uses file-level locking for safety."""
    path = _replied_set_path()
    record = json.dumps({"c": comment_id, "p": platform}) + "\n"
    try:
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(record)
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e:
        logger.warning("Engagement: could not write idempotency record: %s", e)


def _load_persona(niche_id: str) -> NichePersona:
    """Load persona YAML. Raises FileNotFoundError if absent."""
    import yaml

    candidate_paths = [
        _get_agent_root() / "config" / "persona.yaml",
        Path(__file__).parent / "personas" / f"{niche_id}.yaml",
    ]
    for path in candidate_paths:
        if path.exists():
            with open(path) as f:
                return NichePersona.model_validate(yaml.safe_load(f))
    raise FileNotFoundError(
        f"No persona.yaml found for niche '{niche_id}'. "
        f"Searched: {[str(p) for p in candidate_paths]}"
    )


def process_reply_event(event: dict) -> None:
    """Full reply pipeline for one comment event:

    1. Idempotency check  — skip if already replied
    2. Spam filter        — fast regex, no model
    3. Inbound toxicity   — Detoxify gate
    4. Rate limit check   — token bucket per platform
    5. Persona reply gen  — LLM + outbound toxicity gate
    6. Timing jitter      — log-normal human delay
    7. Platform API call  — post the reply
    8. Mark replied       — idempotency record
    """
    comment_id = event["comment_id"]
    comment_text = event.get("comment_text", "")
    platform = event["platform"]
    niche_id = event["niche_id"]
    post_id = event["post_id"]
    post_context = event.get("post_context", "")

    # 1. Idempotency
    if _has_replied(comment_id, platform):
        logger.info("Engagement: already replied to %s on %s, skipping", comment_id, platform)
        return

    # Record to SharePoint (optional — fails gracefully)
    bl = _get_backlog_client()
    sp_item_id = None
    if bl:
        sp_item_id = bl.write_pending_engagement({
            "comment_id": comment_id,
            "platform": platform,
            "post_id": post_id,
            "text": comment_text,
            "author_name": event.get("author_name", ""),
            "created_at": event.get("created_at", ""),
            "niche_id": niche_id,
        })

    # 2. Spam filter
    if is_spam(comment_text):
        logger.info("Engagement: skipping spam comment %s", comment_id)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "skipped")
        return

    # 3. Inbound toxicity gate
    toxicity = ToxicityGate()
    result = toxicity.check_inbound(comment_text)
    if result.is_toxic:
        logger.info(
            "Engagement: skipping toxic comment %s (%s=%.2f)",
            comment_id, result.max_dimension, result.max_score,
        )
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "skipped")
        return

    # 4. Rate limit — raise to trigger Dramatiq retry with backoff
    if not _rate_limiter.acquire(platform):
        logger.warning("Engagement: rate limit reached for %s, retrying %s", platform, comment_id)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "rate_limited")
        raise RuntimeError(f"Rate limit exceeded for {platform}")

    # 5. Generate reply
    persona = _load_persona(niche_id)
    engine = PersonaEngine(persona=persona, toxicity_gate=toxicity)
    reply = engine.generate_reply(
        comment=comment_text,
        platform=platform,
        post_context=post_context,
    )
    if reply is None:
        logger.error("Engagement: failed to generate safe reply for %s", comment_id)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "failed", error_msg="Reply generation failed")
        return

    # 6. Human-like timing delay
    delay = human_delay()
    logger.debug("Engagement: sleeping %.1fs before posting reply", delay)
    time.sleep(delay)

    # 7. Post reply
    posted = _post_reply(platform=platform, post_id=post_id, comment_id=comment_id, reply_text=reply)

    # 8. Mark as replied — only if the reply was actually posted
    if posted:
        _mark_replied(comment_id, platform)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "replied", reply_text=reply)
    else:
        logger.warning("Engagement: reply to %s on %s failed — NOT marking as replied", comment_id, platform)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "failed", error_msg="Platform API call failed")


def process_like_event(event: dict) -> None:
    """Like a positive comment. No LLM needed, but still rate-limited and idempotent."""
    comment_id = event["comment_id"]
    platform = event["platform"]
    post_id = event.get("post_id")

    like_key = f"like:{comment_id}"
    if _has_replied(like_key, platform):
        logger.info("Engagement: already liked %s on %s, skipping", comment_id, platform)
        return

    if not _rate_limiter.acquire(platform):
        logger.warning("Engagement: rate limit reached for %s, retrying like %s", platform, comment_id)
        raise RuntimeError(f"Rate limit exceeded for {platform}")

    delay = human_delay()
    time.sleep(delay)

    try:
        _post_like(platform=platform, comment_id=comment_id, post_id=post_id)
        _mark_replied(like_key, platform)
        logger.info("Engagement: liked comment %s on %s", comment_id, platform)
    except Exception as e:
        logger.error("Engagement: failed to like %s on %s: %s", comment_id, platform, e)
        raise


def _post_reply(platform: str, post_id: str, comment_id: str, reply_text: str) -> bool:
    """Route to the correct platform client for posting the reply.

    Returns True if the reply was actually posted, False otherwise.
    Platform clients return bool — True on success, False on failure.

    Uses the unified platform registry (genlab_core.platforms) first. Falls
    back to the legacy per-function engagement.platform_clients path when
    the client does not implement the Engageable protocol.
    """
    from genlab_core.platforms import get_client
    from genlab_core.platforms.protocols import Engageable

    try:
        client = get_client(platform)
    except (ValueError, ImportError) as exc:
        logger.warning(
            "Engagement: could not load native client for '%s' (%s) — "
            "falling back to legacy path", platform, exc,
        )
    else:
        if isinstance(client, Engageable):
            return client.post_reply(
                parent_id=comment_id, text=reply_text, context_id=post_id
            )
        logger.warning(
            "Engagement: native client for '%s' does not implement Engageable — "
            "falling back to legacy path", platform,
        )

    # Legacy per-platform dispatch (fallback when native client is unavailable or not Engageable)
    if platform == "youtube":
        from genlab_core.engagement.platform_clients.youtube import post_youtube_reply
        return post_youtube_reply(video_id=post_id, parent_id=comment_id, text=reply_text)
    elif platform == "instagram":
        from genlab_core.engagement.platform_clients.instagram import post_instagram_reply
        return post_instagram_reply(media_id=post_id, comment_id=comment_id, text=reply_text)
    elif platform == "x_twitter":
        from genlab_core.engagement.platform_clients.x_twitter import post_x_reply
        return post_x_reply(tweet_id=comment_id, text=reply_text)
    elif platform == "facebook":
        from genlab_core.engagement.platform_clients.facebook import post_facebook_reply
        return post_facebook_reply(comment_id=comment_id, text=reply_text)
    elif platform == "threads":
        from genlab_core.engagement.platform_clients.threads import post_threads_reply
        return post_threads_reply(thread_id=comment_id, text=reply_text)
    elif platform == "tiktok":
        logger.info(
            "Engagement: TikTok engagement not available via API — "
            "manual reply required for comment %s", comment_id,
        )
        return False
    else:
        logger.error("Engagement: unknown platform '%s'", platform)
        return False


def _post_like(platform: str, comment_id: str, post_id: str | None = None) -> None:
    """Route to the correct platform client for liking a comment."""
    if platform == "youtube":
        from genlab_core.engagement.platform_clients.youtube import like_youtube_comment
        like_youtube_comment(comment_id=comment_id)
    elif platform == "instagram":
        from genlab_core.engagement.platform_clients.instagram import like_instagram_comment
        like_instagram_comment(comment_id=comment_id)
    elif platform == "x_twitter":
        from genlab_core.engagement.platform_clients.x_twitter import like_x_tweet
        like_x_tweet(tweet_id=comment_id)
    elif platform in ("facebook", "threads"):
        logger.warning(
            "Engagement: like not yet implemented for %s "
            "(comment_id=%s, post_id=%s). Add platform client when ready.",
            platform, comment_id, post_id,
        )
    else:
        logger.warning("Engagement: like not supported for platform '%s'", platform)
