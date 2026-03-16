"""Core engagement logic: receives an event dict from a Dramatiq task,
decides whether to reply/like, generates the reply, and posts it.

Idempotency is non-negotiable. The YouTube poller runs every 5 minutes.
Without a "already replied" check, the same comment receives multiple
replies before the rate limiter catches up. Uses a simple append-only
JSONL file keyed by (comment_id, platform) as the replied-set.

Confidence routing (hybrid auto-reply):
  auto    — confidence >= 0.85 AND toxicity < 0.15 AND safe pattern AND <100 chars
  review  — confidence >= 0.5 AND toxicity < 0.3
  discard — low confidence OR high toxicity
"""
from __future__ import annotations

import fcntl
import json
import logging
import re
import time
from pathlib import Path
from typing import Literal

from genlab_core.engagement.persona_schema import NichePersona
from genlab_core.engagement.persona_engine import PersonaEngine
from genlab_core.engagement.toxicity_gate import ToxicityGate
from genlab_core.engagement.spam_filter import is_spam
from genlab_core.engagement.timing import human_delay
from genlab_core.engagement.rate_limiter import EngagementRateLimiter
from genlab_core.utils.env import get_agent_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence routing — hybrid auto-reply
# ---------------------------------------------------------------------------

# Safe patterns for auto-reply. If the generated reply matches any of these
# AND confidence/toxicity thresholds are met, the reply is posted immediately.
SAFE_PATTERNS = [
    re.compile(r"^(thanks|thank you|glad you (liked|enjoyed)|appreciate)", re.I),
    re.compile(r"^(right\??|ikr|fr|exactly|so true|facts)", re.I),
    re.compile(r"^(check out|watch|subscribe|follow)", re.I),
    re.compile(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s!]+$"),  # emoji-only
]


def _is_safe_reply(text: str) -> bool:
    """Return True if the reply text matches a known-safe pattern.

    Safe replies are short (< 100 chars) acknowledgments, agreements,
    CTAs, or emoji-only messages that carry minimal risk of controversy.
    """
    if len(text) > 100:
        return False
    return any(p.search(text.strip()) for p in SAFE_PATTERNS)


def classify_reply_action(
    reply_text: str,
    confidence: float,
    toxicity: float,
) -> Literal["auto", "review", "discard"]:
    """Classify a generated reply into an action tier.

    Tiers:
      auto    — post immediately (high confidence, low toxicity, safe pattern)
      review  — queue for human approval
      discard — log and drop

    Args:
        reply_text: The generated reply text.
        confidence: Persona engine confidence score (0.0-1.0).
        toxicity: Outbound toxicity score (0.0-1.0).

    Returns:
        One of "auto", "review", or "discard".
    """
    if toxicity >= 0.3:
        return "discard"
    if confidence < 0.5:
        return "discard"
    if confidence >= 0.85 and toxicity < 0.15 and _is_safe_reply(reply_text):
        return "auto"
    return "review"


# Per-platform reply rate caps (actions per hour).
RATE_CAPS: dict[str, int] = {
    "instagram": 20,
    "youtube": 10,
    "facebook": 20,
    "x_twitter": 4,    # 50/day / ~12 active hours
    "threads": 3,      # 15/day / ~5 active session hours
}

_rate_limiter = EngagementRateLimiter(RATE_CAPS)


_backlog_client_singleton = None


def _get_backlog_client():
    """Lazily load BacklogClient singleton. Returns None if not configured."""
    global _backlog_client_singleton
    if _backlog_client_singleton is None:
        try:
            from genlab_core.http.backlog_client import BacklogClient
            _backlog_client_singleton = BacklogClient()
        except Exception:
            return None
    return _backlog_client_singleton


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

    # Normalise niche_id aliases
    niche_aliases = [niche_id]
    if niche_id == "ai_creators":
        niche_aliases.append("ai_news")
    elif niche_id == "ai_news":
        niche_aliases.append("ai_creators")

    personas_dir = Path(__file__).parent / "personas"
    candidate_paths = []

    # Try agent root first (niche-specific persona.yaml in channel config/)
    try:
        agent_root = _get_agent_root()
        candidate_paths.append(agent_root / "config" / "persona.yaml")
    except Exception:
        pass

    # Then try package personas/ directory with alias resolution
    for alias in niche_aliases:
        candidate_paths.append(personas_dir / f"{alias}.yaml")

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

    # Sanitize external comment text against prompt injection
    from genlab_core.cache.text_sanitizer import check_for_injection
    if check_for_injection(comment_text):
        logger.warning(
            "Engagement: injection pattern in comment %s — skipping LLM reply",
            comment_id,
        )
        return

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
    Uses the unified platform registry (genlab_core.platforms).
    """
    from genlab_core.platforms import get_client
    from genlab_core.platforms.protocols import Engageable

    try:
        client = get_client(platform)
    except (ValueError, ImportError) as exc:
        if platform == "tiktok":
            logger.info(
                "Engagement: TikTok engagement not available via API — "
                "manual reply required for comment %s", comment_id,
            )
            return False
        logger.error(
            "Engagement: could not load client for '%s': %s", platform, exc,
        )
        return False

    if isinstance(client, Engageable):
        return client.post_reply(
            parent_id=comment_id, text=reply_text, context_id=post_id
        )

    logger.error(
        "Engagement: client for '%s' does not implement Engageable — cannot reply",
        platform,
    )
    return False


def _post_like(platform: str, comment_id: str, post_id: str | None = None) -> None:
    """Route to the correct platform client for liking a comment."""
    from genlab_core.platforms import get_client
    from genlab_core.platforms.protocols import Engageable

    try:
        client = get_client(platform)
    except (ValueError, ImportError) as exc:
        logger.warning(
            "Engagement: could not load client for '%s' to like comment %s: %s",
            platform, comment_id, exc,
        )
        return

    if isinstance(client, Engageable):
        client.like(target_id=comment_id, context_id=post_id or "")
    else:
        logger.warning(
            "Engagement: client for '%s' does not implement Engageable — "
            "cannot like comment %s", platform, comment_id,
        )
