"""TikTok client stub — disabled pending TIKTOK_AUDIT_APPROVED=true.

Full implementation with upload logic: ``genlab_core.publishing.tiktok_client``.
When TikTok audit is approved, replace this stub with a delegation to that module.
"""
from __future__ import annotations

import os

from genlab_core.platforms.models import PublishPayload, PublishResult


class TikTokClient:
    platform_id = "tiktok"

    def __init__(self):
        self._enabled = os.environ.get("TIKTOK_AUDIT_APPROVED", "").lower() == "true"

    def publish(self, payload: PublishPayload) -> PublishResult:
        if not self._enabled:
            return PublishResult(
                platform="tiktok",
                success=False,
                error="TikTok publishing disabled pending audit (TIKTOK_AUDIT_APPROVED=true)",
            )
        return PublishResult(
            platform="tiktok",
            success=False,
            error="TikTok publish not yet implemented",
        )
