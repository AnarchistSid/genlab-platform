"""Outbound engagement target discovery — pure business logic, no I/O.

## Growth mechanic

2026-07-17 (Layer 4 growth engine). Audit round 4 identified outbound
reply-to-top-comments on OTHER creators' videos as the SINGLE highest-
impact growth mechanic on IG + YT algorithms for 2024-26:

    "A witty reply on a video with 500K views can itself earn 10K+
    likes, and every like drives profile visits from an audience
    that has PROVEN affinity for the exact niche. At the current
    0.5 follower/day baseline for ai_creators FB, one viral reply
    on an AI-explainer video with 200K views has historically netted
    creators 500-2000 followers overnight — a single-event 1000×
    on current growth rate, repeatable daily per channel."

Gen Lab has ~90% of the infrastructure already: persona_engine (LLM
reply generation), toxicity_gate (safety), top_creators.yaml
(watchlist), rate_limiter (per-creator caps), idempotency dedup.
This module is the missing target-discovery layer.

## Contract

`discover_youtube_targets(niche_id, ...)` returns a list of
`OutboundTarget` dataclasses that a downstream poller can turn into
reply actions. Pure discovery + filter — no HTTP calls made here
(the platform-client abstraction handles that).

## Filtering discipline

Each target passes multiple filters before being returned:

1. Comment must be ≥20 chars (skip emoji-only + "first!")
2. Author must not be the channel owner (don't reply to creator's
   own comments — looks weird)
3. Comment must not already have a reply from us (idempotency —
   caller passes `already_replied_comment_ids` set)
4. Video must be ≤7d old (older videos don't drive new-follower
   discovery — the algo has stopped promoting them)
5. Video's comment count must be ≥20 (skip low-engagement videos
   where our reply won't get visibility)

Additional filters that happen at the CALLER level (not here):
- Rate limit: 1 comment/creator/week per niche (via rate_limiter)
- Toxicity gate on generated reply (via toxicity_gate)
- Layer 4 policy: skip monetized-competitor videos (future work)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


# Default filter thresholds (tunable via kwargs to discover_*)
DEFAULT_MIN_COMMENT_CHARS = 20
DEFAULT_MIN_VIDEO_COMMENT_COUNT = 20
DEFAULT_MAX_VIDEO_AGE_DAYS = 7
DEFAULT_MAX_TARGETS_PER_CREATOR = 3  # avoid spam-flag concentration
DEFAULT_MAX_TARGETS_PER_NICHE = 15  # per-run cap; per-day cap enforced upstream


@dataclass
class OutboundTarget:
    """One reply candidate — a specific comment on a specific video.

    Discovered by the target module, passed to the poller which
    generates a reply via persona_engine + posts it via platform
    client's post_reply.
    """

    platform: str  # "youtube" | "instagram"
    niche_id: str
    video_id: str  # source video (parent of the comment)
    video_channel_id: str  # who owns the video (avoid replying to own)
    video_title: str
    video_view_count: int
    comment_id: str  # the comment to reply to
    comment_author_channel_id: str
    comment_author_display_name: str
    comment_text: str
    comment_like_count: int


def _passes_comment_filter(
    text: str,
    author_channel_id: str,
    video_channel_id: str,
    *,
    min_chars: int,
) -> bool:
    """Comment-level filter: length, not-owner."""
    if not text or len(text.strip()) < min_chars:
        return False
    # Don't reply to the video owner's own comments
    if author_channel_id and author_channel_id == video_channel_id:
        return False
    # Skip common low-value patterns
    lower = text.strip().lower()
    if lower in ("first", "second", "first!", "early"):
        return False
    return True


def _passes_video_filter(
    video_published_at: str,
    comment_count: int,
    *,
    max_age_days: int,
    min_comment_count: int,
) -> bool:
    """Video-level filter: recency + engagement floor."""
    if comment_count < min_comment_count:
        return False
    try:
        published = datetime.fromisoformat(video_published_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    age = datetime.now(UTC) - published
    return age <= timedelta(days=max_age_days)


def _rank_comments(comments: list[dict]) -> list[dict]:
    """Sort comments by like_count descending — reply to top comments.

    Top-comment replies get maximum visibility because YT surfaces
    them prominently. Skip the top 1-2 (usually pinned by the
    creator; replying there may look like piggy-backing) and target
    positions 3-5.
    """
    scored = sorted(
        comments, key=lambda c: int(c.get("like_count", 0) or 0), reverse=True
    )
    # Skip position 0-1, target 2-5 (0-indexed → positions 3-6)
    return scored[2:6] if len(scored) >= 3 else scored


def discover_youtube_targets(
    niche_id: str,
    creator_recent_videos: list[dict],
    *,
    already_replied_comment_ids: set[str] | None = None,
    min_comment_chars: int = DEFAULT_MIN_COMMENT_CHARS,
    min_video_comment_count: int = DEFAULT_MIN_VIDEO_COMMENT_COUNT,
    max_video_age_days: int = DEFAULT_MAX_VIDEO_AGE_DAYS,
    max_targets_per_creator: int = DEFAULT_MAX_TARGETS_PER_CREATOR,
    max_targets_per_niche: int = DEFAULT_MAX_TARGETS_PER_NICHE,
) -> list[OutboundTarget]:
    """Filter + rank creator videos + their comments into OutboundTargets.

    ``creator_recent_videos`` is a list of dicts, each shaped::

        {
            "video_id": "...",
            "channel_id": "...",         # video owner (top creator)
            "title": "...",
            "view_count": int,
            "comment_count": int,
            "published_at": "ISO 8601",
            "comments": [                # top-N recent comments
                {
                    "comment_id": "...",
                    "author_channel_id": "...",
                    "author_display_name": "...",
                    "text": "...",
                    "like_count": int,
                },
                ...
            ],
        }

    Caller (typically ``outbound_poller.py``) fetches the shape via
    YouTube Data API v3.

    Returns up to ``max_targets_per_niche`` targets, distributed
    across creators (max ``max_targets_per_creator`` per creator to
    avoid spam-flag concentration).
    """
    already_replied = already_replied_comment_ids or set()
    targets: list[OutboundTarget] = []
    per_creator_count: dict[str, int] = {}

    for video in creator_recent_videos:
        # Per-niche cap enforced at BOTH outer + inner loop boundaries.
        # Inner-only would let a `break` on per-creator-cap skip the
        # inner-loop's cap check, letting the outer loop overshoot.
        # test_enforces_max_targets_per_niche caught this exact off-by-N.
        if len(targets) >= max_targets_per_niche:
            return targets
        video_channel_id = str(video.get("channel_id") or "")
        if per_creator_count.get(video_channel_id, 0) >= max_targets_per_creator:
            continue

        if not _passes_video_filter(
            str(video.get("published_at") or ""),
            int(video.get("comment_count", 0) or 0),
            max_age_days=max_video_age_days,
            min_comment_count=min_video_comment_count,
        ):
            continue

        comments = video.get("comments") or []
        for comment in _rank_comments(comments):
            comment_id = str(comment.get("comment_id") or "")
            if not comment_id or comment_id in already_replied:
                continue

            if not _passes_comment_filter(
                str(comment.get("text") or ""),
                str(comment.get("author_channel_id") or ""),
                video_channel_id,
                min_chars=min_comment_chars,
            ):
                continue

            targets.append(
                OutboundTarget(
                    platform="youtube",
                    niche_id=niche_id,
                    video_id=str(video.get("video_id") or ""),
                    video_channel_id=video_channel_id,
                    video_title=str(video.get("title") or ""),
                    video_view_count=int(video.get("view_count", 0) or 0),
                    comment_id=comment_id,
                    comment_author_channel_id=str(
                        comment.get("author_channel_id") or ""
                    ),
                    comment_author_display_name=str(
                        comment.get("author_display_name") or ""
                    ),
                    comment_text=str(comment.get("text") or ""),
                    comment_like_count=int(comment.get("like_count", 0) or 0),
                )
            )
            per_creator_count[video_channel_id] = (
                per_creator_count.get(video_channel_id, 0) + 1
            )
            if per_creator_count[video_channel_id] >= max_targets_per_creator:
                break
            if len(targets) >= max_targets_per_niche:
                return targets

    return targets


__all__ = [
    "DEFAULT_MAX_TARGETS_PER_CREATOR",
    "DEFAULT_MAX_TARGETS_PER_NICHE",
    "DEFAULT_MAX_VIDEO_AGE_DAYS",
    "DEFAULT_MIN_COMMENT_CHARS",
    "DEFAULT_MIN_VIDEO_COMMENT_COUNT",
    "OutboundTarget",
    "discover_youtube_targets",
]
