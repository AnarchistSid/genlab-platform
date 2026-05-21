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
        import psycopg

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return
        with psycopg.connect(dsn) as conn:
            conn.execute(
                """INSERT INTO dashboard_events (event_type, title, body, entity_id, entity_type, niche_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (event_type, title, body, entity_id, entity_type, niche_id),
            )
    except Exception as e:
        logger.debug("Failed to push dashboard event: %s", e)
