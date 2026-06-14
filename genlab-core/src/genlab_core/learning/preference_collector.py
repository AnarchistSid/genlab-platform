"""DPO preference data collection from engagement metrics.

Pairs top-performing hooks (chosen) with bottom-performing (rejected)
within each niche × platform group. Generates training data for future
DPO fine-tuning on a 7B-8B parameter model.

At 5 reels/day, after 3 months GenLab will have ~450 pairs.

Threshold tuning history
========================

* Sprint 68 ship: MIN_VIEWS=100 absolute floor. Worked theoretically
  but not in practice: of 1,267 INSIGHTS_168H rows on prod as of
  2026-06-14, only 6 cleared the 100-view bar (max real views = 220).
  At current channel sizes (most channels under 25 followers per
  platform — see ``[[session-2026-06-13-autonomy-deep-dive-v2]]``)
  the absolute floor blocked every niche.
* 2026-06-14 retune: MIN_VIEWS dropped to 10. The percentile-based
  top-25%/bottom-25% pool selection that's been here since Sprint 68
  is the real preference signal; the absolute floor is just a noise
  filter to keep literal-zero-view rows out of the bottom pool. With
  the lower floor, gaming-IG (the warmest channel + platform) can
  start producing pairs from existing data instead of waiting for the
  channels to grow into the original threshold.

The MIN_RATIO=1.5 between chosen and rejected views is retained — it's
the actual preference-strength signal. A 1.5× view delta is a real
difference even at small absolute numbers (15 vs 10 still expresses
"one performed meaningfully better"); below 1.5× the signal is noise.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Floor on view count to filter literal-zero or single-impression noise.
# Lowered from 100 → 10 on 2026-06-14 to match current channel reach;
# percentile-based pool selection (top-25%/bottom-25%) provides the
# preference signal independently of absolute counts.
MIN_VIEWS = 10

# Minimum ratio between chosen.views and rejected.views before we trust
# the pair as a real preference signal. 1.5 is unchanged: even at small
# absolute view counts (15 vs 10), a 1.5× delta is a real difference;
# below 1.5× the comparison is dominated by chance.
MIN_RATIO = 1.5

# Default lookback window. The original 7d matched the Sunday weekly
# timer cadence — "look at the last week each run." But at current
# channel reach + 1-reel-per-day publish rate, no single (niche ×
# platform) group accumulates the ≥4 rows needed for pair formation
# in 7 days; per the 2026-06-14 probe, only gaming-IG ever clears
# the bar and even it needs 24-30 days at current pace.
#
# 21 days (= 3 weeks) is the smallest window that finds at least one
# eligible group in current prod data. Duplicate pairs are filtered
# via the ``preference_data`` existence check inside the loop, so
# widening the window doesn't re-insert work the prior run did.
# Once channels grow past ~5x current reach, this can drop back to
# 7 or 14 without losing signal.
DEFAULT_WINDOW_DAYS = 21


def collect_weekly_pairs(window_days: int = DEFAULT_WINDOW_DAYS) -> int:
    """Generate chosen/rejected pairs from recent engagement data.

    Returns the number of pairs created. Emits a structured diagnostic
    line per (niche × platform) group so the operator can SEE why
    pairs aren't forming when totals stay at zero (the previous silent
    "Created 0 pairs from N eligible blueprints" log made the threshold
    failure invisible).
    """
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "dbname=genlab")
    since = date.today() - timedelta(days=window_days)
    pairs_created = 0

    try:
        conn = psycopg.connect(dsn, row_factory=dict_row)
        cur = conn.cursor()

        # Set RLS to allow all niches
        cur.execute("SELECT set_config('app.niche_id', '', true)")

        # Get published blueprints with engagement data (168h window = mature metrics)
        cur.execute(
            """
            SELECT b.id::text as blueprint_id, b.niche_id, b.hook, b.caption,
                   pa.platform, pa.views, pa.likes,
                   COALESCE(pa.views, 0) as view_count
            FROM blueprints b
            JOIN publishing_analytics pa ON pa.blueprint_id::text = b.id::text
            WHERE pa.status = 'INSIGHTS_168H'
              AND pa.views IS NOT NULL AND pa.views >= %s
              AND b.created_at >= %s
            ORDER BY b.niche_id, pa.platform, pa.views DESC
        """,
            (MIN_VIEWS, since),
        )

        rows = cur.fetchall()
        if len(rows) < 4:
            logger.info(
                "[preference] Only %d eligible rows across all niches × platforms "
                "(min_views=%d, window_days=%d) — need ≥4 within a single group "
                "to form pairs. Operator action: refresh expired tokens or wait "
                "for more INSIGHTS_168H rows to accumulate.",
                len(rows),
                MIN_VIEWS,
                window_days,
            )
            conn.close()
            return 0

        # Group by niche × platform
        groups: dict[str, list] = {}
        for r in rows:
            key = f"{r['niche_id']}:{r['platform']}"
            groups.setdefault(key, []).append(r)

        # Pre-loop diagnostic: which groups can actually form pairs?
        # Surfaces the "data sparsity gate" the operator otherwise can't see.
        for key, items in sorted(groups.items()):
            n = len(items)
            qualifies = n >= 4
            status = "eligible" if qualifies else "BELOW_THRESHOLD"
            logger.info(
                "[preference] %s: rows=%d (top_view=%d, bottom_view=%d) — %s",
                key,
                n,
                items[0]["view_count"] if items else 0,
                items[-1]["view_count"] if items else 0,
                status,
            )

        for key, items in groups.items():
            if len(items) < 4:
                continue

            # Top 25% = chosen, bottom 25% = rejected
            n = len(items)
            quarter = max(1, n // 4)
            chosen_pool = items[:quarter]
            rejected_pool = items[-quarter:]
            attempted = 0
            ratio_skipped = 0
            dup_skipped = 0
            group_pairs = 0

            for chosen in chosen_pool:
                for rejected in rejected_pool:
                    if chosen["blueprint_id"] == rejected["blueprint_id"]:
                        continue
                    attempted += 1

                    ratio = chosen["view_count"] / max(rejected["view_count"], 1)
                    if ratio < MIN_RATIO:
                        ratio_skipped += 1
                        continue

                    # Check for duplicates
                    cur.execute(
                        "SELECT 1 FROM preference_data WHERE chosen_blueprint_id = %s AND rejected_blueprint_id = %s",
                        (chosen["blueprint_id"], rejected["blueprint_id"]),
                    )
                    if cur.fetchone():
                        dup_skipped += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO preference_data
                        (niche_id, platform, chosen_hook, chosen_caption,
                         chosen_engagement, chosen_blueprint_id,
                         rejected_hook, rejected_caption,
                         rejected_engagement, rejected_blueprint_id,
                         engagement_ratio)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                        (
                            chosen["niche_id"],
                            chosen["platform"],
                            chosen["hook"],
                            chosen.get("caption", ""),
                            json.dumps(
                                {"views": chosen["view_count"], "likes": chosen.get("likes", 0)}
                            ),
                            chosen["blueprint_id"],
                            rejected["hook"],
                            rejected.get("caption", ""),
                            json.dumps(
                                {"views": rejected["view_count"], "likes": rejected.get("likes", 0)}
                            ),
                            rejected["blueprint_id"],
                            round(ratio, 2),
                        ),
                    )
                    pairs_created += 1
                    group_pairs += 1

            # Per-group result diagnostic: surfaces "12 attempted but
            # all 12 had ratio < 1.5" cases that would otherwise be
            # invisible in the silent commit.
            logger.info(
                "[preference] %s: attempted=%d, ratio_skipped=%d, dup_skipped=%d, pairs_created=%d",
                key,
                attempted,
                ratio_skipped,
                dup_skipped,
                group_pairs,
            )

        conn.commit()
        conn.close()
        logger.info(
            "[preference] Created %d pairs from %d eligible blueprints", pairs_created, len(rows)
        )
        return pairs_created

    except Exception as exc:
        logger.exception("[preference] Collection failed: %s", exc)
        return 0
