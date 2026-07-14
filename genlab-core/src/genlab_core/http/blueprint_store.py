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

        # PR #526 (2026-06-24, SR-C tenant binding): pass niche_id
        # through to the backend's SET LOCAL app.niche_id step. The
        # blueprint dict always carries niche_id from the caller
        # (push_to_backlog), so it's just a forwarding hop — no new
        # data needed. Wrapper-forward shipped in PR #525.
        bp_niche_id = blueprint.get("niche_id")
        try:
            record = self._backend("Blueprints").create(
                "Blueprints",
                fields,
                typecast=True,
                niche_id=bp_niche_id,
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
                # Retry preserves the same niche binding — the original
                # ValueError was about column shape, not tenant context.
                record = self._backend("Blueprints").create(
                    "Blueprints",
                    fields,
                    niche_id=bp_niche_id,
                )
            else:
                raise
        # 2026-07-14 (backlog audit F1): PostgresBackend.create returns
        # bare str, SharePoint returns dict — route through the shared
        # helper. Prior `record["id"]` blew up with TypeError on the
        # Postgres path (which is prod today).
        from genlab_core.storage.protocol import id_from_create_result

        return id_from_create_result(record)

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
        # 2026-07-14 (backlog audit F9): short-circuit on same-state
        # transition to avoid spurious UPDATE round-trips + invalidated
        # updated_at ordering on Mission Control. `PUBLISHED → PUBLISHED`
        # with no extra kwargs is a no-op — skip the UPDATE, log DEBUG
        # for traceability. Callers relying on the write side-effect
        # (very rare) can pass `force=True` (existing param) to bypass.
        current_status = (
            blueprint.get("fields", blueprint).get("status") if isinstance(blueprint, dict) else None
        )
        if not force and not kwargs and current_status == status:
            logger.debug(
                "[blueprint_store] update_blueprint_status same-state no-op: %s → %s (skipping UPDATE)",
                candidate_id,
                status,
            )
            return
        # PR #533 (2026-06-24, SR-A tenant binding): pass niche_id to
        # backend.update so SET LOCAL app.niche_id fires before the
        # UPDATE. Without this, a malicious caller (or buggy code)
        # with the blueprint id could mutate rows across tenants —
        # admin-mode UPDATE bypasses RLS USING clauses. niche_id
        # already comes in as a kwarg for the find step (line 253),
        # so this is a pure forwarding hop. Wrapper layer (#532)
        # forwards niche_id to backend.update.
        self._backend("Blueprints").update(
            "Blueprints",
            blueprint["id"],
            {"status": status, **kwargs},
            typecast=True,
            niche_id=niche_id,
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

        PR #527 (2026-06-24, SR-C bulk path): each record now carries
        ``niche_id`` (was previously omitted — exactly the SR-C bug
        class for bulk rows). Behaviour by unique niche set:

          * 1 unique niche → pass as ``niche_id=`` kwarg to the
            backend, getting full ``SET LOCAL app.niche_id`` binding
            on the INSERT pipeline.
          * 0 niches (legacy callers that don't carry niche_id) →
            pass ``None`` kwarg; backend admin-mode fallback (PR #517
            backward compat).
          * >1 niches (heterogeneous batch — unexpected; production
            pipelines run per-niche) → log a WARNING and pass ``None``
            kwarg. Per-record ``niche_id`` field still lands so the
            row gets its tenant tag for read-time RLS; only the
            ``SET LOCAL`` optimization is skipped to avoid the
            backend's heterogeneous-tenant ValueError.
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

            record = {
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
            # PR #527 (2026-06-24): inject niche_id into each record so
            # the row carries its tenant tag. Was previously omitted —
            # bulk rows landed without tenant binding (exactly the SR-C
            # bug class flagged by the audit, just on the bulk path).
            if bp.get("niche_id"):
                record["niche_id"] = bp["niche_id"]
            records.append(record)

        # PR #527: detect unique-niche state to decide kwarg shape.
        # PostgresBackend.batch_create() raises ValueError on
        # heterogeneous-tenant batches when niche_id kwarg is set, so
        # we only pass the kwarg when every record agrees.
        niche_ids = {r.get("niche_id") for r in records if r.get("niche_id")}
        batch_niche_id: str | None
        if len(niche_ids) == 1:
            batch_niche_id = next(iter(niche_ids))
        elif len(niche_ids) > 1:
            # Heterogeneous batch — unexpected; production pipelines run
            # per-niche. Log loudly so the operator notices a misconfig,
            # but don't crash: rows still get the per-record niche_id
            # field; only the SET LOCAL optimization is sacrificed.
            logger.warning(
                "[blueprint_store] heterogeneous-niche batch (%d distinct: %s) — "
                "passing niche_id=None to backend.batch_create to avoid "
                "tenant-mismatch ValueError. Each record still carries its "
                "own niche_id field. See PR #527 for the dispatch logic.",
                len(niche_ids),
                sorted(niche_ids),
            )
            batch_niche_id = None
        else:
            # 0 niches — legacy callers that don't carry niche_id at all.
            # Preserve backward compat (admin-mode INSERT).
            batch_niche_id = None

        created = self._backend("Blueprints").batch_create(
            "Blueprints",
            records,
            niche_id=batch_niche_id,
        )
        # 2026-07-14 (backlog audit F2): PostgresBackend.batch_create
        # returns list[str], SharePoint returns list[dict] — the
        # unconditional `r["id"]` list-comp blew up under Postgres.
        from genlab_core.storage.protocol import ids_from_batch_create_result

        return ids_from_batch_create_result(created)
