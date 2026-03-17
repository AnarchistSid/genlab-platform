"""PendingFeedbackTask: records what the bandit decided at publish time.

At publish time, we know:
  - Which content was published (content_id)
  - Which bandit arm was selected (bandit_arm)
  - What context the bandit saw (bandit_context as JSON)
  - Which platform it was published to
  - When it was published

24-48 hours later, the FeedbackCollector reads these records and:
  1. Fetches actual metrics from the platform APIs
  2. Computes a shaped reward
  3. Updates the Thompson Sampling bandit
  4. Sets collection_status = "complete"
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CollectionWindow = Literal["6h", "24h", "48h", "168h"]
CollectionStatus = Literal[
    "awaiting_6h",
    "awaiting_24h",
    "awaiting_48h",
    "awaiting_168h",
    "complete",
    "error",
    "early_stopped",
]


class PendingFeedbackTask(BaseModel):
    """One record per published post. Tracks metric collection progress."""

    content_id: str
    platform: str
    niche_id: str = "gaming"
    published_at: datetime
    platform_post_id: str
    content_type: str = "unknown"
    hook_type: str = ""
    hook_text: str = ""
    hook_length: int = 0
    sharepoint_id: str | None = None
    bandit_arm: str | None = None
    bandit_context: dict | None = None
    collection_windows: list[CollectionWindow] = Field(
        default_factory=lambda: ["6h", "24h", "48h", "168h"]
    )
    completed_windows: list[CollectionWindow] = Field(default_factory=list)
    collection_status: CollectionStatus = "awaiting_6h"
    early_stop: bool = False
    reward_48h: float | None = None
    error_message: str | None = None

    @property
    def pending_windows(self) -> list[CollectionWindow]:
        return [w for w in self.collection_windows if w not in self.completed_windows]

    @property
    def is_complete(self) -> bool:
        return self.collection_status in ("complete", "error", "early_stopped")

    def to_sharepoint_fields(self) -> dict:
        """Serialise to SharePoint list field format."""
        fields: dict = {
            "Title": f"{self.platform}__{self.platform_post_id}",
            "Platform": self.platform,
            "PostID": self.platform_post_id,
            "NicheId": self.niche_id,
            "PostContentType": self.content_type,
            "HookType": self.hook_type,
            "PublishedAt": self.published_at.isoformat(),
            "Status": self.collection_status,
            "BanditArm": self.bandit_arm or "",
            "BanditContext": (
                __import__("json").dumps(self.bandit_context)
                if self.bandit_context
                else ""
            ),
            "EarlyStop": self.early_stop,
        }
        if self.hook_text:
            fields["HookText"] = self.hook_text[:500]
            fields["HookLength"] = self.hook_length or len(self.hook_text)
        return fields
