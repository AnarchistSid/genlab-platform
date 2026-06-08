"""PendingEngagement persistence, extracted from BacklogClient.

The engagement-engine writes incoming-comment events here for the
auto-reply / review-queue dispatcher to consume. Three methods live
in this module:

* :meth:`EngagementStore.write_pending_engagement` — record an
  inbound comment event.
* :meth:`EngagementStore.list_pending_engagement` — read the queue
  (optionally filtered by niche + status).
* :meth:`EngagementStore.update_engagement_status` — transition a row
  to one of the seven valid terminal/intermediate states.

Tier 2 / audit S-2, slice 2.2 — second focused extraction from the
BacklogClient god class. BacklogClient retains all 3 method names +
signatures as thin one-line delegators so the public API stays
byte-stable.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


# The full set of statuses the engagement worker / comment_processor
# / dashboard reviewer may transition a row to. The audit found that
# ``pending_review`` had silently been dropped from this set, breaking
# the dashboard's review queue (rows generated but never appeared).
# Keep this list authoritative — adding a new state means adding it
# here AND updating comment_processor's branching.
_VALID_ENGAGEMENT_STATUSES: frozenset[str] = frozenset(
    {
        "replied",
        "liked",
        "skipped",
        "failed",
        "rate_limited",
        "pending",
        "pending_review",
    }
)


class EngagementStore:
    """CRUD over the PendingEngagement table.

    The constructor takes the already-built ``pending_engagement``
    proxy (the ``ScheduleGuardedProxy`` instance that BacklogClient
    builds in ``__init__``) so this store stays trivially testable
    via a ``MagicMock``.
    """

    def __init__(self, pending_engagement: Any) -> None:
        # May be None when the underlying SharePoint list isn't
        # configured (legacy installs). Every method below early-returns
        # with a warning in that case, matching the historical
        # behaviour.
        self._proxy = pending_engagement

    # ── Write ──────────────────────────────────────────────────────

    def write_pending_engagement(self, event: dict) -> str | None:
        """Record an incoming comment event for monitoring.

        Event dict should have: comment_id, platform, post_id, text,
        author_name, created_at (ISO string), niche_id. Status
        defaults to 'pending'.

        Returns the record ID on success, None on failure or when
        the proxy isn't configured.
        """
        if not self._proxy:
            logger.warning("[engagement] PendingEngagement table not configured")
            return None
        # Promoted columns (snake_case) go to SQL columns; non-promoted
        # fields go to extra JSONB.
        fields = {
            "platform": event.get("platform", ""),
            "post_id": event.get("post_id", ""),
            "niche_id": event.get("niche_id", ""),
            "status": "pending",
            # Non-promoted fields → extra JSONB
            "comment_id": event.get("comment_id", ""),
            "comment_text": (event.get("text") or "")[:2000],
            "author_name": event.get("author_name", ""),
        }
        try:
            result = self._proxy.create(fields)
            return str(result) if result else None
        except Exception as e:
            logger.warning("[engagement] write_pending_engagement failed: %s", e)
            return None

    # ── Read ───────────────────────────────────────────────────────

    def list_pending_engagement(
        self,
        niche_id: str | None = None,
        status: str = "pending",
        limit: int = 50,
    ) -> list[dict]:
        """Retrieve pending comment events for monitoring/processing."""
        if not self._proxy:
            logger.warning("[engagement] PendingEngagement table not configured")
            return []
        formula = f"{{status}}='{_esc(status)}'"
        if niche_id:
            formula = f"AND({{status}}='{_esc(status)}', {{niche_id}}='{_esc(niche_id)}')"
        try:
            return self._proxy.find(formula=formula, max_records=limit)
        except Exception as e:
            logger.warning("[engagement] list_pending_engagement failed: %s", e)
            return []

    # ── Update ─────────────────────────────────────────────────────

    def update_engagement_status(
        self,
        item_id: str,
        status: str,
        reply_text: str = "",
        error_msg: str = "",
    ) -> None:
        """Update the status of a pending engagement item.

        Args:
            item_id: Record ID (UUID or legacy SharePoint ID)
            status: New status. Must be one of
                :data:`_VALID_ENGAGEMENT_STATUSES`. Invalid values
                are rejected with a WARNING + no-op (historical
                behaviour — the dashboard's review queue silently
                broke for weeks in 2026-05 because ``pending_review``
                wasn't in this set).
            reply_text: The reply that was posted (if status=replied).
            error_msg: Error message (if status=failed).
        """
        if not self._proxy:
            logger.warning(
                "BacklogClient: pending_engagement proxy not configured — skipping status update"
            )
            return

        if status not in _VALID_ENGAGEMENT_STATUSES:
            logger.warning("BacklogClient: invalid engagement status '%s'", status)
            return

        # Promoted columns use snake_case to match SQL schema; non-promoted
        # fields go to extra JSONB.
        fields: dict[str, str] = {
            "status": status,
            "processed_at": datetime.now(UTC).isoformat(),
        }
        if reply_text:
            fields["reply_text"] = reply_text[:2000]
        if error_msg:
            fields["error_message"] = error_msg[:500]

        try:
            self._proxy.update(item_id, fields)
            logger.info("BacklogClient: engagement %s → %s", item_id, status)
        except Exception as e:
            logger.warning("BacklogClient: failed to update engagement %s: %s", item_id, e)
