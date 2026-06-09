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
        record = self._sp_call(
            self._backend("Stories").create,
            "Stories",
            fields,
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
        self._sp_call(
            self._backend("Stories").update,
            "Stories",
            story["id"],
            {"status": status, **kwargs},
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
        """
        records = []
        for story in stories:
            scores = story.get("scores", {})
            records.append(
                {
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
            )
        created = self._backend("Stories").batch_create("Stories", records)
        return [r["id"] for r in created]
