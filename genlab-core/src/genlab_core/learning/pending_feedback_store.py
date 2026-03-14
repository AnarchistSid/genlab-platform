"""PendingFeedbackStore — SharePoint Lists CRUD for feedback tasks.

Records bandit context at publish time. At each collection window
(6h, 24h, 48h, 168h), the metric collector updates the stored task
with platform metrics. After 48h (primary reward signal), the task
is ready for bandit partial_fit().

Uses BacklogClient.pending_feedback proxy (GraphTableProxy) for
all SharePoint operations.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from genlab_core.learning.pending_feedback_task import (
    PendingFeedbackTask,
    CollectionStatus,
    CollectionWindow,
)

logger = logging.getLogger(__name__)


# Status transitions: awaiting_6h → awaiting_24h → awaiting_48h → awaiting_168h → complete
_NEXT_STATUS: dict[CollectionWindow, CollectionStatus] = {
    "6h": "awaiting_24h",
    "24h": "awaiting_48h",
    "48h": "awaiting_168h",
    "168h": "complete",
}

# Minimum age (hours) before a window is eligible for collection
_WINDOW_MIN_HOURS: dict[CollectionWindow, float] = {
    "6h": 6.0,
    "24h": 24.0,
    "48h": 48.0,
    "168h": 168.0,
}


class PendingFeedbackStore:
    """SharePoint Lists-backed store for PendingFeedbackTask records."""

    def __init__(self, backlog_client: Any) -> None:
        self._client = backlog_client

    @property
    def _proxy(self):
        """Get the pending_feedback GraphTableProxy from BacklogClient."""
        proxy = getattr(self._client, "pending_feedback", None)
        if proxy is None:
            raise RuntimeError(
                "BacklogClient has no pending_feedback proxy. "
                "Add PendingFeedback list_id to lists_config.yaml."
            )
        return proxy

    def create(self, task: PendingFeedbackTask) -> None:
        """Record a new pending feedback task at publish time."""
        try:
            fields = task.to_sharepoint_fields()
            self._proxy.create(fields)
            logger.info(
                "[feedback] recorded: %s on %s (arm=%s)",
                task.content_id, task.platform, task.bandit_arm,
            )
        except Exception as e:
            logger.warning(
                "[feedback] create failed for %s: %s", task.content_id, e,
            )

    def get_pending(
        self,
        niche_id: str | None = None,
    ) -> list[PendingFeedbackTask]:
        """Fetch tasks not yet complete."""
        try:
            # OData filter: Status not in terminal states
            items = self._proxy.all(
                formula="{Status}='awaiting_6h'",
            )
            # Also fetch other non-terminal statuses
            for status in ("awaiting_24h", "awaiting_48h", "awaiting_168h"):
                items.extend(
                    self._proxy.all(formula=f"{{Status}}='{status}'")
                )

            tasks = []
            for item in items:
                try:
                    task = self._from_sharepoint_item(item)
                    if niche_id and task.niche_id != niche_id:
                        continue
                    tasks.append(task)
                except Exception as e:
                    logger.debug("[feedback] parse error: %s", e)
            return tasks
        except Exception as e:
            logger.warning("[feedback] get_pending failed: %s", e)
            return []

    def next_collection_window(
        self,
        task: PendingFeedbackTask,
        now: datetime | None = None,
    ) -> CollectionWindow | None:
        """Return next uncollected window that is old enough, or None."""
        now = now or datetime.now(timezone.utc)
        pub = task.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_hours = (now - pub).total_seconds() / 3600

        for window in task.pending_windows:
            min_hours = _WINDOW_MIN_HOURS.get(window, 0)
            if age_hours >= min_hours:
                return window
        return None

    def update_window(
        self,
        task: PendingFeedbackTask,
        window: CollectionWindow,
        reward_48h: float | None = None,
    ) -> None:
        """Mark a collection window as completed."""
        try:
            task.completed_windows.append(window)
            next_status = _NEXT_STATUS.get(window, task.collection_status)
            task.collection_status = next_status

            if window == "48h" and reward_48h is not None:
                task.reward_48h = reward_48h

            update_fields: dict[str, Any] = {
                "Status": task.collection_status,
                "EarlyStop": task.early_stop,
            }
            if reward_48h is not None:
                update_fields["Reward48h"] = reward_48h

            # Use sharepoint_id if available, otherwise find by title
            record_id = task.sharepoint_id
            if not record_id:
                title = f"{task.platform}__{task.platform_post_id}"
                logger.debug("[feedback] no sharepoint_id, finding by Title=%s", title)
                matches = self._proxy.all(
                    formula=f"{{Title}}='{title}'",
                    max_records=1,
                )
                if matches:
                    record_id = matches[0].get("id")

            if record_id:
                self._proxy.update(record_id, update_fields)

            logger.info(
                "[feedback] updated %s window=%s status=%s",
                task.platform_post_id, window, task.collection_status,
            )
        except Exception as e:
            logger.warning("[feedback] update_window failed: %s", e)

    @staticmethod
    def _from_sharepoint_item(item: dict) -> PendingFeedbackTask:
        """Parse SharePoint item into PendingFeedbackTask."""
        fields = item.get("fields", item)
        bandit_ctx_raw = fields.get("BanditContext", "")
        bandit_ctx = json.loads(bandit_ctx_raw) if bandit_ctx_raw else None

        status = fields.get("Status", "awaiting_6h")

        # Derive completed_windows from Status since they aren't persisted.
        # A task at awaiting_24h has already completed 6h, etc.
        _STATUS_TO_COMPLETED: dict[str, list[CollectionWindow]] = {
            "awaiting_6h": [],
            "awaiting_24h": ["6h"],
            "awaiting_48h": ["6h", "24h"],
            "awaiting_168h": ["6h", "24h", "48h"],
            "complete": ["6h", "24h", "48h", "168h"],
        }
        completed = _STATUS_TO_COMPLETED.get(status, [])

        raw_pub = fields.get("PublishedAt")
        if isinstance(raw_pub, datetime):
            published_at = raw_pub if raw_pub.tzinfo else raw_pub.replace(tzinfo=timezone.utc)
        elif isinstance(raw_pub, str) and raw_pub.strip():
            published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
        else:
            published_at = datetime.now(timezone.utc)

        return PendingFeedbackTask(
            content_id=fields.get("PostID", ""),
            platform=fields.get("Platform", ""),
            niche_id=fields.get("NicheId", "gaming"),
            published_at=published_at,
            platform_post_id=fields.get("PostID", ""),
            content_type=fields.get("PostContentType", "unknown"),
            hook_type=fields.get("HookType", ""),
            hook_text=fields.get("HookText", ""),
            hook_length=int(fields.get("HookLength", 0) or 0),
            sharepoint_id=item.get("id"),
            bandit_arm=fields.get("BanditArm") or None,
            bandit_context=bandit_ctx,
            collection_status=status,
            completed_windows=list(completed),
            early_stop=bool(fields.get("EarlyStop", False)),
        )
