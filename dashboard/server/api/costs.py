"""Cost-tracking endpoints.

Per the 2026-06-13 autonomy deep-dive: the operator had no visibility
into per-niche LLM/image/TTS spend — there was no cost table in the
DB, and ``CostAccumulator`` only dumped to per-run JSON files. The
2026-06-14 follow-up shipped:

  * Migration ``p6k7l8m9n0o1`` (table ``pipeline_run_costs``)
  * ``cost_persist.persist_run_cost`` called from ``run_report.py``
  * This module — operator-facing query API

Endpoints
=========

``GET /api/v1/costs/summary?days=7``
    Per-niche × per-category rollup over a rolling window. Headline
    answer to "what did each channel cost this week."

``GET /api/v1/costs/by-niche/<niche_id>?days=30&group_by=day``
    Time-series for one niche. Operator drilldown for "is gaming's
    LLM spend trending up or down."

Both endpoints are read-only and return clean error envelopes on
DB / parse failures (never 500 silently).
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("costs_api", __name__, url_prefix="/api/v1/costs")

# Whitelist of niche IDs accepted by the by-niche endpoint. Same
# pattern as the auto-approval calibration-stats endpoint —
# defense-in-depth against typos / SQL injection attempts in the
# path parameter (even though the underlying query is parameterized).
_VALID_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


def _connect_or_error():
    """Return (conn, None) on success or (None, error_response) on failure.

    Centralizes the DATABASE_URL + psycopg.connect handling so both
    endpoints behave identically on infra failure.
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return None, api_error(error="DATABASE_URL not configured", code=503)
    try:
        import psycopg

        return psycopg.connect(dsn, connect_timeout=5), None
    except Exception as exc:
        logger.exception("costs API: psycopg.connect failed")
        return None, api_error(error=f"DB connect failed: {exc}", code=503)


@bp.route("/summary", methods=["GET"])
def summary():
    """Per-niche × per-category rollup over a rolling window.

    Response shape (matches the React TypeScript interface):
        {
          "window_days": 7,
          "total_usd": 12.34,
          "rows": [
            {
              "niche_id": "gaming",
              "total_usd": 4.56,
              "llm_usd": 3.20,
              "image_usd": 1.36,
              "tts_usd": 0.00,
              "run_count": 7,
              "avg_per_run_usd": 0.6514
            },
            ...
          ]
        }
    """
    try:
        days = int(request.args.get("days", "7"))
    except (TypeError, ValueError):
        return api_error(error="days must be an integer", code=400)
    if days < 1 or days > 365:
        return api_error(error="days must be in 1..365", code=400)

    conn, err = _connect_or_error()
    if err:
        return err
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT niche_id,
                           ROUND(SUM(total_usd)::numeric, 4) AS total_usd,
                           ROUND(SUM(llm_usd)::numeric, 4) AS llm_usd,
                           ROUND(SUM(image_usd)::numeric, 4) AS image_usd,
                           ROUND(SUM(tts_usd)::numeric, 4) AS tts_usd,
                           ROUND(SUM(compute_usd)::numeric, 4) AS compute_usd,
                           ROUND(SUM(media_usd)::numeric, 4) AS media_usd,
                           ROUND(SUM(bandwidth_usd)::numeric, 4) AS bandwidth_usd,
                           COUNT(*) AS run_count,
                           ROUND((SUM(total_usd) / NULLIF(COUNT(*), 0))::numeric, 4) AS avg_per_run
                    FROM pipeline_run_costs
                    WHERE completed_at >= NOW() - (%s || ' days')::interval
                    GROUP BY 1 ORDER BY 1
                    """,
                    (str(days),),
                )
                rows = cur.fetchall()
                # Aggregate total across all niches for the headline pill
                cur.execute(
                    """
                    SELECT COALESCE(ROUND(SUM(total_usd)::numeric, 4), 0)
                    FROM pipeline_run_costs
                    WHERE completed_at >= NOW() - (%s || ' days')::interval
                    """,
                    (str(days),),
                )
                grand_total = cur.fetchone()[0]
    except Exception as exc:
        logger.exception("costs/summary query failed")
        return api_error(error=f"Query failed: {exc}", code=500)

    rows_out = []
    for r in rows:
        rows_out.append(
            {
                "niche_id": r[0],
                "total_usd": float(r[1] or 0),
                "llm_usd": float(r[2] or 0),
                "image_usd": float(r[3] or 0),
                "tts_usd": float(r[4] or 0),
                "compute_usd": float(r[5] or 0),
                "media_usd": float(r[6] or 0),
                "bandwidth_usd": float(r[7] or 0),
                "run_count": int(r[8] or 0),
                "avg_per_run_usd": float(r[9] or 0),
            }
        )

    return api_success(
        data={
            "window_days": days,
            "total_usd": float(grand_total or 0),
            "rows": rows_out,
        }
    )


@bp.route("/by-niche/<niche_id>", methods=["GET"])
def by_niche(niche_id: str):
    """Time-series for one niche. Operator drilldown for trending spend.

    Query params:
        days (default 30, range 1..365)
        group_by (default "day", one of "day" / "week")

    Response shape:
        {
          "niche_id": "gaming",
          "window_days": 30,
          "group_by": "day",
          "series": [
            {"period": "2026-06-13", "total_usd": 0.6234, "run_count": 1},
            ...
          ]
        }
    """
    if niche_id not in _VALID_NICHES:
        return api_error(error=f"niche_id must be one of {sorted(_VALID_NICHES)}", code=400)

    try:
        days = int(request.args.get("days", "30"))
    except (TypeError, ValueError):
        return api_error(error="days must be an integer", code=400)
    if days < 1 or days > 365:
        return api_error(error="days must be in 1..365", code=400)

    group_by = request.args.get("group_by", "day")
    if group_by not in ("day", "week"):
        return api_error(error="group_by must be 'day' or 'week'", code=400)

    # date_trunc is safe to interpolate as a literal because we've
    # whitelisted to {"day", "week"}; underlying value param is still
    # parameterized.
    trunc_unit = "day" if group_by == "day" else "week"

    conn, err = _connect_or_error()
    if err:
        return err
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT to_char(date_trunc('{trunc_unit}', completed_at),
                                   'YYYY-MM-DD') AS period,
                           ROUND(SUM(total_usd)::numeric, 4) AS total,
                           COUNT(*) AS runs
                    FROM pipeline_run_costs
                    WHERE niche_id = %s
                      AND completed_at >= NOW() - (%s || ' days')::interval
                    GROUP BY 1 ORDER BY 1
                    """,
                    (niche_id, str(days)),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.exception("costs/by-niche query failed")
        return api_error(error=f"Query failed: {exc}", code=500)

    series = [
        {"period": r[0], "total_usd": float(r[1] or 0), "run_count": int(r[2] or 0)} for r in rows
    ]

    return api_success(
        data={
            "niche_id": niche_id,
            "window_days": days,
            "group_by": group_by,
            "series": series,
        }
    )
