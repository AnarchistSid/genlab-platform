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

# Kept in sync with dashboard/server/api/attribution_health.py.
# Post-2026-07-11 audit: 100/99 instead of 95/90. Any single miss
# is a real audience-facing failure.
_HEALTHY_PCT = 100.0
_CAUTION_PCT = 99.0


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

    # 2026-07-14 (class-of-bug scan): import canonical markers +
    # case-insensitive match. Prior state hardcoded case-sensitive
    # "\U0001f3ac Original:" — diverged from
    # platforms/caption_validation.py which lowercases input before
    # comparing. Same-invariant-two-paths class-of-bug that the
    # 2026-07-13 writer-wire session established the pattern for.
    from genlab_core.platforms.caption_validation import (
        _MARKER_FOOTAGE,
        _MARKER_ORIGINAL,
    )

    original_mark = _MARKER_ORIGINAL  # already lowercase
    footage_mark = _MARKER_FOOTAGE  # already lowercase

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Post-2026-07-11 audit tightening: the metric now requires
            # a credit marker to be present in AT LEAST ONE caption/
            # description field. Previously the "source LIKE '%twitch%' +
            # video_url populated" clause counted a blueprint as
            # attributed even when its caption shipped empty of visible
            # credit — which is exactly what happened to today's gaming
            # publish. Internal metric said "100%", real users saw a
            # blank caption. That failure mode is now impossible.
            #
            # ``source_channel_id IS NOT NULL`` is also removed as a
            # standalone signal for the same reason — the ID may be
            # persisted while the caption ships without the credit
            # line (writer wire silently failed, publisher backstop
            # skipped because caption already had "🎬 Original:" from
            # an unrelated match, etc.). Only the CAPTION MARKER
            # counts now.
            # Post-2026-07-13 audit follow-up: union ALL platform-content
            # fields that carry per-platform caption text. Previously the
            # SQL only checked 4 fields (caption, facebook_content,
            # threads_content, youtube_content). Missing: twitter_content
            # (present on 131/131 recent blueprints) and tiktok_content
            # (future-proof — TikTok publisher activates on audit
            # approval). Without the twitter_content clause, any
            # blueprint where the writer put the credit line into the
            # tweet-specific field but not into the main caption would
            # silently drop out of the "with_attribution" count. IG uses
            # the top-level ``caption`` field (no separate
            # instagram_content key) so it's already covered.
            cur.execute(
                """
                -- 2026-07-14 (class-of-bug scan): LOWER() the columns
                -- so LIKE matches case-insensitively against markers
                -- (which are already lowercased). Sibling fix at
                -- monitoring/attribution_health_monitor.py:95.
                SELECT
                    niche_id,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(caption, '')) LIKE %s
                           OR LOWER(COALESCE(caption, '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'facebook_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'facebook_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'threads_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'threads_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'youtube_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'youtube_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'twitter_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'twitter_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'tiktok_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'tiktok_content', '')) LIKE %s
                    ) AS with_attribution
                FROM blueprints
                WHERE status = 'PUBLISHED'
                  AND updated_at > NOW() - (%s || ' hours')::interval
                GROUP BY niche_id
                ORDER BY niche_id
                """,
                (
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
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
