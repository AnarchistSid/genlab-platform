"""Autonomous reviewer status API (Phase 5.B session 2, 2026-08-14).

Read-only endpoint returning per-proposal-type autonomous-decision
stats: LLM base rate, augmented rate, escalation rate, average
augmentation delta. Feeds an operator surface so they can eyeball
whether the augmenter is helping.

Same fail-open pattern as sibling endpoints.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "autonomous_reviewer_api",
    __name__,
    url_prefix="/api/v1/autonomous-reviewer",
)


@bp.route("/status", methods=["GET"])
def get_status():
    """Per-proposal-type mature-history + latest meta-grade summary.

    Response:
        {
          "status": "success",
          "data": {
            "flag_enabled": false,
            "per_type": [
              {
                "proposal_type": "arm_add",
                "n_verdicts_8wk": 12,
                "n_improved": 7,
                "n_unchanged": 3,
                "n_regressed": 2,
                "success_rate": 0.78,
                "is_mature": true,
                "meta_grade": "B"
              }
            ]
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
            history_rows = conn.execute(
                """
                SELECT proposal_type,
                       COUNT(*)::int AS n,
                       COUNT(*) FILTER (WHERE verdict = 'improved')::int AS n_imp,
                       COUNT(*) FILTER (WHERE verdict = 'unchanged')::int AS n_unc,
                       COUNT(*) FILTER (WHERE verdict = 'regressed')::int AS n_reg
                FROM strategist_outcome_verification
                WHERE verdict != 'pending'
                  AND applied_at >= NOW() - INTERVAL '8 weeks'
                GROUP BY proposal_type
                ORDER BY proposal_type
                """
            ).fetchall()

            meta_row = conn.execute(
                """
                SELECT per_type_grades
                FROM meta_strategist_reports
                ORDER BY week_of DESC LIMIT 1
                """
            ).fetchone()
    except Exception as exc:
        logger.warning("[autonomous_reviewer] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    grades = {}
    if meta_row is not None:
        raw = meta_row.get("per_type_grades") if hasattr(meta_row, "get") else meta_row[0]
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        if isinstance(raw, dict):
            grades = raw

    flag = os.environ.get(
        "GENLAB_AUTONOMOUS_REVIEWER_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}

    if not history_rows:
        return jsonify({
            "status": "success",
            "data": {"flag_enabled": flag, "per_type": []},
        })

    per_type = []
    for r in history_rows:
        # dict_row is guaranteed by the connect(row_factory=dict_row)
        # above — no tuple fallback needed
        ptype = r["proposal_type"]
        n = int(r["n"] or 0)
        n_imp = int(r["n_imp"] or 0)
        n_unc = int(r["n_unc"] or 0)
        n_reg = int(r["n_reg"] or 0)
        denom = n_imp + n_reg
        success_rate = (n_imp / denom) if denom > 0 else 0.0
        per_type.append({
            "proposal_type": ptype,
            "n_verdicts_8wk": n,
            "n_improved": n_imp,
            "n_unchanged": n_unc,
            "n_regressed": n_reg,
            "success_rate": success_rate,
            "is_mature": n >= 3,
            "meta_grade": grades.get(ptype),
        })

    return jsonify({
        "status": "success",
        "data": {"flag_enabled": flag, "per_type": per_type},
    })
