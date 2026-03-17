"""Postiz integration — ABANDONED.

Postiz self-hosted social publishing was evaluated but never fully configured.
All channels use bespoke per-platform publishers instead.

This stub module preserves backward-compatible imports so that code referencing
``PostizClient`` or ``ShadowPublisher`` doesn't crash at import time.
Both classes raise ``NotImplementedError`` on instantiation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PublishResult:
    """Stub publish result for Postiz (abandoned)."""

    def __init__(self, *, platform: str = "", status: str = "error", **kwargs: Any):
        self.platform = platform
        self.status = status
        self.error = kwargs.get("error", "Postiz integration abandoned")


class PostizClient:
    """Stub — Postiz integration was abandoned. Raises on init."""

    def __init__(self, **kwargs: Any):
        raise ValueError(
            "Postiz integration is abandoned. Use bespoke per-platform publishers."
        )

    def __enter__(self) -> "PostizClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def upload_media(self, path: str) -> dict:
        raise NotImplementedError

    def publish(self, **kwargs: Any) -> PublishResult:
        raise NotImplementedError


class ShadowPublisher:
    """Stub — Postiz shadow publishing was abandoned."""

    def __init__(self, **kwargs: Any):
        raise ValueError("Postiz shadow publishing is abandoned.")
