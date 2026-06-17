"""Push events to the dashboard_events table for real-time notification display.

Usage:
    from genlab_core.observability.dashboard_events import push_event
    push_event("pipeline_complete", "Pipeline Complete", "Gaming run finished in 45s", niche_id="gaming")
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def push_event(
    event_type: str,
    title: str,
    body: str = "",
    *,
    entity_id: str = "",
    entity_type: str = "",
    niche_id: str = "",
) -> None:
    """Insert a dashboard notification event into the database.

    Non-blocking: failures are logged but never raise.
    """
    try:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return
        # SR-A/C/D Tier-1 migration (2026-06-17): route through
        # pg_connect so the RLS GUC is set from niche_id. Behaviour
        # preserved (Phase-1 admin-mode fallback when env flag unset).
        # Passing niche_id="" stays in admin mode explicitly — which
        # matches the caller's intent when no niche is supplied.
        from genlab_core.storage.tenant_context import pg_connect

        # Empty niche_id → admin mode. Explicit "" matches the
        # ContextVar's "none-set" sentinel semantics: callers who
        # have a niche pass it; callers who don't (cross-cutting
        # events) get admin.
        effective_niche = niche_id or ""
        with pg_connect(dsn, niche_id=effective_niche) as conn:
            conn.execute(
                """INSERT INTO dashboard_events (event_type, title, body, entity_id, entity_type, niche_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (event_type, title, body, entity_id, entity_type, niche_id),
            )
    except Exception as e:
        logger.debug("Failed to push dashboard event: %s", e)
