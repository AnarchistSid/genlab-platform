"""Content ideation pool API (Phase 4.E session 3, 2026-08-14).

Read-only endpoint returning per-niche pool depth (pending /
consumed / expired) + total lifetime idea count. Feeds
IdeationPoolCard on Mission Control.

Same fail-open contract as sibling read-only endpoints — cold-
start returns {"data": null}.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "ideation_pool_api",
    __name__,
    url_prefix="/api/v1/ideation-pool",
)


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Per-niche pool depth + rollout flag state.

    Response:
        {
          "status": "success",
          "data": {
            "rollout_pct": 25,
            "flag_enabled": true,
            "per_niche": [
              {
                "niche_id": "gaming",
                "pending": 18,
                "consumed": 3,
                "expired": 0,
                "total": 21,
                "latest_batch_at": "..."
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
            rows = conn.execute(
                """
                SELECT niche_id,
                       COUNT(*) FILTER (WHERE status = 'pending')::int AS pending,
                       COUNT(*) FILTER (WHERE status = 'consumed')::int AS consumed,
                       COUNT(*) FILTER (WHERE status = 'expired')::int AS expired,
                       COUNT(*)::int AS total,
                       MAX(created_at) AS latest_batch_at
                FROM content_ideas_pool
                GROUP BY niche_id
                ORDER BY niche_id
                """
            ).fetchall()
    except Exception as exc:
        logger.warning("[ideation_pool] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    if not rows:
        return jsonify({"status": "success", "data": None})

    def _iso(v):
        try:
            return v.isoformat() if v is not None else None
        except AttributeError:
            return v

    flag = os.environ.get(
        "GENLAB_IDEATION_POOL_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}
    try:
        pct = int(os.environ.get("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "0"))
    except (TypeError, ValueError):
        pct = 0
    pct = max(0, min(100, pct))

    return jsonify(
        {
            "status": "success",
            "data": {
                "flag_enabled": flag,
                "rollout_pct": pct,
                "per_niche": [
                    {
                        "niche_id": r["niche_id"],
                        "pending": r["pending"],
                        "consumed": r["consumed"],
                        "expired": r["expired"],
                        "total": r["total"],
                        "latest_batch_at": _iso(r["latest_batch_at"]),
                    }
                    for r in rows
                ],
            },
        }
    )
