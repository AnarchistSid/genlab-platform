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
from datetime import UTC, datetime
from typing import Any

from genlab_core.learning.pending_feedback_task import (
    CollectionStatus,
    CollectionWindow,
    PendingFeedbackTask,
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
                task.content_id,
                task.platform,
                task.bandit_arm,
            )
        except Exception as e:
            logger.warning(
                "[feedback] create failed for %s: %s",
                task.content_id,
                e,
            )

    def get_pending(
        self,
        niche_id: str | None = None,
    ) -> list[PendingFeedbackTask]:
        """Fetch tasks not yet complete."""
        try:
            # Postgres uses collection_status; SharePoint uses Status
            items = self._proxy.all(
                formula="{collection_status}='awaiting_6h'",
            )
            for status in ("awaiting_24h", "awaiting_48h", "awaiting_168h"):
                items.extend(self._proxy.all(formula=f"{{collection_status}}='{status}'"))

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
        now = now or datetime.now(UTC)
        pub = task.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
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
            # Preserve early_stopped/error status — don't overwrite with next window
            if task.collection_status not in ("early_stopped", "error"):
                next_status = _NEXT_STATUS.get(window, task.collection_status)
                task.collection_status = next_status

            if reward_48h is not None:
                task.reward_48h = reward_48h

            update_fields: dict[str, Any] = {
                "collection_status": task.collection_status,
                "early_stop": task.early_stop,
            }
            if reward_48h is not None:
                update_fields["reward_48h"] = reward_48h

            # Use sharepoint_id if available, otherwise find by title
            record_id = task.sharepoint_id
            if not record_id:
                title = f"{task.platform}__{task.platform_post_id}"
                logger.debug("[feedback] no sharepoint_id, finding by Title=%s", title)
                matches = self._proxy.all(
                    formula=f"{{post_id}}='{task.platform_post_id}'",
                    max_records=1,
                )
                if matches:
                    record_id = matches[0].get("id")

            if record_id:
                self._proxy.update(record_id, update_fields)

            logger.info(
                "[feedback] updated %s window=%s status=%s",
                task.platform_post_id,
                window,
                task.collection_status,
            )
        except Exception as e:
            logger.warning("[feedback] update_window failed: %s", e)

    def mark_bandit_processed(self, task: PendingFeedbackTask) -> None:
        """Stamp the row with ``extra.bandit_backfilled_at = now`` after a
        successful live bandit update.

        Reuses the same JSONB flag the one-shot backfill script
        (``backfill_bandit_from_pending_feedback.py``) writes — so the
        backfill's existing ``(extra->>'bandit_backfilled_at') IS NULL``
        filter correctly skips rows that the live path already handled.

        Without this stamp, the live updater and the backfill script
        were independent and unaware of each other. Any row the live
        updater processed silently looked like a "zombie" (reward_48h
        set, never replayed to bandit_arms) to the backfill script's
        ``--include-post-fix`` mode, causing a double-update on
        bandit_arms when the timer eventually ran. With this stamp,
        the daily backfill timer is safe to enable — same idempotency
        contract as the historical-replay invocation.

        Best-effort: a failure to set the flag does NOT roll back the
        live bandit_arms update (which has already happened by the
        time we get here). The worst case is a duplicate update when
        the backfill timer next fires — annoying but not destructive
        (bandit alpha/beta accumulate +1 each instead of +0.5 each).
        """
        try:
            record_id = task.sharepoint_id
            if not record_id:
                matches = self._proxy.all(
                    formula=f"{{post_id}}='{task.platform_post_id}'",
                    max_records=1,
                )
                if matches:
                    record_id = matches[0].get("id")
            if not record_id:
                logger.debug(
                    "[feedback] mark_bandit_processed: no record_id for %s",
                    task.platform_post_id,
                )
                return
            # Sending a key that isn't in PROMOTED_COLUMNS lands in the
            # extra JSONB via Postgres's ``extra = extra || %s::jsonb``
            # merge — which is exactly the storage shape the backfill
            # script reads via ``extra->>'bandit_backfilled_at'``.
            self._proxy.update(
                record_id,
                {"bandit_backfilled_at": datetime.now(UTC).isoformat()},
            )
            logger.debug(
                "[feedback] marked %s as bandit_processed",
                task.platform_post_id,
            )
        except Exception as exc:
            logger.warning(
                "[feedback] mark_bandit_processed failed for %s: %s",
                task.platform_post_id,
                exc,
            )

    @staticmethod
    def _f(fields: dict, *keys: str, default: Any = "") -> Any:
        """Get a field by trying CamelCase then snake_case key variants."""
        for k in keys:
            val = fields.get(k)
            if val is not None:
                return val
        return default

    @staticmethod
    def _from_sharepoint_item(item: dict) -> PendingFeedbackTask:
        """Parse SharePoint/Postgres item into PendingFeedbackTask.

        Handles both CamelCase (SharePoint) and snake_case (Postgres) column names
        by trying each variant via _f().
        """
        fields = item.get("fields", item)
        _f = PendingFeedbackStore._f

        # Postgres JSONB columns are auto-parsed to dicts; SharePoint stores
        # them as JSON strings. Handle both shapes — otherwise every Postgres
        # row would fail to parse and the bandit never updates.
        bandit_ctx_raw = _f(fields, "BanditContext", "bandit_context", default=None)
        if isinstance(bandit_ctx_raw, dict):
            bandit_ctx = bandit_ctx_raw
        elif isinstance(bandit_ctx_raw, str) and bandit_ctx_raw:
            bandit_ctx = json.loads(bandit_ctx_raw)
        else:
            bandit_ctx = None

        status = _f(fields, "Status", "status", "collection_status", default="awaiting_6h")

        # Derive completed_windows from Status since they aren't persisted.
        _STATUS_TO_COMPLETED: dict[str, list[CollectionWindow]] = {
            "awaiting_6h": [],
            "awaiting_24h": ["6h"],
            "awaiting_48h": ["6h", "24h"],
            "awaiting_168h": ["6h", "24h", "48h"],
            "complete": ["6h", "24h", "48h", "168h"],
        }
        completed = _STATUS_TO_COMPLETED.get(status, [])

        raw_pub = _f(fields, "PublishedAt", "published_at", "publish_time", default=None)
        if isinstance(raw_pub, datetime):
            published_at = raw_pub if raw_pub.tzinfo else raw_pub.replace(tzinfo=UTC)
        elif isinstance(raw_pub, str) and raw_pub.strip():
            published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
        else:
            published_at = datetime.now(UTC)

        post_id = _f(fields, "PostID", "post_id", default="")

        # 2026-06-14: hydrate reward_48h. Without this, every consumer of
        # _from_sharepoint_item (config_updater, backfill scripts, the
        # store's own list_pending caller chain) silently saw
        # ``reward_48h = None`` on every task — even when the DB row had
        # the value. The weekly config_updater filter
        # ``r.reward_48h is not None`` then dropped every row, which is
        # why config_updates has been empty since the table existed.
        raw_reward = _f(fields, "Reward48h", "reward_48h", default=None)
        if raw_reward in (None, ""):
            reward_48h: float | None = None
        else:
            try:
                reward_48h = float(raw_reward)
            except (TypeError, ValueError):
                reward_48h = None

        # AGENT-AUTONOMY-RESEARCH Move #8: hydrate IPS propensity +
        # temperature. Old rows predating the migration return None
        # for both — IPS estimators downstream MUST filter on
        # ``propensity is not None`` before applying the 1/p weight.
        def _opt_float(*keys: str) -> float | None:
            raw = _f(fields, *keys, default=None)
            if raw in (None, ""):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        propensity = _opt_float("Propensity", "propensity")
        temperature = _opt_float("Temperature", "temperature")

        # Intelligent transformation multi-arm attribution (PR 5, 2026-07-05).
        # Postgres JSONB auto-parses to dict; SharePoint stores as JSON
        # string. Handle both shapes for round-trip parity — same pattern
        # as bandit_context above.
        arm_ids_raw = _f(fields, "ArmIdsByDimension", "arm_ids_by_dimension", default=None)
        if isinstance(arm_ids_raw, dict):
            arm_ids_by_dim = arm_ids_raw
        elif isinstance(arm_ids_raw, str) and arm_ids_raw:
            try:
                arm_ids_by_dim = json.loads(arm_ids_raw)
                if not isinstance(arm_ids_by_dim, dict):
                    arm_ids_by_dim = {}
            except (ValueError, TypeError):
                arm_ids_by_dim = {}
        else:
            arm_ids_by_dim = {}

        return PendingFeedbackTask(
            content_id=post_id,
            platform=_f(fields, "Platform", "platform", default=""),
            niche_id=_f(fields, "NicheId", "niche_id", default="gaming"),
            published_at=published_at,
            platform_post_id=post_id,
            content_type=_f(fields, "PostContentType", "post_content_type", default="unknown"),
            hook_type=_f(fields, "HookType", "hook_type", default=""),
            hook_text=_f(fields, "HookText", "hook_text", default=""),
            hook_length=int(_f(fields, "HookLength", "hook_length", default=0) or 0),
            sharepoint_id=item.get("id"),
            bandit_arm=_f(fields, "BanditArm", "bandit_arm", "arm_id", default=None),
            bandit_context=bandit_ctx,
            collection_status=status,
            completed_windows=list(completed),
            early_stop=bool(_f(fields, "EarlyStop", "early_stop", default=False)),
            reward_48h=reward_48h,
            propensity=propensity,
            temperature=temperature,
            arm_ids_by_dimension=arm_ids_by_dim,
        )
