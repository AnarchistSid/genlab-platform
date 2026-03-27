"""DPO preference data collection from engagement metrics.

Pairs top-performing hooks (chosen) with bottom-performing (rejected)
within each niche × platform group. Generates training data for future
DPO fine-tuning on a 7B-8B parameter model.

At 5 reels/day, after 3 months GenLab will have ~450 pairs.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MIN_VIEWS = 100    # Minimum views for a blueprint to be considered
MIN_RATIO = 1.5    # Minimum ratio between chosen and rejected views


def collect_weekly_pairs(window_days: int = 7) -> int:
    """Generate chosen/rejected pairs from recent engagement data.

    Returns the number of pairs created.
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
        cur.execute("""
            SELECT b.id::text as blueprint_id, b.niche_id, b.hook, b.caption,
                   pa.platform, pa.views, pa.likes,
                   COALESCE(pa.views, 0) as view_count
            FROM blueprints b
            JOIN publishing_analytics pa ON pa.blueprint_id::text = b.id::text
            WHERE pa.status = 'INSIGHTS_168H'
              AND pa.views IS NOT NULL AND pa.views >= %s
              AND b.created_at >= %s
            ORDER BY b.niche_id, pa.platform, pa.views DESC
        """, (MIN_VIEWS, since))

        rows = cur.fetchall()
        if len(rows) < 4:
            logger.info("[preference] Only %d eligible rows — need at least 4 for pairs", len(rows))
            conn.close()
            return 0

        # Group by niche × platform
        groups: dict[str, list] = {}
        for r in rows:
            key = f"{r['niche_id']}:{r['platform']}"
            groups.setdefault(key, []).append(r)

        for key, items in groups.items():
            if len(items) < 4:
                continue

            # Top 25% = chosen, bottom 25% = rejected
            n = len(items)
            quarter = max(1, n // 4)
            chosen_pool = items[:quarter]
            rejected_pool = items[-quarter:]

            for chosen in chosen_pool:
                for rejected in rejected_pool:
                    if chosen["blueprint_id"] == rejected["blueprint_id"]:
                        continue

                    ratio = chosen["view_count"] / max(rejected["view_count"], 1)
                    if ratio < MIN_RATIO:
                        continue

                    # Check for duplicates
                    cur.execute(
                        "SELECT 1 FROM preference_data WHERE chosen_blueprint_id = %s AND rejected_blueprint_id = %s",
                        (chosen["blueprint_id"], rejected["blueprint_id"]),
                    )
                    if cur.fetchone():
                        continue

                    cur.execute("""
                        INSERT INTO preference_data
                        (niche_id, platform, chosen_hook, chosen_caption,
                         chosen_engagement, chosen_blueprint_id,
                         rejected_hook, rejected_caption,
                         rejected_engagement, rejected_blueprint_id,
                         engagement_ratio)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        chosen["niche_id"], chosen["platform"],
                        chosen["hook"], chosen.get("caption", ""),
                        json.dumps({"views": chosen["view_count"], "likes": chosen.get("likes", 0)}),
                        chosen["blueprint_id"],
                        rejected["hook"], rejected.get("caption", ""),
                        json.dumps({"views": rejected["view_count"], "likes": rejected.get("likes", 0)}),
                        rejected["blueprint_id"],
                        round(ratio, 2),
                    ))
                    pairs_created += 1

        conn.commit()
        conn.close()
        logger.info("[preference] Created %d pairs from %d eligible blueprints", pairs_created, len(rows))
        return pairs_created

    except Exception as exc:
        logger.exception("[preference] Collection failed: %s", exc)
        return 0
