#!/usr/bin/env python3
"""Engagement comment poller — polls YouTube and X/Twitter for new comments.

Runs as a long-lived daemon via launchd. Polls YouTube every 30 minutes
and X/Twitter every 15 minutes.

Supports both single-niche and multi-niche modes:
    --niche gaming --platform youtube --channel-id UC_xxx   (single)
    --niche all --platform youtube                          (all niches from config)
    --niche all --platform all                              (all niches × all platforms)

When ENGAGEMENT_DISPATCH=true, new comments are dispatched to the Dramatiq
engagement queue for automated reply generation.

When ENGAGEMENT_DISPATCH=false (observe mode), comments are polled and logged
but NOT dispatched.

Environment:
    ENGAGEMENT_DISPATCH=true|false  Controls reply dispatch (default: false = observe mode)
    REDIS_HOST=localhost            Dramatiq broker host (default: localhost)
    REDIS_PORT=6379                 Dramatiq broker port (default: 6379)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

try:
    from genlab_core.observability.logging import configure_logging
    _is_json = os.environ.get("GENLAB_LOG_JSON", "").lower() == "true"
    configure_logging(json_output=_is_json, level=logging.INFO)
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
logger = logging.getLogger("engagement.poller")


def _is_dispatch_enabled() -> bool:
    """Check if reply dispatch is enabled via ENGAGEMENT_DISPATCH env var."""
    return os.environ.get("ENGAGEMENT_DISPATCH", "").lower() == "true"


def _classify_priority(comment: dict) -> str:
    """Determine which Dramatiq queue to use based on comment properties."""
    is_question = comment.get("is_question", False) or "?" in comment.get("text", "")
    if is_question:
        return "high"
    return "normal"


def _dispatch_to_dramatiq(comments: list[dict], niche_id: str) -> int:
    """Send comments to the appropriate Dramatiq queue. Returns count dispatched.

    If ENGAGEMENT_DISPATCH is not 'true', logs comments but does not dispatch.
    """
    if not _is_dispatch_enabled():
        for raw in comments:
            logger.info(
                "[OBSERVE] %s|%s @%s: %s",
                raw["platform"],
                niche_id,
                raw.get("author_name", "?"),
                raw.get("text", "")[:80],
            )
        logger.info("[OBSERVE] %d comments captured (dispatch OFF — observe mode)", len(comments))
        return 0

    from genlab_core.engagement.tasks import (
        like_comment,
        reply_to_comment_high,
        reply_to_comment_normal,
    )

    dispatched = 0
    for raw in comments:
        event = {
            "comment_id": raw["comment_id"],
            "comment_text": raw.get("text", ""),
            "platform": raw["platform"],
            "niche_id": niche_id,
            "post_id": raw.get("post_id", ""),
            "post_context": "",
        }

        # Always like every comment (high priority — fast, no LLM needed)
        like_comment.send(event)

        # Then queue a reply
        priority = _classify_priority(raw)
        if priority == "high":
            reply_to_comment_high.send(event)
        else:
            reply_to_comment_normal.send(event)
        dispatched += 1

    return dispatched


async def _poll_loop_youtube(niche_id: str, channel_id: str) -> None:
    """Poll YouTube comments in a loop."""
    from genlab_core.engagement.poller import YOUTUBE_POLL_INTERVAL, poll_youtube_comments

    logger.info("Starting YouTube poller for niche=%s channel=%s interval=%ds",
                niche_id, channel_id, YOUTUBE_POLL_INTERVAL)

    while True:
        try:
            comments = await poll_youtube_comments(niche_id, channel_id)
            if comments:
                count = _dispatch_to_dramatiq(comments, niche_id)
                logger.info("YouTube: dispatched %d comments to Dramatiq", count)
        except Exception as e:
            logger.error("YouTube poll error: %s", e, exc_info=True)

        await asyncio.sleep(YOUTUBE_POLL_INTERVAL)


async def _poll_loop_twitter(niche_id: str, user_id: str) -> None:
    """Poll X/Twitter mentions in a loop."""
    from genlab_core.engagement.poller import TWITTER_POLL_INTERVAL, poll_twitter_mentions

    logger.info("Starting X/Twitter poller for niche=%s user=%s interval=%ds",
                niche_id, user_id, TWITTER_POLL_INTERVAL)

    while True:
        try:
            mentions = await poll_twitter_mentions(niche_id, user_id)
            if mentions:
                count = _dispatch_to_dramatiq(mentions, niche_id)
                logger.info("X/Twitter: dispatched %d mentions to Dramatiq", count)
        except Exception as e:
            logger.error("X/Twitter poll error: %s", e, exc_info=True)

        await asyncio.sleep(TWITTER_POLL_INTERVAL)


async def _poll_loop_threads(niche_id: str, user_id: str) -> None:
    """Poll Threads replies in a loop."""
    from genlab_core.engagement.poller import THREADS_POLL_INTERVAL, poll_threads_comments

    logger.info("Starting Threads poller for niche=%s user=%s interval=%ds",
                niche_id, user_id, THREADS_POLL_INTERVAL)

    while True:
        try:
            comments = await poll_threads_comments(niche_id, user_id)
            if comments:
                count = _dispatch_to_dramatiq(comments, niche_id)
                logger.info("Threads: dispatched %d comments to Dramatiq", count)
        except Exception as e:
            logger.error("Threads poll error: %s", e, exc_info=True)

        await asyncio.sleep(THREADS_POLL_INTERVAL)


def _resolve_id(yaml_value: str, niche_id: str, env_suffix: str) -> str:
    """Resolve an ID, preferring YAML value, falling back to prefixed env var.

    The YAML files document the convention that channel/user IDs are populated
    from `{NICHE_PREFIX}_{ENV_SUFFIX}` env vars (e.g. CRITICALRUSH_YOUTUBE_CHANNEL_ID).
    This helper actually wires that intent — callers don't have to.
    """
    val = (yaml_value or "").strip()
    if val and val not in ("", "0"):
        return val

    from genlab_core.publishing.niche_credentials import NICHE_CREDENTIAL_PREFIXES
    prefix = NICHE_CREDENTIAL_PREFIXES.get(niche_id, "")
    if not prefix:
        return ""
    return os.environ.get(f"{prefix}_{env_suffix}", "").strip()


def _load_poller_config() -> dict:
    """Load engagement poller config from YAML."""
    from pathlib import Path

    import yaml

    config_path = Path(__file__).resolve().parent.parent / "config" / "engagement_pollers.yaml"
    if not config_path.exists():
        logger.warning("Poller config not found at %s", config_path)
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f).get("pollers", {})


async def _idle_forever(reason: str) -> None:
    """Sleep indefinitely so systemd doesn't restart-loop when no work to do."""
    logger.warning("%s — entering idle sleep (no restart loop)", reason)
    while True:
        await asyncio.sleep(3600)


async def _run_all_pollers(platform: str) -> None:
    """Run pollers for all niches concurrently."""
    config = _load_poller_config()
    if not config:
        await _idle_forever("No poller configuration found")
        return

    tasks = []
    for niche_id, platforms in config.items():
        if platform in ("all", "youtube") and "youtube" in platforms:
            channel_id = _resolve_id(
                platforms["youtube"].get("channel_id", ""),
                niche_id, "YOUTUBE_CHANNEL_ID",
            )
            if channel_id:
                tasks.append(_poll_loop_youtube(niche_id, channel_id))
                logger.info("Queued YouTube poller for %s (channel=%s)", niche_id, channel_id)

        if platform in ("all", "twitter") and "twitter" in platforms:
            user_id = _resolve_id(
                str(platforms["twitter"].get("user_id", "")),
                niche_id, "X_USER_ID",
            )
            if user_id:
                tasks.append(_poll_loop_twitter(niche_id, user_id))
                logger.info("Queued Twitter poller for %s (user=%s)", niche_id, user_id)

        if platform in ("all", "threads") and "threads" in platforms:
            threads_user_id = _resolve_id(
                str(platforms["threads"].get("user_id", "")),
                niche_id, "THREADS_USER_ID",
            )
            if threads_user_id:
                tasks.append(_poll_loop_threads(niche_id, threads_user_id))
                logger.info("Queued Threads poller for %s (user=%s)", niche_id, threads_user_id)

    if not tasks:
        # Idle instead of exit(1) so systemd doesn't restart-loop when only
        # some platforms are provisioned. The service will pick up new IDs
        # on next restart (e.g. after .env update + systemctl restart).
        await _idle_forever(f"No valid pollers configured for platform={platform}")
        return

    logger.info("Starting %d poller tasks concurrently", len(tasks))
    await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Engagement comment poller")
    parser.add_argument("--niche", required=True,
                       help="Niche ID (e.g., gaming, ai_creators) or 'all'")
    parser.add_argument("--platform", required=True, choices=["youtube", "twitter", "threads", "all"],
                       help="Platform to poll (or 'all')")
    parser.add_argument("--channel-id", default="", help="YouTube channel ID (single-niche mode)")
    parser.add_argument("--user-id", default="", help="X/Twitter user ID (single-niche mode)")
    args = parser.parse_args()

    mode = "DISPATCH" if _is_dispatch_enabled() else "OBSERVE"
    logger.info("Engagement poller starting — mode=%s niche=%s platform=%s",
                mode, args.niche, args.platform)

    # Multi-niche mode
    if args.niche == "all":
        asyncio.run(_run_all_pollers(args.platform))
        return

    # Single-niche mode (backward compatible)
    if args.platform == "youtube":
        if not args.channel_id:
            logger.error("--channel-id is required for youtube platform")
            sys.exit(1)
        asyncio.run(_poll_loop_youtube(args.niche, args.channel_id))

    elif args.platform == "twitter":
        if not args.user_id:
            logger.error("--user-id is required for twitter platform")
            sys.exit(1)
        asyncio.run(_poll_loop_twitter(args.niche, args.user_id))


if __name__ == "__main__":
    main()
