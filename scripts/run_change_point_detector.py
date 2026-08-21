#!/usr/bin/env python3
"""Phase 2.F — CUSUM change-point detector runner.

Fires weekly. For each (niche, platform) pair with ≥14d of
reward_48h history, runs CUSUM detection. When a change is flagged,
writes to pipeline_alerts as `platform_reward_shift:{niche}:{platform}`.

## Design

Simple time-series over the last 30 days: mean reward_48h per day.
Baseline window = first 7 days; CUSUM runs over the remaining 23.
Sensitivity: h=4σ, k=0.5σ (standard textbook values).

## Usage

    uv run python scripts/run_change_point_detector.py
    uv run python scripts/run_change_point_detector.py --dry-run

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger("run_change_point_detector")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")
IN_SCOPE_PLATFORMS = ("facebook", "instagram", "youtube", "threads")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args(argv)


def _daily_reward_series(conn, niche_id: str, platform: str) -> list[float]:
    """Return 30d chronological daily-mean reward_48h series. Missing
    days = 0.0 (treated as "no reward = bombed")."""
    try:
        rows = conn.execute(
            """
            SELECT DATE(created_at AT TIME ZONE 'UTC') AS day,
                   AVG(reward_48h)::float AS mean_reward
            FROM pending_feedback
            WHERE niche_id = %s AND platform = %s
              AND reward_48h IS NOT NULL
              AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY 1
            ORDER BY 1
            """,
            (niche_id, platform),
        ).fetchall()
    except Exception as exc:
        logger.debug("[change_point] query failed for %s/%s: %s",
                     niche_id, platform, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    # Bucket into 30 days chronologically
    from datetime import date, timedelta
    today = date.today()
    by_day = {r.get("day") if hasattr(r, "get") else r[0]: float(r.get("mean_reward") if hasattr(r, "get") else r[1]) for r in rows}
    return [
        by_day.get(today - timedelta(days=(29 - i)), 0.0)
        for i in range(30)
    ]


def _emit_alert(conn, niche_id: str, platform: str, cp) -> None:
    check_name = f"platform_reward_shift:{platform}"
    direction_label = "UP" if cp.direction == "up" else "DOWN"
    # 2026-08-21: severity keyed on DIRECTION as well as confidence.
    #
    # Previously this was `"warning" if cp.confidence < 0.9 else "critical"`,
    # which ignored direction entirely — so a high-confidence UP shift, i.e.
    # reward IMPROVING, paged as CRITICAL. The operator's Mission Control
    # banner opened on "4 unresolved CRITICAL system alerts" whose first
    # entry was good news (movies/instagram, +16.14σ, confidence 0.98).
    #
    # That is worse than noise: a CRITICAL band that routinely contains
    # things needing no action teaches the operator to skim it, and the real
    # entries below it (an imminent OOM, a permissions drift that stops every
    # pipeline at startup) inherit that discount.
    #
    # A downward shift is a regression and still escalates on confidence. An
    # upward shift is information — worth surfacing, never worth paging.
    if cp.direction == "up":
        severity = "warning"
    else:
        severity = "warning" if cp.confidence < 0.9 else "critical"
    msg = (
        f"CUSUM detected {direction_label} shift in reward for "
        f"{niche_id}/{platform} at index {cp.at_index} of 30d series "
        f"(magnitude {cp.magnitude:.2f}σ, confidence {cp.confidence:.2f})"
    )
    try:
        existing = conn.execute(
            """
            SELECT 1 FROM pipeline_alerts
            WHERE check_name = %s AND niche_id = %s
              AND resolved_at IS NULL
            LIMIT 1
            """,
            (check_name, niche_id),
        ).fetchone()
        if existing:
            return
        conn.execute(
            """
            INSERT INTO pipeline_alerts
              (niche_id, check_name, severity, message, details)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                niche_id, check_name, severity, msg,
                json.dumps({
                    "direction": cp.direction,
                    "at_index": cp.at_index,
                    "magnitude": cp.magnitude,
                    "confidence": cp.confidence,
                }),
            ),
        )
        logger.info(
            "[change_point] emitted %s alert for %s/%s",
            severity, niche_id, platform,
        )
    except Exception as exc:
        logger.warning("[change_point] alert emit failed: %s", exc)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    import psycopg
    from psycopg.rows import dict_row

    from genlab_core.monitoring.change_point import detect_change_point

    counts = {"scanned": 0, "detected_up": 0, "detected_down": 0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche in ACTIVE_NICHES:
            for platform in IN_SCOPE_PLATFORMS:
                counts["scanned"] += 1
                series = _daily_reward_series(conn, niche, platform)
                cp = detect_change_point(series)
                if cp is None:
                    continue
                counts[f"detected_{cp.direction}"] += 1
                if args.dry_run:
                    print(
                        f"  {niche:12s} {platform:10s} → {cp.direction.upper()} "
                        f"at t={cp.at_index} mag={cp.magnitude:.2f}σ "
                        f"conf={cp.confidence:.2f}"
                    )
                    continue
                _emit_alert(conn, niche, platform, cp)
        if not args.dry_run:
            conn.commit()

    logger.info(
        "change_point: done — scanned=%d up=%d down=%d",
        counts["scanned"], counts["detected_up"], counts["detected_down"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
