"""Layer 5 attribution-health computation (PR #Layer5, 2026-07-11).

Backing module for ``/api/v1/attribution-health/stats``. Queries
Postgres for PUBLISHED blueprints in a rolling window and computes
``attribution_present_pct`` per niche + overall.

Attribution signals recognised (any is sufficient):

  1. ``source_channel_id`` populated (proposer-key: PR #762)
  2. Any of ``caption`` / ``extra->>facebook_content`` / ``extra->>
     threads_content`` / ``extra->>youtube_content`` contains
     ``"\U0001f3ac Original:"``  (writer wire + publisher backstop)
  3. Same fields contain ``"Footage:"``  (YouTube description)

Fail-open: returns empty rows on DB error so the card renders a
zero-state rather than 500.

## Why a separate module

Same rationale as ``publishing_health_per_niche.py``: the endpoint
layer stays thin and testable; the SQL + row-shape mapping lives here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_NICHE_IDS = ("ai_creators", "anime", "gaming", "movies", "sports")

# Kept in sync with dashboard/server/api/attribution_health.py
_HEALTHY_PCT = 95.0
_CAUTION_PCT = 90.0


def _classify(pct: float) -> str:
    """Return 'healthy' / 'caution' / 'critical' based on thresholds."""
    if pct >= _HEALTHY_PCT:
        return "healthy"
    if pct >= _CAUTION_PCT:
        return "caution"
    return "critical"


def compute_stats(
    *,
    window_hours: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (per_niche_rows, overall_stats).

    Both are dict shapes ready to serialise into the JSON response.
    per_niche_rows sorts by niche_id ascending. overall_stats aggregates
    all rows regardless of niche.

    Errors bubble up to the endpoint's fail-open wrapper.
    """
    import psycopg

    dsn = os.environ.get("DATABASE_URL") or "dbname=genlab"

    # ORIGINAL_MARK = 🎬 Original — the sentinel string emitted by
    # both format_source_attribution and the publisher backstop's
    # in-flight append. Substring match on the full unicode marker
    # is portable across Postgres text fields.
    original_mark = "\U0001f3ac Original:"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # We use ILIKE with '%%mark%%' escaped for psycopg's %s binding.
            cur.execute(
                """
                SELECT
                    niche_id,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE source_channel_id IS NOT NULL
                           OR COALESCE(caption, '') LIKE %s
                           OR COALESCE(caption, '') LIKE %s
                           OR COALESCE(extra->>'facebook_content', '') LIKE %s
                           OR COALESCE(extra->>'facebook_content', '') LIKE %s
                           OR COALESCE(extra->>'threads_content', '') LIKE %s
                           OR COALESCE(extra->>'threads_content', '') LIKE %s
                           OR COALESCE(extra->>'youtube_content', '') LIKE %s
                           OR COALESCE(extra->>'youtube_content', '') LIKE %s
                    ) AS with_attribution
                FROM blueprints
                WHERE status = 'PUBLISHED'
                  AND updated_at > NOW() - (%s || ' hours')::interval
                GROUP BY niche_id
                ORDER BY niche_id
                """,
                (
                    f"%{original_mark}%",
                    "%Footage:%",
                    f"%{original_mark}%",
                    "%Footage:%",
                    f"%{original_mark}%",
                    "%Footage:%",
                    f"%{original_mark}%",
                    "%Footage:%",
                    str(window_hours),
                ),
            )
            rows = cur.fetchall()

    # Ensure every known niche appears in the response even when
    # zero-published (row missing) — the dashboard should render an
    # explicit "0/0 · N/A" instead of hiding the niche.
    row_by_niche = {r[0]: (r[1], r[2]) for r in rows}
    per_niche: list[dict[str, Any]] = []
    total_all = with_all = 0
    for niche in _NICHE_IDS:
        total, with_attr = row_by_niche.get(niche, (0, 0))
        pct = round(100.0 * with_attr / total, 1) if total else 0.0
        per_niche.append(
            {
                "niche_id": niche,
                "total_published": int(total),
                "with_attribution": int(with_attr),
                "attribution_pct": pct,
                "status": _classify(pct) if total else "no_data",
            }
        )
        total_all += int(total)
        with_all += int(with_attr)

    overall_pct = round(100.0 * with_all / total_all, 1) if total_all else 0.0
    overall = {
        "total_published": total_all,
        "with_attribution": with_all,
        "attribution_pct": overall_pct,
        "status": _classify(overall_pct) if total_all else "no_data",
    }
    return per_niche, overall
