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


def _post_id_with_platform_prefix(platform: str, post_id: str) -> str:
    """Return ``{platform}:{post_id}`` idempotently.

    Task #623 (2026-07-09) — prevents the double-prefix bug where
    ``platform_post_id`` was already prefixed upstream (by
    ``feedback_registration._normalize_post_id``) and this class then
    added another prefix, producing ``facebook:facebook:1181...``.

    Task #624 (2026-07-09) — delegates to
    ``genlab_core.cache.post_id_norm.normalize_post_id``, the single
    canonical implementation. This wrapper is preserved so the
    existing ``from pending_feedback_task import _post_id_with_platform_prefix``
    imports (there is one in ``tests/learning/``) don't break.
    """
    from genlab_core.cache.post_id_norm import normalize_post_id

    return normalize_post_id(platform, post_id)


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
    # AGENT-AUTONOMY-RESEARCH Move #8 — per-decision IPS propensity.
    # ``propensity`` is ``p(bandit_arm | bandit_context)`` under the
    # policy that picked the arm. ``temperature`` is the softmax
    # temperature that was active when the decision was made (None in
    # deterministic mode). Both are logged at decision time because
    # adding them retroactively is impossible. See
    # ``learning/linucb.py.select_with_propensity`` for the producer.
    propensity: float | None = None
    temperature: float | None = None
    # Intelligent transformation sprint (2026-07-05, migration a7v8w9x0y1z2).
    # Multi-arm attribution carrier: N transformation-dimension arm
    # assignments per reel. Iterated by transformation_reward_router at
    # each collection window to update N Beta posteriors from one
    # publish. Empty dict is the default (transformation off or pre-
    # sprint publishes) — the router skips iteration on empty.
    arm_ids_by_dimension: dict[str, str] = Field(default_factory=dict)
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
        """Serialise to storage fields (Postgres promoted columns + extra).

        Uses lowercase keys matching Postgres promoted columns in
        PROMOTED_COLUMNS["pending_feedback"].
        """
        import json as _json

        # task_id must include platform: the publisher calls create()
        # once per platform with the same content_id, and pending_feedback
        # has UNIQUE(task_id). Without the platform suffix only the first
        # platform (IG by alphabetical order) gets a row — every other
        # platform raises "duplicate key value violates unique constraint
        # pending_feedback_task_id_key". 2026-05-21 forensics: 62 IG / 7
        # YT / 0 FB / 0 X / 0 Threads rows in the last 14 days, meaning
        # the bandit has effectively only received Instagram reward signal
        # since the table was designed.
        fields: dict = {
            "Title": f"{self.platform}__{self.platform_post_id}",
            # Postgres promoted columns (lowercase)
            "niche_id": self.niche_id,
            "task_id": f"{self.content_id}__{self.platform}",
            # Stored as ``platform:native`` to match analytics.post_id so reward
            # can be joined to real engagement. The live metric fetch strips the
            # prefix (metric_collector.fetch_platform_metrics), and the store
            # round-trips post_id -> platform_post_id consistently, so this is
            # backward-compatible with the reward pipeline.
            #
            # 2026-07-09 (task #623): idempotent prefixing. The pre-fix line
            # unconditionally prepended ``{platform}:`` even when ``platform_post_id``
            # already had that prefix — 297 pending_feedback rows and ~400 across
            # publishing_analytics ended up as ``facebook:facebook:1181...``.
            # ``metric_collector.fetch_platform_metrics:86`` strips ONE prefix,
            # leaving still-invalid ``facebook:1181...`` which the Meta/YT/etc
            # APIs reject → every metric fetch returned 0 → every reward = 0 →
            # the bandit received "everything fails" signal for weeks. This
            # single line is the root cause of the entire learning stack being
            # functionally dormant. Idempotent prefixing (skip if already
            # ``{platform}:``) matches the same pattern used in
            # ``feedback_registration._normalize_post_id`` and
            # ``analytics_store`` at line 211.
            "post_id": _post_id_with_platform_prefix(self.platform, self.platform_post_id),
            "platform": self.platform,
            "arm_id": self.bandit_arm or "",
            "bandit_context": (_json.dumps(self.bandit_context) if self.bandit_context else ""),
            "collection_status": self.collection_status,
            "publish_time": self.published_at.isoformat(),
            # Extra fields (go to JSONB)
            "content_type": self.content_type,
            "hook_type": self.hook_type,
            "early_stop": self.early_stop,
        }
        if self.hook_text:
            fields["hook_text"] = self.hook_text[:500]
            fields["hook_length"] = self.hook_length or len(self.hook_text)
        # IPS propensity fields go to promoted columns (Move #8). Only
        # write when set — preserves backward compat with the (many)
        # callers that don't yet populate propensity.
        if self.propensity is not None:
            fields["propensity"] = self.propensity
        if self.temperature is not None:
            fields["temperature"] = self.temperature
        # Intelligent transformation multi-arm attribution (PR 5, 2026-07-05).
        # Only write when non-empty — legacy publishes keep the promoted
        # column at the SQL DEFAULT '{}'. JSONB serialization matches
        # bandit_context pattern above.
        if self.arm_ids_by_dimension:
            fields["arm_ids_by_dimension"] = _json.dumps(self.arm_ids_by_dimension)
        return fields
