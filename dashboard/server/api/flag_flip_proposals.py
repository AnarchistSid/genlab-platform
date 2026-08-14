"""Flag-flip proposals API (Phase 5.C session 2, 2026-08-14).

Read-only queue of pending / recently-applied / recently-rejected
proposals. Feeds an operator surface so they can eyeball the
autonomous flag-flip pipeline + intervene inside the 24h override
window if needed.

Cold-start returns {"data": null}.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "flag_flip_proposals_api",
    __name__,
    url_prefix="/api/v1/flag-flip-proposals",
)


@bp.route("/pending", methods=["GET"])
def list_pending():
    """All pending proposals with age + hours-until-auto-apply
    calculation. Response:

        {
          "status": "success",
          "data": {
            "override_window_hours": 24,
            "confidence_threshold": 0.9,
            "rows": [
              {
                "id": "...", "flag_name": "GENLAB_...", "from_state": "25",
                "to_state": "50", "confidence": 0.85, "rationale": "...",
                "age_hours": 6.2, "hours_until_auto_apply": 17.8,
                "auto_apply_eligible": false
              }
            ]
          }
        }
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "success", "data": None})

    window_hours = 24
    conf_threshold = 0.9

    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT id::text AS id, flag_name, from_state, to_state,
                       rationale, confidence,
                       EXTRACT(EPOCH FROM (NOW() - proposed_at))::float / 3600 AS age_hours,
                       proposed_at, evidence
                FROM flag_flip_proposals
                WHERE status = 'pending'
                ORDER BY proposed_at ASC
                """
            ).fetchall()
    except Exception as exc:
        logger.warning("[flag_flip_proposals] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    def _iso(v):
        try:
            return v.isoformat() if v is not None else None
        except AttributeError:
            return v

    out_rows = []
    for r in rows:
        age = float(r["age_hours"] or 0)
        conf = float(r["confidence"] or 0)
        hours_until = max(0.0, window_hours - age)
        eligible = age >= window_hours and conf >= conf_threshold
        out_rows.append({
            "id": r["id"],
            "flag_name": r["flag_name"],
            "from_state": r["from_state"],
            "to_state": r["to_state"],
            "rationale": r["rationale"],
            "confidence": conf,
            "age_hours": round(age, 1),
            "hours_until_auto_apply": round(hours_until, 1),
            "auto_apply_eligible": eligible,
            "proposed_at": _iso(r["proposed_at"]),
            "evidence": r["evidence"],
        })

    return jsonify({
        "status": "success",
        "data": {
            "override_window_hours": window_hours,
            "confidence_threshold": conf_threshold,
            "rows": out_rows,
        },
    })


@bp.route("/<proposal_id>/reject", methods=["POST"])
def reject_proposal(proposal_id: str):
    """Operator explicitly rejects a pending proposal. Flips
    status to rejected + records rejected_at. Auto-apply pass
    will skip rejected rows on next fire."""
    from flask import request
    reason = (request.json or {}).get("reason", "operator_override") \
        if request.is_json else "operator_override"
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "error", "reason": "DATABASE_URL unset"}), 500
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            result = conn.execute(
                """
                UPDATE flag_flip_proposals
                SET status = 'rejected',
                    rejected_at = NOW(),
                    rejection_reason = %s
                WHERE id = %s AND status = 'pending'
                """,
                (reason, proposal_id),
            )
            conn.commit()
            if result.rowcount == 0:
                return jsonify({
                    "status": "error", "reason": "not found or not pending",
                }), 404
    except Exception as exc:
        logger.warning("[flag_flip_proposals] reject failed: %s", exc)
        return jsonify({"status": "error", "reason": str(exc)}), 500
    return jsonify({"status": "success", "data": {"status": "rejected"}})
