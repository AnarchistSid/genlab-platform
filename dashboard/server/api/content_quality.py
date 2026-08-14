"""Content quality scores API (Phase 4.A session 4, 2026-08-14).

Read-only endpoint that surfaces per-niche aggregates from the
``content_quality_scores`` table populated by the 30-min
``genlab-quality-scorer.timer``.

Observability-first ship: consumer wire
(``GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED``) stays off until the
operator eyeballs the score distribution for ≥1 week via the
card this endpoint feeds. Matches the intelligence-stack pattern.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "content_quality_api",
    __name__,
    url_prefix="/api/v1/content-quality",
)


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Return per-niche score aggregates + flag state.

    Response:
        {
          "status": "success",
          "data": {
            "flag_enabled": false,
            "per_niche": [
              {
                "niche_id": "gaming",
                "n_scored": 12,
                "avg_joint": 0.42,
                "avg_visual": 0.38,
                "avg_audio": 0.51,
                "min_joint": 0.12,
                "max_joint": 0.78,
                "last_scored_at": "..."
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
                       COUNT(*)::int AS n_scored,
                       AVG(joint_score)::float AS avg_joint,
                       AVG(visual_score)::float AS avg_visual,
                       AVG(audio_score)::float AS avg_audio,
                       AVG(aesthetic_score)::float AS avg_aesthetic,
                       COUNT(aesthetic_score)::int AS n_aesthetic,
                       MIN(joint_score)::float AS min_joint,
                       MAX(joint_score)::float AS max_joint,
                       MAX(computed_at) AS last_scored_at
                FROM content_quality_scores
                WHERE computed_at >= NOW() - INTERVAL '7 days'
                GROUP BY niche_id
                ORDER BY niche_id
                """
            ).fetchall()
    except Exception as exc:
        logger.warning("[content_quality] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    if not rows:
        return jsonify({"status": "success", "data": None})

    def _iso(v):
        try:
            return v.isoformat() if v is not None else None
        except AttributeError:
            return v

    flag_enabled = os.environ.get(
        "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}

    return jsonify(
        {
            "status": "success",
            "data": {
                "flag_enabled": flag_enabled,
                "per_niche": [
                    {
                        "niche_id": r["niche_id"],
                        "n_scored": r["n_scored"],
                        "avg_joint": r["avg_joint"],
                        "avg_visual": r["avg_visual"],
                        "avg_audio": r["avg_audio"],
                        "avg_aesthetic": r["avg_aesthetic"],
                        "n_aesthetic": r["n_aesthetic"],
                        "min_joint": r["min_joint"],
                        "max_joint": r["max_joint"],
                        "last_scored_at": _iso(r["last_scored_at"]),
                    }
                    for r in rows
                ],
            },
        }
    )
