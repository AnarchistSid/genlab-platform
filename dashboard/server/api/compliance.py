"""Compliance API endpoints (PR #578, 2026-06-25).

Surfaces the data Phase A (PRs #566-#570) accumulates in
compliance_events:

  GET /api/v1/compliance/events
      Recent compliance_events rows. Filter by niche_id, event_type,
      decision, limit. SR-F: restricted operators see only allowed
      niches' rows (same filter pattern as /api/v1/audience/history).

  GET /api/v1/compliance/checks
      What checks are currently registered in the policy_gate (i.e.
      what the system would call on the next publish). Lets the
      dashboard render a "Phase A active checks" pill.

Per-source-performance lives on the existing learning.py blueprint
(/api/v1/learning/source-performance) — that file already owns the
URL prefix.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("compliance_api", __name__, url_prefix="/api/v1/compliance")


# Closed enums mirrored from genlab_core.compliance.events. Repeated
# here to validate input WITHOUT importing the core module on every
# request (cheap defensive guard; the actual write-time validation
# happens at log_compliance_event in core).
_VALID_EVENT_TYPES = {
    "pre_publish_check",
    "ai_disclosure_added",
    "copyright_flag",
    "spam_pattern_detected",
    "account_health_warning",
    "auto_publish_block",
    "manual_override",
    "unknown",
}
_VALID_DECISIONS = {"allow", "warn", "block"}


@bp.route("/events", methods=["GET"])
def list_compliance_events():
    """List recent compliance_events rows.

    Query params:
      niche_id   — optional niche filter (default: all)
      event_type — optional event type filter
      decision   — optional decision filter ('allow'|'warn'|'block')
      limit      — max rows (default 50, capped at 500)

    SR-F: per-user allowlist filter applied (PR #551+ pattern).
    Restricted operator sees only their niches' rows; admin sees all.

    Response shape:
      {data: [
        {id, niche_id, blueprint_id, platform, event_type,
         decision, reasons, metadata, created_at}, ...
      ]}
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)

    niche_id = (request.args.get("niche_id") or "").strip()
    event_type = (request.args.get("event_type") or "").strip()
    decision = (request.args.get("decision") or "").strip()
    try:
        limit = min(int(request.args.get("limit", 50)), 500)
    except (ValueError, TypeError):
        return api_error(error="Invalid limit parameter")

    # SR-F: validate explicit niche_id against operator allowlist
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id and niche_id != "all" and niche_id not in _allowed:
        return api_error(
            error=(
                f"Compliance events scoped to niche '{niche_id}' which is "
                f"not in your allowlist (allowed: {sorted(_allowed)}). "
                f"See SR-F (PR #578)."
            ),
            code=403,
        )

    # Validate enum filters server-side (cheap defensive guard)
    if event_type and event_type not in _VALID_EVENT_TYPES:
        return api_error(error=f"Invalid event_type. Valid: {sorted(_VALID_EVENT_TYPES)}")
    if decision and decision not in _VALID_DECISIONS:
        return api_error(error=f"Invalid decision. Valid: {sorted(_VALID_DECISIONS)}")

    try:
        from psycopg.rows import dict_row

        # Build WHERE clause dynamically; all values parameterised
        where_parts = []
        params: list = []
        if niche_id and niche_id != "all":
            where_parts.append("niche_id = %s")
            params.append(niche_id)
        if event_type:
            where_parts.append("event_type = %s")
            params.append(event_type)
        if decision:
            where_parts.append("decision = %s")
            params.append(decision)
        where_clause = " WHERE " + " AND ".join(where_parts) if where_parts else ""
        params.append(limit)

        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            conn.execute("SELECT set_config('app.niche_id', 'all', true)")
            rows = conn.execute(
                f"""
                SELECT id, niche_id, blueprint_id, platform, event_type,
                       decision, reasons, metadata, created_at
                FROM compliance_events
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()

        # SR-F post-filter when no explicit niche_id was provided
        if _allowed is not None and not niche_id:
            rows = [r for r in rows if (r.get("niche_id") or "") in _allowed]

        # Normalise for JSON: UUID + datetime → str
        data = [
            {
                "id": str(r["id"]),
                "niche_id": r["niche_id"],
                "blueprint_id": str(r["blueprint_id"]) if r["blueprint_id"] else None,
                "platform": r["platform"],
                "event_type": r["event_type"],
                "decision": r["decision"],
                "reasons": r["reasons"] or [],
                "metadata": r["metadata"] or {},
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
        return api_success(data=data)
    except Exception as exc:
        logger.error("Compliance events list error: %s", exc, exc_info=True)
        return api_error(error="Internal server error", code=500)


@bp.route("/checks", methods=["GET"])
def list_registered_checks():
    """Names of currently registered policy_gate checks.

    Returns the list of check names + count + whether the gate is
    reachable (compliance package importable). When the package isn't
    available (degraded environment, missing dep), returns count=0 +
    reachable=false rather than crashing — lets the dashboard pill
    render a 'compliance gate unavailable' state.
    """
    try:
        from genlab_core.compliance.policy_gate import registered_check_names

        names = registered_check_names()
        return api_success(
            data={
                "checks": names,
                "count": len(names),
                "reachable": True,
            }
        )
    except ImportError:
        return api_success(
            data={
                "checks": [],
                "count": 0,
                "reachable": False,
                "reason": "compliance package not importable",
            }
        )
    except Exception as exc:
        logger.error("List registered checks failed: %s", exc, exc_info=True)
        return api_error(error="Internal server error", code=500)
