"""Stories persistence, extracted from BacklogClient.

Four methods live in this module:

* :meth:`StoryStore.create_story` — record a single story.
* :meth:`StoryStore.find_story_by_story_id` — single-row lookup
  with optional ``niche_id`` filter.
* :meth:`StoryStore.update_story_status` — find by story_id then
  update; raises ValueError if the row doesn't exist.
* :meth:`StoryStore.batch_create_stories` — bulk insert (uses
  backend.batch_create — far faster than a loop of .create).

Tier 2 / audit S-2, slice 2.5a — fifth focused extraction from the
BacklogClient god class. The companion extraction (AssetStore) lands
in 2.5b and consumes ``StoryStore.find_story_by_story_id`` for the
``create_asset`` and ``find_assets_by_story_id`` story-link lookups.

Constructor takes three callables — all bound methods or static
helpers from BacklogClient — so the store stays decoupled from the
host class:

* ``sp_call``: circuit-breaker wrapping helper.
* ``backend``: live backend lookup (so future backend swaps flow
  through here without rebuild).
* ``resolve_source``: ``BacklogClient._resolve_source`` —
  Source-list lookup helper, kept centralised because it depends on
  ``DOMAIN_SOURCE_MAP``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


class StoryStore:
    """CRUD over the Stories table."""

    def __init__(
        self,
        sp_call: Callable[..., Any],
        backend: Callable[[str], Any],
        resolve_source: Callable[[dict], str],
    ) -> None:
        self._sp_call = sp_call
        self._backend = backend
        self._resolve_source = resolve_source

    # ── Create ─────────────────────────────────────────────────────

    def create_story(self, story: dict) -> str:
        scores = story.get("scores", {})
        fields = {
            "story_id": story["story_id"],
            "title": story["title"],
            "url": story["url"],
            "source": self._resolve_source(story),
            "published_at": story.get("published_at"),
            "summary": story.get("summary", ""),
            "why_it_matters": story.get("why_it_matters", ""),
            "priority": story.get("priority", scores.get("priority", 0.5)),
            "status": "INTAKE",
            "themes": story.get("themes", []),
            "authority_score": scores.get("authority", 0.0),
            "recency_score": scores.get("recency", 0.0),
            "novelty_score": scores.get("novelty", 0.0),
        }
        if story.get("niche_id"):
            fields["niche_id"] = story["niche_id"]
        # PR #528 (2026-06-24, SR-C tenant binding): pass niche_id
        # through to backend's SET LOCAL app.niche_id. Mirrors PR #526
        # (BlueprintStore single-row). The story dict already carries
        # niche_id from callers (intake fetchers), so just forward it.
        record = self._sp_call(
            self._backend("Stories").create,
            "Stories",
            fields,
            niche_id=story.get("niche_id"),
        )
        return record["id"]

    # ── Read ───────────────────────────────────────────────────────

    def find_story_by_story_id(
        self,
        story_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        formula = f"{{story_id}}='{_esc(story_id)}'"
        records = self._sp_call(
            self._backend("Stories").find,
            "Stories",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    # ── Update ─────────────────────────────────────────────────────

    def update_story_status(
        self,
        story_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        **kwargs,
    ) -> None:
        """Set a story's status (and any other ``**kwargs`` fields).

        Raises ``ValueError`` if no row matches ``story_id`` — the
        historical behaviour callers rely on.
        """
        story = self.find_story_by_story_id(story_id, niche_id=niche_id)
        if not story:
            raise ValueError(f"Story {story_id} not found")
        # PR #534 (2026-06-24, SR-A migration): pass niche_id to
        # backend.update so SET LOCAL fires. find already runs
        # tenant-scoped (line above); the update was previously
        # admin-mode. Same pattern as BlueprintStore PR #533.
        self._sp_call(
            self._backend("Stories").update,
            "Stories",
            story["id"],
            {"status": status, **kwargs},
            niche_id=niche_id,
        )

    # ── Batch ──────────────────────────────────────────────────────

    def batch_create_stories(self, stories: list[dict]) -> list[str]:
        """Bulk-insert stories via ``backend.batch_create``.

        Faster than a loop of ``create_story`` for >5 rows (pipeline
        mode under PostgreSQL, batch HTTP under SharePoint). Returns
        the new record ids in input order.

        Note: the bulk path omits ``why_it_matters`` from the field
        set — this matches the historical behaviour
        (``BacklogClient.batch_create_stories`` never included it).

        PR #528 (2026-06-24, SR-C bulk path): mirrors PR #527's
        BlueprintStore bulk migration exactly — inject niche_id into
        each record + dispatch on unique-niche set. See PR #527 for
        the rationale and trade-offs on heterogeneous batches.
        """
        records = []
        for story in stories:
            scores = story.get("scores", {})
            record = {
                "story_id": story["story_id"],
                "title": story["title"],
                "url": story["url"],
                "source": self._resolve_source(story),
                "published_at": story.get("published_at"),
                "summary": story.get("summary", ""),
                "priority": story.get("priority", scores.get("priority", 0.5)),
                "status": "INTAKE",
                "themes": story.get("themes", []),
                "authority_score": scores.get("authority", 0.0),
                "recency_score": scores.get("recency", 0.0),
                "novelty_score": scores.get("novelty", 0.0),
            }
            # PR #528: inject niche_id into each record (closes the
            # bulk-path SR-C data gap — same shape as PR #527).
            if story.get("niche_id"):
                record["niche_id"] = story["niche_id"]
            records.append(record)

        # PR #528: unique-niche dispatch — pass kwarg only when all
        # records agree, to avoid backend's heterogeneous-tenant
        # ValueError. Per-record fields still land in the >1 case
        # so the row gets its tenant tag for read-time RLS.
        niche_ids = {r.get("niche_id") for r in records if r.get("niche_id")}
        batch_niche_id: str | None
        if len(niche_ids) == 1:
            batch_niche_id = next(iter(niche_ids))
        elif len(niche_ids) > 1:
            logger.warning(
                "[story_store] heterogeneous-niche batch (%d distinct: %s) — "
                "passing niche_id=None to backend.batch_create. Per-record "
                "niche_id fields still land. See PR #528.",
                len(niche_ids),
                sorted(niche_ids),
            )
            batch_niche_id = None
        else:
            batch_niche_id = None

        created = self._backend("Stories").batch_create(
            "Stories",
            records,
            niche_id=batch_niche_id,
        )
        return [r["id"] for r in created]
