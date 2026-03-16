"""Platform reply clients for the engagement engine.

Each client provides post_reply(comment_id, text, niche_id) -> bool.
Registry provides get_reply_client(platform) for lazy lookup.
"""
from __future__ import annotations

from typing import Optional, Protocol


class ReplyClient(Protocol):
    """Protocol for platform reply clients."""

    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Post a reply to a comment. Returns True on success."""
        ...


def get_reply_client(platform: str) -> Optional[ReplyClient]:
    """Return a reply client for the given platform, or None if unsupported."""
    from genlab_core.engagement.platform_clients.youtube_reply import YouTubeReplyClient
    from genlab_core.engagement.platform_clients.instagram_reply import InstagramReplyClient
    from genlab_core.engagement.platform_clients.facebook_reply import FacebookReplyClient
    from genlab_core.engagement.platform_clients.twitter_reply import TwitterReplyClient
    from genlab_core.engagement.platform_clients.threads_reply import ThreadsReplyClient

    registry: dict[str, type] = {
        "youtube": YouTubeReplyClient,
        "instagram": InstagramReplyClient,
        "facebook": FacebookReplyClient,
        "twitter": TwitterReplyClient,
        "x": TwitterReplyClient,
        "threads": ThreadsReplyClient,
    }
    cls = registry.get(platform.lower())
    return cls() if cls else None
