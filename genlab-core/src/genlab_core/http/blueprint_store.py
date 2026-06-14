"""Blueprints persistence, extracted from BacklogClient.

Six methods live in this module:

* :meth:`BlueprintStore.create_blueprint` — record a single
  blueprint, resolving story + template links via injected finders.
  Graceful retry without optional/new columns if the backend
  rejects unknown fields.
* :meth:`BlueprintStore.find_blueprint_by_candidate_id` — single-row
  lookup with optional ``niche_id`` filter.
* :meth:`BlueprintStore.update_blueprint_status` — find + update
  with the scheduled-post protection gate.
* :meth:`BlueprintStore.get_blueprints_safe_to_cleanup` — list rows
  in a given status that have no ``scheduled_for`` and a low enough
  priority to safely demote/delete.
* :meth:`BlueprintStore.get_blueprints_by_status` — list rows by
  status (optional ``niche_id``).
* :meth:`BlueprintStore.batch_create_blueprints` — bulk insert
  using two-pass caching for story + template lookups, then a
  single ``backend.batch_create`` call.

Tier 2 / audit S-2 — extracts the largest remaining surface in
``BacklogClient``. Constructor takes five callables — the
most-callable-rich store in the family — because the Blueprints
surface sits at the intersection of Stories (link resolution),
Templates (link resolution), and the schedule-guard protection
helper:

* ``sp_call``: circuit-breaker wrap helper.
* ``backend``: live backend lookup.
* ``find_story``: ``StoryStore.find_story_by_story_id``.
* ``find_template``: ``TemplateStore.find_template_by_template_id``.
* ``assert_not_scheduled``: ``BacklogClient.assert_not_scheduled`` —
  the scheduled-post protection invariant. Kept centralised
  because it depends on ``_is_demotion`` + ``_STATUS_ORDER``.

BacklogClient retains all 6 method names + signatures as thin
delegators so the public API stays byte-stable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


class BlueprintStore:
    """CRUD over the Blueprints table."""

    def __init__(
        self,
        sp_call: Callable[..., Any],
        backend: Callable[[str], Any],
        find_story: Callable[..., dict | None],
        find_template: Callable[..., dict | None],
        assert_not_scheduled: Callable[[dict, str], None],
    ) -> None:
        self._sp_call = sp_call
        self._backend = backend
        self._find_story = find_story
        self._find_template = find_template
        self._assert_not_scheduled = assert_not_scheduled

    # ── Create ─────────────────────────────────────────────────────

    def create_blueprint(
        self,
        blueprint: dict,
        story_record: dict | None = None,
        template_record: dict | None = None,
    ) -> str:
        """Record a single blueprint with story + template links.

        ``story_record`` / ``template_record`` short-circuit the
        finder calls (use them in ``batch_create_blueprints`` to
        avoid N+1 lookups). Raises ``ValueError`` if the story
        doesn't exist.

        Graceful retry path: if the backend rejects unknown columns
        (older SharePoint schemas, Postgres migrations not yet
        applied), strips the optional/new fields and re-attempts.
        """
        story = story_record or self._find_story(blueprint["story_id"])
        if not story:
            raise ValueError(f"Story {blueprint['story_id']} not found")

        template_record_id = None
        if blueprint.get("template_id"):
            template = template_record or self._find_template(blueprint["template_id"])
            if template:
                template_record_id = template["id"]

        validation = blueprint.get("validation_status", {})

        fields = {
            "candidate_id": blueprint["candidate_id"],
            "story": [story["id"]],
            "template": [template_record_id] if template_record_id else [],
            "template_id": blueprint.get("template_id", ""),
            "template_name": blueprint.get("template_name", ""),
            "topic": blueprint.get("topic", ""),
            "angle": blueprint.get("angle", ""),
            "format": blueprint.get("format"),
            "hook": blueprint.get("hook", ""),
            "structure": blueprint.get("structure_text", "")
            or "\n".join(blueprint.get("structure", [])),
            "cta": blueprint.get("cta", ""),
            "priority_score": blueprint.get("priority_score", 0.5),
            "status": "INTEL_READY",
            "why_this_will_work": blueprint.get("why_this_will_work", ""),
            "validation_constraints_passed": validation.get("constraints_passed", False),
            "validation_claims_passed": validation.get("claims_passed", False),
            "validation_risk_acceptable": validation.get("risk_acceptable", False),
        }

        if blueprint.get("niche_id"):
            fields["niche_id"] = blueprint["niche_id"]

        # clip_url: persisted by push_to_backlog so renderer can
        # download the clip later.
        if blueprint.get("clip_url"):
            fields["clip_url"] = blueprint["clip_url"]

        # Optional performance-ML fields (caller enriches blueprint
        # dict before passing it — no cross-project import needed).
        for key in ("topic_category", "hook_formula", "published_hour", "published_day"):
            val = blueprint.get(key)
            if val:
                fields[key] = val

        try:
            record = self._backend("Blueprints").create(
                "Blueprints",
                fields,
                typecast=True,
            )
        except Exception as e:
            err_str = str(e)
            if (
                "UNKNOWN_FIELD_NAME" in err_str
                or "columnNotFound" in err_str
                or "not recognized" in err_str
            ):
                for f in (
                    "template_id",
                    "template_name",
                    "topic_category",
                    "hook_formula",
                    "published_hour",
                    "published_day",
                    "clip_url",
                ):
                    fields.pop(f, None)
                record = self._backend("Blueprints").create("Blueprints", fields)
            else:
                raise
        return record["id"]

    # ── Read ───────────────────────────────────────────────────────

    def find_blueprint_by_candidate_id(
        self,
        candidate_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        formula = f"{{candidate_id}}='{_esc(candidate_id)}'"
        records = self._sp_call(
            self._backend("Blueprints").find,
            "Blueprints",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def get_blueprints_by_status(
        self,
        status: str,
        *,
        niche_id: str | None = None,
        max_records: int | None = None,
    ) -> list[dict]:
        formula = f"{{status}}='{_esc(status)}'"
        return self._sp_call(
            self._backend("Blueprints").find,
            "Blueprints",
            formula=formula,
            niche_id=niche_id,
            max_records=max_records,
        )

    def get_blueprints_safe_to_cleanup(
        self,
        status: str,
        *,
        niche_id: str | None = None,
        max_priority: float = 1.0,
    ) -> list[dict]:
        """Return blueprints that can be safely demoted/deleted.

        Filters out anything with ``scheduled_for`` set or with
        ``priority_score > max_priority``. Matches the historical
        BacklogClient behaviour exactly.
        """
        all_bps = self.get_blueprints_by_status(status, niche_id=niche_id)
        safe = []
        for bp in all_bps:
            f = bp.get("fields", bp)
            if f.get("scheduled_for"):
                continue
            score = float(f.get("priority_score", 0) or 0)
            if score <= max_priority:
                safe.append(bp)
        return safe

    # ── Update ─────────────────────────────────────────────────────

    def update_blueprint_status(
        self,
        candidate_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        force: bool = False,
        **kwargs,
    ) -> None:
        """Update a blueprint's status (+ optional fields).

        Calls ``assert_not_scheduled`` first (unless ``force=True``)
        — this is the scheduled-post protection invariant. Raises
        ``ValueError`` if the blueprint doesn't exist OR if the
        protection gate rejects the demotion.
        """
        blueprint = self.find_blueprint_by_candidate_id(candidate_id, niche_id=niche_id)
        if not blueprint:
            raise ValueError(f"Blueprint {candidate_id} not found")
        if not force:
            self._assert_not_scheduled(blueprint, status)
        self._backend("Blueprints").update(
            "Blueprints",
            blueprint["id"],
            {"status": status, **kwargs},
            typecast=True,
        )

    # ── Batch ──────────────────────────────────────────────────────

    def batch_create_blueprints(self, blueprints: list[dict]) -> list[str]:
        """Bulk-insert blueprints with cached story + template lookups.

        Two-pass implementation:
          1. Collect all unique story_ids + template_ids, look each
             up exactly once. Caches stay scoped to this call.
          2. Build the field dicts using the cache, then make a
             single ``backend.batch_create`` call.

        Blueprints whose story can't be found are logged + skipped
        — matches the historical behaviour (callers tolerate
        partial success).

        Note: the bulk path omits the same niche_id/clip_url/etc.
        optional fields that single-row create includes. This is
        the historical behaviour (single create has had those
        fields added over time; bulk wasn't kept in sync).
        """
        story_cache: dict[str, dict | None] = {}
        template_cache: dict[str, dict | None] = {}
        for bp in blueprints:
            sid = bp["story_id"]
            if sid not in story_cache:
                story_cache[sid] = self._find_story(sid)
            tid = bp.get("template_id")
            if tid and tid not in template_cache:
                template_cache[tid] = self._find_template(tid)

        records = []
        for bp in blueprints:
            story = story_cache.get(bp["story_id"])
            if not story:
                logger.warning("Story %s not found, skipping blueprint", bp["story_id"])
                continue

            template_record_id = None
            if bp.get("template_id"):
                template = template_cache.get(bp["template_id"])
                if template:
                    template_record_id = template["id"]

            records.append(
                {
                    "candidate_id": bp["candidate_id"],
                    "story": [story["id"]],
                    "template": [template_record_id] if template_record_id else [],
                    "topic": bp.get("topic", ""),
                    "angle": bp.get("angle", ""),
                    "format": bp.get("format"),
                    "hook": bp.get("hook", ""),
                    "structure": "\n".join(bp.get("structure", [])),
                    "cta": bp.get("cta", ""),
                    "priority_score": bp.get("priority_score", 0.5),
                    "status": "INTEL_READY",
                    "why_this_will_work": bp.get("why_this_will_work", ""),
                }
            )

        created = self._backend("Blueprints").batch_create("Blueprints", records)
        return [r["id"] for r in created]
