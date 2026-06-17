"""Source-quality endpoints.

Per the 2026-06-13 autonomy deep-dive memory: **22 of the 30 top
content sources have 0% claim rate over 14 days**. The operator is
paying for ~2,000 wasted fetches per 14 days from sources that
produce no pipeline output. This endpoint surfaces per-source
performance so the operator can prune them.

Endpoint: GET /api/v1/sources/performance?days=14&min_fetched=5

Response:
    {
      "window_days": 14,
      "total_fetched": 4691,
      "total_claimed": 559,
      "claim_rate": 0.119,
      "bucket_counts": {"active": 4, "weak": 4, "dead": 22, "unproven": 0},
      "sources": [
        {
          "source_name": "r/gaming new",
          "source_platform": "reddit",
          "fetched": 119,
          "claimed": 9,
          "claim_pct": 7.6,
          "distinct_niches": 1,
          "latest_fetch": "2026-06-13T...",
          "health": "active"  // "active" | "weak" | "dead" | "unproven"
        },
        ...
      ]
    }

Health buckets are operator-friendly heuristics:
    - "active"     → claim_pct ≥ 5%   (worth keeping)
    - "weak"       → 1% ≤ claim_pct < 5%   (consider pruning)
    - "dead"       → claim_pct < 1% AND fetched ≥ 10   (prune)
    - "unproven"   → fetched < 10  (brand-new source, give it time)

The min-fetch threshold on "dead" prevents a brand-new source with 0
fetches from being labeled dead before it has a chance to prove itself.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("sources_api", __name__, url_prefix="/api/v1/sources")

_DEFAULT_WINDOW_DAYS = 14
_MIN_FETCH_FOR_DEAD_LABEL = 10
_DEAD_PCT = 1.0
_WEAK_PCT = 5.0


def _classify_health(fetched: int, claim_pct: float) -> str:
    """Bucket a source's health into operator-actionable labels.

    The 4 labels are deliberately coarse — the operator's binary
    decision is "keep or prune," and the bucket names should make
    that decision visually obvious from a glance at the dashboard.
    """
    if claim_pct >= _WEAK_PCT:
        return "active"
    if claim_pct >= _DEAD_PCT:
        return "weak"
    if fetched >= _MIN_FETCH_FOR_DEAD_LABEL:
        return "dead"
    # Below the dead-fetch floor — could be a brand-new source that
    # hasn't had time to prove itself. Don't shame it yet.
    return "unproven"


@bp.route("/performance", methods=["GET"])
def source_performance():
    """Per-source rollup over a rolling window.

    Joins ``content_pool`` (fetched rows) with claim attribution
    (claimed_by) and groups by (source_name, source_platform). Sources
    are returned sorted by fetched-volume descending so the operator
    sees the high-volume waste first.

    Query params:
      * ``days`` (default 14, range 1..90) — lookback window
      * ``min_fetched`` (default 5) — exclude long-tail noise so the
        table is operator-actionable
    """
    try:
        window_days = int(request.args.get("days", _DEFAULT_WINDOW_DAYS))
    except (TypeError, ValueError):
        return api_error(error="days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="days must be in 1..90", code=400)

    try:
        min_fetched = int(request.args.get("min_fetched", "5"))
    except (TypeError, ValueError):
        return api_error(error="min_fetched must be an integer", code=400)
    if min_fetched < 1:
        return api_error(error="min_fetched must be ≥ 1", code=400)

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)

    try:
        # Lazy import so the dashboard test harness doesn't require
        # psycopg installed when this endpoint isn't exercised.

        with pg_connect(dsn, connect_timeout=5, niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_name,
                           COALESCE(source_platform, 'unknown') AS source_platform,
                           COUNT(*) AS fetched,
                           COUNT(*) FILTER (WHERE claimed_by IS NOT NULL) AS claimed,
                           COUNT(DISTINCT claimed_by)
                               FILTER (WHERE claimed_by IS NOT NULL) AS distinct_niches,
                           MAX(created_at) AS latest_fetch
                    FROM content_pool
                    WHERE created_at >= NOW() - (%s || ' days')::interval
                      AND source_name IS NOT NULL
                    GROUP BY 1, 2
                    HAVING COUNT(*) >= %s
                    ORDER BY fetched DESC
                    LIMIT 200
                    """,
                    (str(window_days), min_fetched),
                )
                rows = cur.fetchall()

                # Aggregates: total fetched + total claimed for the
                # headline stat. Computed in SQL to avoid summing
                # 200-row Python lists when content_pool is large.
                cur.execute(
                    """
                    SELECT COUNT(*) AS fetched,
                           COUNT(*) FILTER (WHERE claimed_by IS NOT NULL) AS claimed
                    FROM content_pool
                    WHERE created_at >= NOW() - (%s || ' days')::interval
                      AND source_name IS NOT NULL
                    """,
                    (str(window_days),),
                )
                total_row = cur.fetchone() or (0, 0)
    except Exception as exc:
        logger.exception("source-performance query failed")
        return api_error(error=f"Query failed: {exc}", code=500)

    sources_out = []
    for r in rows:
        fetched = int(r[2] or 0)
        claimed = int(r[3] or 0)
        claim_pct = round(100.0 * claimed / fetched, 2) if fetched else 0.0
        sources_out.append(
            {
                "source_name": r[0],
                "source_platform": r[1],
                "fetched": fetched,
                "claimed": claimed,
                "claim_pct": claim_pct,
                "distinct_niches": int(r[4] or 0),
                "latest_fetch": r[5].isoformat() if r[5] else None,
                "health": _classify_health(fetched, claim_pct),
            }
        )

    total_fetched = int(total_row[0] or 0)
    total_claimed = int(total_row[1] or 0)
    overall_rate = round(total_claimed / total_fetched, 3) if total_fetched else 0.0

    # Buckets summary for the dashboard headline pill ("22 dead sources").
    bucket_counts = {"active": 0, "weak": 0, "dead": 0, "unproven": 0}
    for s in sources_out:
        bucket_counts[s["health"]] += 1

    return api_success(
        data={
            "window_days": window_days,
            "total_fetched": total_fetched,
            "total_claimed": total_claimed,
            "claim_rate": overall_rate,
            "bucket_counts": bucket_counts,
            "sources": sources_out,
        }
    )
