"""Layered protocols for platform clients.

Publisher is required. Engageable, Trackable, HealthCheckable are optional.
Use isinstance() checks at dispatch time to determine capabilities.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from genlab_core.platforms.models import (
        PlatformMetrics,
        PublishPayload,
        PublishResult,
        TokenStatus,
    )


@runtime_checkable
class Publisher(Protocol):
    """Core protocol — every platform must implement publish()."""

    platform_id: str

    def publish(self, payload: PublishPayload) -> PublishResult: ...


@runtime_checkable
class Engageable(Protocol):
    """Optional: reply to comments, like posts."""

    def post_reply(
        self, parent_id: str, text: str, *, context_id: str = ""
    ) -> bool: ...

    def like(self, target_id: str, *, context_id: str = "") -> bool: ...


@runtime_checkable
class Trackable(Protocol):
    """Optional: collect analytics/metrics for a published post."""

    def get_metrics(
        self, post_id: str, published_at: datetime
    ) -> PlatformMetrics | None: ...


@runtime_checkable
class HealthCheckable(Protocol):
    """Optional: verify token health for the platform."""

    def check_token_health(self) -> TokenStatus: ...
