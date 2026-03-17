"""DEPRECATED: Use genlab_core.engagement.tasks instead.

Deprecated in Sprint 24 (2026-03-10).
"""
import warnings as _warnings

_warnings.warn(
    "genlab_core.platform._engagement_worker is deprecated. "
    "Use genlab_core.engagement.tasks instead.",
    DeprecationWarning,
    stacklevel=2,
)

from genlab_core.engagement.tasks import (  # noqa: F401
    reply_to_comment_high as process_comment_task,
)
