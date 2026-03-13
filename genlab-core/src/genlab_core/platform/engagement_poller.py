"""DEPRECATED: Use genlab_core.engagement.poller instead.

Deprecated in Sprint 24 (2026-03-10).
"""
import warnings as _warnings
_warnings.warn(
    "genlab_core.platform.engagement_poller is deprecated. "
    "Use genlab_core.engagement.poller instead.",
    DeprecationWarning,
    stacklevel=2,
)

from genlab_core.engagement.poller import (  # noqa: F401
    YOUTUBE_POLL_INTERVAL,
    TWITTER_POLL_INTERVAL,
    poll_youtube_comments,
    poll_twitter_mentions,
)
