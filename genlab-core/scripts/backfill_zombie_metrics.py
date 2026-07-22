"""Backfill publishing_analytics rows stuck at SUCCESS for FB + Threads.

Context: two independent bugs shipped 2026-07-22 kept FB and Threads rows
at SUCCESS forever:

    * Threads: `_fetch_platform_insights` had no elif branch — the
      dispatcher returned None for every Threads post (commit f9f186c2).
    * FB: `_REELS_INSIGHTS_METRICS` included `post_impressions_unique`
      which Meta v22 rejects, poisoning the entire batch (commit 2898cc1e).

Both fetchers are now live-verified against real prod post_ids returning
real engagement data. This script iterates the historical zombie pool
and transitions each row SUCCESS → INSIGHTS_48H with the raw metric
values Meta returns.

Usage:
    # Dry-run (default) — report what WOULD happen, no writes
    uv run python -m scripts.backfill_zombie_metrics

    # Actually write to prod
    uv run python -m scripts.backfill_zombie_metrics --commit

    # Limit to N rows (safety valve)
    uv run python -m scripts.backfill_zombie_metrics --commit --limit 10

Rate limit: 3s sleep between calls to stay well under Meta's per-app
quota (X-App-Usage stays below ~15% baseline per observations 2026-07-22).

Idempotency: persistent state file at
`/opt/genlab/.runtime/zombie_backfill_state.json`. Each successful backfill
appends its post_id; re-runs skip already-processed rows. Per rule #15,
the state file must be owned by `genlab:genlab` (systemd services fail
open on PermissionError which silently loses progress).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

_STATE_PATH = Path("/opt/genlab/.runtime/zombie_backfill_state.json")
_SLEEP_S = 3.0
_ELIGIBLE_PLATFORMS = ("facebook", "threads")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _load_state() -> set[str]:
    """Return set of already-processed post_ids."""
    if not _STATE_PATH.exists():
        return set()
    try:
        data = json.loads(_STATE_PATH.read_text())
        return set(data.get("processed_post_ids", []))
    except (OSError, ValueError) as exc:
        logger.warning("state read failed (starting fresh): %s", exc)
        return set()


def _save_state(processed: set[str]) -> None:
    """Persist processed set atomically with rule #15 ownership hygiene."""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"processed_post_ids": sorted(processed)}, indent=2))
    tmp.replace(_STATE_PATH)


def _fetch_eligible_rows() -> list[dict[str, Any]]:
    """Query publishing_analytics for stuck SUCCESS rows on FB + Threads.

    Age filter: 6-720h (30d) — matches insight windows_completed span so
    we backfill everything that could have been but wasn't. Order by
    published_at DESC so newest-first (most useful for reward signal).
    """
    from genlab_core.storage.tenant_context import pg_connect

    with pg_connect(niche_id="all") as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, post_id, platform, niche_id,
                   EXTRACT(EPOCH FROM (NOW() - published_at)) / 3600 AS age_hours
            FROM publishing_analytics
            WHERE status = 'SUCCESS'
              AND platform = ANY(%s)
              AND published_at >= NOW() - INTERVAL '30 days'
              AND published_at <= NOW() - INTERVAL '6 hours'
            ORDER BY published_at DESC
            """,
            (list(_ELIGIBLE_PLATFORMS),),
        )
        rows = cur.fetchall()

    return [
        {
            "record_id": row[0],
            "post_id": row[1],
            "platform": row[2],
            "niche_id": row[3],
            "age_hours": float(row[4] or 0),
        }
        for row in rows
    ]


def _pick_window(age_hours: float) -> int:
    """Which insight window bucket does this row's age fall into?

    Matches `run_fetch_insights.WINDOW_RANGES` — final bucket is 168h
    (weekly snapshot). Newer rows go to a narrower bucket.
    """
    if age_hours >= 164:
        return 168
    if age_hours >= 44:
        return 48
    if age_hours >= 20:
        return 24
    return 6


def backfill_one(
    row: dict[str, Any],
    *,
    dry_run: bool,
) -> tuple[bool, str]:
    """Backfill a single row. Returns (success, reason)."""
    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.scripts.run_fetch_insights import (
        _fetch_platform_insights,
        _load_env_for_niche,
        _mark_window_completed,
    )

    _load_env_for_niche(row["niche_id"])
    insights = _fetch_platform_insights(
        row["platform"], row["post_id"], niche_id=row["niche_id"]
    )
    if not insights:
        return False, "fetcher returned empty/None"

    window = _pick_window(row["age_hours"])
    reason = f"{row['platform']} → INSIGHTS_{window}H (views={insights.get('views', 0)})"

    if dry_run:
        return True, f"DRY: {reason}"

    client = BacklogClient()
    try:
        client.upsert_analytics(
            post_id=row["post_id"],
            platform=row["platform"],
            insights=insights,
            blueprint_record_id="",
            candidate_id="",
            published_at="",
            fetch_window=f"{window}h",
            niche_id=row["niche_id"],
        )
        _mark_window_completed(client, row["record_id"], "", window, metrics=insights)
    except Exception as exc:
        return False, f"upsert failed: {exc}"

    return True, reason


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commit", action="store_true", help="Actually write (default: dry-run)")
    p.add_argument("--limit", type=int, default=0, help="Stop after N rows (0 = no limit)")
    args = p.parse_args()

    dry_run = not args.commit
    logger.info("mode: %s", "DRY-RUN" if dry_run else "COMMIT")

    processed = _load_state()
    logger.info("state: %d rows already processed", len(processed))

    rows = _fetch_eligible_rows()
    todo = [r for r in rows if r["post_id"] not in processed]
    logger.info("eligible: %d total, %d not-yet-processed", len(rows), len(todo))

    if args.limit > 0:
        todo = todo[: args.limit]
        logger.info("limit: capped to %d rows", args.limit)

    ok = 0
    fail = 0
    by_platform: dict[str, dict[str, int]] = {}

    for i, row in enumerate(todo, 1):
        success, reason = backfill_one(row, dry_run=dry_run)
        stats = by_platform.setdefault(row["platform"], {"ok": 0, "fail": 0})
        if success:
            ok += 1
            stats["ok"] += 1
            logger.info(
                "[%d/%d] %s %s: %s",
                i,
                len(todo),
                "OK" if not dry_run else "DRY-OK",
                row["post_id"][:32],
                reason,
            )
            if not dry_run:
                processed.add(row["post_id"])
                if ok % 5 == 0:
                    _save_state(processed)  # checkpoint every 5
        else:
            fail += 1
            stats["fail"] += 1
            logger.warning(
                "[%d/%d] FAIL %s: %s", i, len(todo), row["post_id"][:32], reason
            )
        if i < len(todo):
            time.sleep(_SLEEP_S)

    if not dry_run:
        _save_state(processed)

    logger.info("=" * 50)
    logger.info("done: %d ok, %d fail (of %d attempted)", ok, fail, len(todo))
    for platform, s in sorted(by_platform.items()):
        logger.info("  %-10s ok=%3d fail=%3d", platform, s["ok"], s["fail"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
