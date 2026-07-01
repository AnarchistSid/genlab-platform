"""Strategist reports API — read-only surface for the dashboard.

Endpoints (all under `/api/v1/strategist`):

  GET /reports/latest?niche_id=X
      Latest Strategist report for one niche. Returns 404 if none.

  GET /reports/unreviewed?limit=20
      Reports the operator hasn't reviewed yet, newest first.
      Used by the dashboard banner to surface pending work.

  POST /reports/{report_id}/review
      Body: {"accepted_proposal_indices": [0, 2], "notes": "..."}
      Records operator's accept/reject decision. Idempotent — re-posting
      overwrites `reviewed_at`.

React UI (StrategistReportCard) lands in PR Strategist-2b. This module
is the backend contract that UI will consume.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint("strategist_api", __name__, url_prefix="/api/v1/strategist")

# Niche whitelist — server-side guard against `?niche_id=<anything>` probes.
# Mirrors the 5-active-niche list in strategist run_strategist.py.
_VALID_NICHES = frozenset(["ai_creators", "gaming", "sports", "movies", "anime"])


def _get_persister():
    """Lazy import so this module doesn't drag genlab_core deps into
    server startup. Tests can monkeypatch this factory to inject a mock."""
    import os

    import psycopg
    from genlab_core.intelligence.persister import PostgresPersister
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return None
    conn = psycopg.connect(dsn, row_factory=dict_row)
    return PostgresPersister(conn), conn


def _report_to_json(report) -> dict[str, Any]:
    """Serialize a StrategistReport to a JSON-safe dict.

    Handled here (not via model_dump) so we can add UI-friendly derived
    fields — `slot_time_ist` for the run_at, `days_since_review`, etc. —
    without polluting the persistence schema.
    """
    return {
        "id": str(report.id),
        "niche_id": report.niche_id,
        "week_of": report.week_of.isoformat(),
        "run_at": report.run_at.isoformat(),
        "detected_phase": report.detected_phase.value,
        "phase_evidence": report.phase_evidence,
        "weekly_summary": report.weekly_summary,
        "proposals": [p.model_dump(mode="json") for p in report.proposals],
        "causal_hypotheses": [h.model_dump(mode="json") for h in report.causal_hypotheses],
        "universal_playbook_proposals": [
            p.model_dump(mode="json") for p in report.universal_playbook_proposals
        ],
    }


@bp.route("/reports/latest", methods=["GET"])
def get_latest_report():
    niche_id = (request.args.get("niche_id") or "").strip()
    if niche_id not in _VALID_NICHES:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Invalid niche_id (allowed: {sorted(_VALID_NICHES)})",
                }
            ),
            400,
        )

    try:
        pair = _get_persister()
        if pair is None:
            return jsonify({"status": "error", "message": "Persister unavailable"}), 503
        persister, conn = pair
        try:
            report = persister.get_latest(niche_id)
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("strategist.get_latest_failed niche=%s err=%s", niche_id, exc)
        return jsonify({"status": "error", "message": "Query failed"}), 500

    if report is None:
        return (
            jsonify(
                {
                    "status": "success",
                    "data": None,
                    "message": f"No Strategist reports yet for {niche_id}",
                }
            ),
            200,
        )

    return jsonify({"status": "success", "data": _report_to_json(report)})


@bp.route("/reports/unreviewed", methods=["GET"])
def list_unreviewed():
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        return jsonify({"status": "error", "message": "limit must be integer"}), 400
    # Cap upper bound to prevent large-response abuse; lower bound at 1
    limit = max(1, min(limit, 100))

    try:
        pair = _get_persister()
        if pair is None:
            return jsonify({"status": "error", "message": "Persister unavailable"}), 503
        persister, conn = pair
        try:
            reports = persister.list_unreviewed(limit=limit)
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("strategist.list_unreviewed_failed err=%s", exc)
        return jsonify({"status": "error", "message": "Query failed"}), 500

    return jsonify(
        {
            "status": "success",
            "data": {
                "count": len(reports),
                "reports": [_report_to_json(r) for r in reports],
            },
        }
    )


@bp.route("/reports/<report_id>/review", methods=["POST"])
def record_review(report_id: str):
    """Record the operator's accept/reject decision on a report's proposals.

    Body shape:
      {
        "accepted_proposal_indices": [int, ...],
        "rejected_proposal_indices": [int, ...],
        "notes": "..."   // optional operator-facing note
      }

    Both lists optional; empty lists mean "no explicit decision on any
    proposal" (operator dismissed the whole report without acting).
    """
    body = request.get_json(silent=True) or {}
    accepted = body.get("accepted_proposal_indices", [])
    rejected = body.get("rejected_proposal_indices", [])
    notes = body.get("notes", "") or ""

    if not isinstance(accepted, list) or not isinstance(rejected, list):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "accepted_proposal_indices and rejected_proposal_indices must be lists",
                }
            ),
            400,
        )

    # Author from auth middleware; falls back to "unknown" so we still
    # record the review even when auth context is missing.
    reviewer = request.remote_user if hasattr(request, "remote_user") else "unknown"
    if not reviewer:
        reviewer = "unknown"

    now = datetime.now(UTC)
    try:
        import os

        import psycopg

        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            return jsonify({"status": "error", "message": "DB unavailable"}), 503
        conn = psycopg.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE strategist_reports
                    SET reviewed_at = %s,
                        reviewed_by = %s,
                        proposals_accepted = %s::jsonb,
                        proposals_rejected = %s::jsonb,
                        operator_notes = %s
                    WHERE id = %s::uuid
                    """,
                    (
                        now,
                        reviewer,
                        _json_dumps(accepted),
                        _json_dumps(rejected),
                        notes,
                        report_id,
                    ),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return (
                        jsonify({"status": "error", "message": f"Report {report_id} not found"}),
                        404,
                    )
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("strategist.record_review_failed id=%s err=%s", report_id, exc)
        return jsonify({"status": "error", "message": "Update failed"}), 500

    return jsonify(
        {
            "status": "success",
            "data": {
                "id": report_id,
                "reviewed_at": now.isoformat(),
                "reviewed_by": reviewer,
                "accepted_count": len(accepted),
                "rejected_count": len(rejected),
            },
        }
    )


def _json_dumps(v: Any) -> str:
    import json

    return json.dumps(v)
