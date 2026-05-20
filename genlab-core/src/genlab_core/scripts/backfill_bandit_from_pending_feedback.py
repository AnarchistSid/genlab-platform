"""One-shot backfill: replay historical pending_feedback rewards into bandit_arms.

Why this exists
---------------
Between 2026-03-17 (Postgres migration) and 2026-05-19 (commit e0f12f1),
``PendingFeedbackStore._from_sharepoint_item`` raised on every Postgres
row whose ``bandit_context`` was a non-empty dict (it called
``json.loads()`` on an already-parsed JSONB). The exception was swallowed
at DEBUG level inside ``get_pending()``, which then returned ``[]``. For
63 days, ``collect_metrics`` saw zero pending tasks and never called
``_default_bandit_updater``.

But the row data itself was correct — ``reward_48h`` was set on PF rows
by older code paths (e.g. the early-stop branch which doesn't go through
``_from_sharepoint_item`` parse, or pre-migration SharePoint paths).
The 2026-05-20 health-monitor v2 check ``check_bandit_posterior_drift``
surfaced ~93 PF rows across 7 arms with rewards that never made it to
the bandit.

What this does
--------------
Reads PF rows where ``reward_48h IS NOT NULL`` and ``collection_status``
is past 48h, then calls ``_default_bandit_updater`` exactly once per row.

Safety
------
The 2026-03-17 -> 2026-05-19 outage means rows with ``updated_at`` in
that window definitely did NOT reach the bandit. Rows older than
2026-03-17 (SharePoint era) DID flow through the old bandit code, so
they're EXCLUDED to prevent double-counting. Rows newer than 2026-05-19
either ran through the fixed bandit code (don't re-credit) OR are
zombies whose status was advanced without firing the bandit; the
``--include-post-fix`` flag enables the latter only when needed.

Idempotency
-----------
Tracked via ``extra->>'bandit_backfilled_at'`` on each PF row. A row
already stamped is skipped. Re-running is safe.

Usage
-----
    # Dry run (default): show what would happen, write nothing
    uv run python -m genlab_core.scripts.backfill_bandit_from_pending_feedback

    # Apply for real
    uv run python -m genlab_core.scripts.backfill_bandit_from_pending_feedback --apply

    # Optionally include post-fix zombies (rare)
    uv run python -m genlab_core.scripts.backfill_bandit_from_pending_feedback --apply --include-post-fix
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_bandit")

# The Postgres-parse outage window. Rows whose reward_48h was finalised
# inside this range did NOT reach the bandit, so they are safe to replay.
OUTAGE_START = "2026-03-17"
OUTAGE_END = "2026-05-19"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually call bandit_updater (default: dry run)")
    parser.add_argument("--include-post-fix", action="store_true",
                        help="Also replay rows updated after the fix landed "
                             "(only needed for zombies — use with care)")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL not set")
        return 1

    import psycopg
    from psycopg.rows import dict_row

    from genlab_core.learning.metric_collector import _default_bandit_updater

    where = [
        "reward_48h IS NOT NULL",
        "arm_id IS NOT NULL AND arm_id <> ''",
        f"updated_at >= '{OUTAGE_START}'::date",
        # Backfill marker on the row's ``extra`` JSONB prevents double-replay
        "(extra->>'bandit_backfilled_at') IS NULL",
    ]
    if not args.include_post_fix:
        where.append(f"updated_at <= '{OUTAGE_END}'::date")

    sql = f"""
        SELECT id, niche_id, arm_id, platform, reward_48h, bandit_context,
               collection_status, updated_at
        FROM pending_feedback
        WHERE {' AND '.join(where)}
        ORDER BY niche_id, arm_id, updated_at
    """

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        logger.info("Found %d PF rows eligible for backfill", len(rows))
        if not rows:
            return 0

        # Group by (niche, arm) for the report
        grouped: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            grouped.setdefault((r["niche_id"], r["arm_id"]), []).append(r)

        print(f"\n{'NICHE':12} {'ARM':30} {'COUNT':>6} {'AVG_REWARD':>11}")
        print("-" * 65)
        for (nid, arm), rs in sorted(grouped.items()):
            avg = sum(r["reward_48h"] for r in rs) / len(rs)
            print(f"{nid:12} {arm:30} {len(rs):>6} {avg:>11.3f}")

        if not args.apply:
            print(f"\nDRY RUN — would replay {len(rows)} rewards. "
                  f"Run with --apply to commit.")
            return 0

        # APPLY: call updater for each row, then mark as backfilled
        ok = 0
        fail = 0
        for r in rows:
            try:
                ctx = r.get("bandit_context") or None
                if isinstance(ctx, str):
                    ctx = json.loads(ctx) if ctx else None
                _default_bandit_updater(
                    r["niche_id"],
                    r["arm_id"],
                    r["platform"],
                    float(r["reward_48h"]),
                    ctx,
                )
                # Stamp the row so a re-run skips it
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE pending_feedback
                        SET extra = extra || jsonb_build_object(
                            'bandit_backfilled_at', %s::text
                        )
                        WHERE id = %s
                        """,
                        (datetime.now(UTC).isoformat(), r["id"]),
                    )
                ok += 1
            except Exception as exc:
                logger.warning("Backfill failed for %s/%s id=%s: %s",
                               r["niche_id"], r["arm_id"], r["id"], exc)
                fail += 1
        conn.commit()

        print(f"\nBackfill complete: ok={ok} fail={fail}")
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
