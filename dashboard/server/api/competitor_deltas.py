"""Competitor content deltas API (Phase 3.A observability, 2026-08-14).

Read-only endpoint that surfaces recent rows from the
``competitor_content_deltas`` table populated by the daily
``genlab-competitor-deltas.timer`` runner. The operator's
"validate before flip" surface for:

  * Deciding whether the delta data is trustworthy enough to feed
    into the strategist ``competitor_context`` state field
  * Eyeballing which competitor hooks are outperforming our typical
    output before we start systematically adapting them

Endpoint stays observation-only. Fail-open: cold-start returns
``{"data": null}`` rather than 500ing so the frontend renders
'No competitor deltas yet' copy without special-casing errors.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "competitor_deltas_api",
    __name__,
    url_prefix="/api/v1/competitor-deltas",
)


@bp.route("/latest", methods=["GET"])
def get_latest():
    """Return the top-N most recent competitor deltas per niche.

    Query params:
      * ``niche_id`` (optional) — filter to one niche
      * ``limit`` (optional, default 25, cap 100) — rows per niche
      * ``min_ratio`` (optional, default 1.5) — only return
        competitor videos that outperformed our median by ≥ this

    Response shape:
        {
          "status": "success",
          "data": {
            "generated_at": "<latest computed_at>",
            "flag_enabled": false,
            "rows": [
              {
                "niche_id": "gaming",
                "competitor_channel_label": "PewDiePie",
                "competitor_video_id": "abc123",
                "competitor_title": "Reacting to X",
                "competitor_view_count": 1200000,
                "our_reference_view_count": 15000,
                "delta_ratio": 80.0,
                "competitor_published_at": "..."
              }
            ]
          }
        }
    """
    niche_filter = request.args.get("niche_id")
    try:
        limit = max(1, min(100, int(request.args.get("limit", 25))))
    except (TypeError, ValueError):
        limit = 25
    try:
        min_ratio = float(request.args.get("min_ratio", 1.5))
    except (TypeError, ValueError):
        min_ratio = 1.5

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "success", "data": None})

    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            params: list = [min_ratio]
            where_niche = ""
            if niche_filter:
                where_niche = "AND niche_id = %s"
                params.append(niche_filter)
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT niche_id,
                       competitor_channel_id,
                       competitor_channel_label,
                       competitor_video_id,
                       competitor_title,
                       competitor_published_at,
                       competitor_view_count,
                       competitor_like_count,
                       competitor_comment_count,
                       our_reference_view_count,
                       delta_views,
                       delta_ratio,
                       computed_at
                FROM competitor_content_deltas
                WHERE delta_ratio >= %s
                  {where_niche}
                ORDER BY niche_id, delta_ratio DESC NULLS LAST
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
    except Exception as exc:
        logger.warning(
            "[competitor-deltas] query failed — returning empty: %s", exc,
        )
        return jsonify({"status": "success", "data": None})

    if not rows:
        return jsonify({"status": "success", "data": None})

    def _iso(v):
        try:
            return v.isoformat() if v is not None else None
        except AttributeError:
            return v

    generated_at = max(
        (r["computed_at"] for r in rows if r.get("computed_at")),
        default=None,
    )
    flag_enabled = os.environ.get(
        "GENLAB_COMPETITOR_CONTEXT_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}

    return jsonify(
        {
            "status": "success",
            "data": {
                "generated_at": _iso(generated_at),
                "flag_enabled": flag_enabled,
                "rows": [
                    {
                        "niche_id": r["niche_id"],
                        "competitor_channel_id": r["competitor_channel_id"],
                        "competitor_channel_label": r["competitor_channel_label"],
                        "competitor_video_id": r["competitor_video_id"],
                        "competitor_title": r["competitor_title"],
                        "competitor_published_at": _iso(r["competitor_published_at"]),
                        "competitor_view_count": r["competitor_view_count"],
                        "competitor_like_count": r["competitor_like_count"],
                        "competitor_comment_count": r["competitor_comment_count"],
                        "our_reference_view_count": r["our_reference_view_count"],
                        "delta_views": r["delta_views"],
                        "delta_ratio": r["delta_ratio"],
                        "computed_at": _iso(r["computed_at"]),
                    }
                    for r in rows
                ],
            },
        }
    )
