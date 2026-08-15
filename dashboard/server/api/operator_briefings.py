"""Operator daily briefing API (Phase 5.D, 2026-08-15).

Read-only surface for the most-recent briefing row + count of
pending items. Cold-start returns ``{"data": null}``.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "operator_briefings_api",
    __name__,
    url_prefix="/api/v1/operator-briefings",
)


def _iso(v):
    try:
        return v.isoformat() if v is not None else None
    except AttributeError:
        return v


@bp.route("/latest", methods=["GET"])
def latest():
    """Most recent briefing row. Response:

        {
          "status": "success",
          "data": {
            "id": "...",
            "generated_at": "2026-08-15T06:00:12+00:00",
            "summary_md": "**...**\\n- line1\\n- line2\\n...",
            "email_sent": true,
            "email_recipient": "operator@example.com",
            "email_error": null,
            "llm_cost_usd": 0.0032,
            "n_pending_flag_flips": 2,
            "n_pending_strategist_proposals": 3,
            "structured": { ... raw aggregate ... }
          }
        }
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "success", "data": None})

    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT id::text AS id, generated_at, summary_md,
                       structured, email_sent, email_recipient,
                       email_error, llm_cost_usd,
                       n_pending_flag_flips,
                       n_pending_strategist_proposals
                FROM operator_briefings
                ORDER BY generated_at DESC
                LIMIT 1
                """
            ).fetchone()
    except Exception as exc:
        logger.warning("[operator_briefings] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    if row is None:
        return jsonify({"status": "success", "data": None})

    return jsonify({
        "status": "success",
        "data": {
            "id": row["id"],
            "generated_at": _iso(row["generated_at"]),
            "summary_md": row["summary_md"],
            "structured": row["structured"],
            "email_sent": bool(row["email_sent"]),
            "email_recipient": row["email_recipient"],
            "email_error": row["email_error"],
            "llm_cost_usd": float(row["llm_cost_usd"] or 0.0),
            "n_pending_flag_flips": int(row["n_pending_flag_flips"] or 0),
            "n_pending_strategist_proposals": int(
                row["n_pending_strategist_proposals"] or 0,
            ),
        },
    })
