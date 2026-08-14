"""Sponsorship pipeline API (Phase 3.C session 1, 2026-08-14).

Read-only endpoint listing DRAFTED / APPROVED / SENT rows in the
``sponsorship_pipeline`` table populated by
``scripts/generate_sponsorship_outreach.py``.

Session 1 is READ-ONLY intentionally. The write endpoints (approve,
reject, mark-sent) ship in session 2 alongside the sending
transport wire. Reason: shipping approve+send in the same session
tempts the operator to click "approve" on 25 drafts before the
inbox transport is battle-tested against a single row. Read-first
ship pattern here matches the intelligence-stack cards
(observation before consumer wire).

## Endpoints

  * ``GET /api/v1/sponsorship/pipeline`` — list drafts, filterable
    by niche_id + status.

## Fail-open contract

Cold-start returns ``{"data": null}`` rather than 500ing so the
frontend renders 'No pipeline rows yet' copy without special-casing.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "sponsorship_pipeline_api",
    __name__,
    url_prefix="/api/v1/sponsorship/pipeline",
)

_VALID_STATUSES = frozenset({
    "DRAFTED", "APPROVED", "SENT", "RESPONDED", "DEAL",
    "REJECTED", "STALE",
})


@bp.route("", methods=["GET"])
def list_pipeline():
    """List pipeline rows.

    Query params:
      * ``niche_id`` (optional) — filter to one niche
      * ``status`` (optional) — filter to one status (whitelisted)
      * ``limit`` (optional, default 50, cap 200)

    Response:
        {
          "status": "success",
          "data": {
            "sending_enabled": false,
            "rows": [
              {
                "id": "...",
                "niche_id": "gaming",
                "brand_name": "...",
                "brand_email": "...",
                "tier_at_generation": "eligible_now",
                "subject": "...",
                "body": "...",
                "kit_url": "...",
                "status": "DRAFTED",
                "drafted_at": "..."
              }
            ]
          }
        }
    """
    niche_filter = request.args.get("niche_id")
    status_filter = request.args.get("status")
    if status_filter and status_filter not in _VALID_STATUSES:
        return jsonify({"status": "error", "reason": "invalid status"}), 400
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "success", "data": None})

    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            params: list = []
            where_clauses = []
            if niche_filter:
                where_clauses.append("sp.niche_id = %s")
                params.append(niche_filter)
            if status_filter:
                where_clauses.append("sp.status = %s")
                params.append(status_filter)
            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT sp.id, sp.niche_id, sp.tier_at_generation,
                       sp.subject, sp.body, sp.kit_url, sp.status,
                       sp.drafted_at, sp.approved_at, sp.sent_at,
                       sp.responded_at, sp.deal_closed_at,
                       sbt.brand_name, sbt.brand_email,
                       sbt.contact_first_name
                FROM sponsorship_pipeline sp
                JOIN sponsorship_brand_targets sbt ON sbt.id = sp.target_id
                {where_sql}
                ORDER BY sp.drafted_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
    except Exception as exc:
        logger.warning("[sponsorship_pipeline] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    if not rows:
        return jsonify({"status": "success", "data": None})

    def _iso(v):
        try:
            return v.isoformat() if v is not None else None
        except AttributeError:
            return v

    # sending_enabled = the session-2 auto-send flag. False in session 1;
    # the endpoint returns it so the frontend can render a badge
    # ("send-wire not live") on the pipeline UI.
    sending_enabled = os.environ.get(
        "GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}

    return jsonify(
        {
            "status": "success",
            "data": {
                "sending_enabled": sending_enabled,
                "rows": [
                    {
                        "id": str(r["id"]),
                        "niche_id": r["niche_id"],
                        "brand_name": r["brand_name"],
                        "brand_email": r["brand_email"],
                        "contact_first_name": r["contact_first_name"],
                        "tier_at_generation": r["tier_at_generation"],
                        "subject": r["subject"],
                        "body": r["body"],
                        "kit_url": r["kit_url"],
                        "status": r["status"],
                        "drafted_at": _iso(r["drafted_at"]),
                        "approved_at": _iso(r["approved_at"]),
                        "sent_at": _iso(r["sent_at"]),
                        "responded_at": _iso(r["responded_at"]),
                        "deal_closed_at": _iso(r["deal_closed_at"]),
                    }
                    for r in rows
                ],
            },
        }
    )
