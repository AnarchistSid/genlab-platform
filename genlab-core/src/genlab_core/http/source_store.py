"""Sources persistence, extracted from BacklogClient.

Three methods live in this module:

* :meth:`SourceStore.create_source` — record a new RSS / YouTube
  / Reddit source row.
* :meth:`SourceStore.update_source_fetch_status` — record the most
  recent fetch attempt (timestamp + status code/string).
* :meth:`SourceStore.get_enabled_sources` — list rows where
  ``enabled=TRUE`` (optional ``niche_id`` filter).

Tier 2 / audit S-2, slice 2.4 (sources half) — fourth focused
extraction from the BacklogClient god class. BacklogClient retains
all 3 method names + signatures as thin delegators so the public API
stays byte-stable.

Constructor shape is the simplest in the family: just ``backend``.
Like Templates, the Sources list is always present in
``lists_config.yaml`` — none of the original methods guarded on
``if not self.sources``, so there's no truthy gate to mirror.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


class SourceStore:
    """CRUD over the Sources table."""

    def __init__(self, backend: Callable[[str], Any]) -> None:
        self._backend = backend

    # ── Create ─────────────────────────────────────────────────────

    def create_source(self, source: dict) -> str:
        """Record a new content source (RSS feed, YouTube channel,
        etc.). Returns the new record id."""
        record = self._backend("Sources").create(
            "Sources",
            {
                "source_id": source["source_id"],
                "domain": source["domain"],
                "name": source.get("name", source["domain"]),
                "url": source["url"],
                "type": source["type"],
                "priority": source.get("priority", 1.0),
                "enabled": source.get("enabled", True),
                "authority_score": source.get("authority_score", 0.5),
            },
        )
        return record["id"]

    # ── Update ─────────────────────────────────────────────────────

    def update_source_fetch_status(self, source_id: str, status: str) -> None:
        """Record the most recent fetch attempt for a source.

        Stamps ``last_fetch_at`` (UTC ISO 8601) + ``last_fetch_status``.
        No-op when the source doesn't exist — matches the historical
        behaviour.
        """
        records = self._backend("Sources").find(
            "Sources",
            formula=f"{{source_id}}='{_esc(source_id)}'",
            max_records=1,
        )
        if not records:
            return
        self._backend("Sources").update(
            "Sources",
            records[0]["id"],
            {
                "last_fetch_at": datetime.now(UTC).isoformat(),
                "last_fetch_status": status,
            },
        )

    # ── Read ───────────────────────────────────────────────────────

    def get_enabled_sources(self, *, niche_id: str | None = None) -> list[dict]:
        return self._backend("Sources").find(
            "Sources",
            formula="{enabled}=TRUE()",
            niche_id=niche_id,
        )
