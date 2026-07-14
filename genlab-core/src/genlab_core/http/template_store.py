"""Templates persistence, extracted from BacklogClient.

Three methods live in this module:

* :meth:`TemplateStore.create_template` — record a new content
  template (with the verbose constraints dict flattening).
* :meth:`TemplateStore.find_template_by_template_id` — single-row
  lookup by canonical ``template_id``.
* :meth:`TemplateStore.get_active_templates` — list rows where
  ``status='active'`` (optional ``niche_id`` filter).

Tier 2 / audit S-2, slice 2.4 (templates half) — fourth focused
extraction from the BacklogClient god class. BacklogClient retains
all 3 method names + signatures as thin delegators so the public API
stays byte-stable.

Constructor shape is the simplest in the family: just ``backend``.
Unlike AB_Tests/PendingEngagement, the Templates list is always
present in ``lists_config.yaml`` — none of the original methods
guarded on ``if not self.templates``, so there's no truthy gate to
mirror.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


class TemplateStore:
    """CRUD over the Templates table."""

    def __init__(self, backend: Callable[[str], Any]) -> None:
        # Live backend lookup. Bound to ``BacklogClient._backend`` so
        # future backend swaps (e.g. SP → Postgres) flow through here
        # without needing to rebuild the store.
        self._backend = backend

    # ── Create ─────────────────────────────────────────────────────

    def create_template(self, template: dict) -> str:
        """Record a new content template.

        Flattens the nested ``constraints`` dict into top-level
        promoted columns. Returns the new record id.

        PR #529 (2026-06-24, SR-C tenant binding, 3rd store): pass
        ``niche_id`` from the template dict to backend.create so the
        row carries its tenant tag AND the backend does the
        ``SET LOCAL app.niche_id`` step. Mirrors PR #528 (StoryStore
        single-row). The ``templates`` table on prod has a
        ``niche_id`` column (verified 2026-06-24 schema probe),
        so this is a real tenant-scoped table.
        """
        constraints = template.get("constraints", {})
        fields = {
            "template_id": template["template_id"],
            "name": template["name"],
            "format": template["format"],
            "best_for": template.get("best_for", []),
            "max_slides": constraints.get("max_slides"),
            "max_words_per_slide_title": constraints.get("max_words_per_slide_title"),
            "max_words_per_slide_body": constraints.get("max_words_per_slide_body"),
            "max_reel_seconds": constraints.get("max_reel_seconds"),
            "structure": "\n".join(template.get("structure", [])),
            "default_cta": template.get("default_cta", ""),
            "pattern_refs": ", ".join(template.get("pattern_refs", [])),
            "status": "active",
        }
        # PR #529: surface niche_id as a promoted field so the row
        # carries its tenant tag (was previously omitted — exactly
        # the SR-C bug class for the bulk path PR #527 fixed on
        # blueprints).
        if template.get("niche_id"):
            fields["niche_id"] = template["niche_id"]
        record = self._backend("Templates").create(
            "Templates",
            fields,
            typecast=True,
            niche_id=template.get("niche_id"),
        )
        # 2026-07-14 (backlog audit F1): shared helper.
        from genlab_core.storage.protocol import id_from_create_result

        return id_from_create_result(record)

    # ── Read ───────────────────────────────────────────────────────

    def find_template_by_template_id(self, template_id: str) -> dict | None:
        records = self._backend("Templates").find(
            "Templates",
            formula=f"{{template_id}}='{_esc(template_id)}'",
            max_records=1,
        )
        return records[0] if records else None

    def get_active_templates(self, *, niche_id: str | None = None) -> list[dict]:
        return self._backend("Templates").find(
            "Templates",
            formula="{status}='active'",
            niche_id=niche_id,
        )
