#!/usr/bin/env python3
"""Phase 2.C — SLO forecast runner.

Fires hourly via genlab-slo-forecast.timer. Steps:

  1. For each SLO check_name we track, query pipeline_alerts over
     last 14 days grouped by day.
  2. Compute EWMA-smoothed trend + 24h-ahead forecast.
  3. Write forecast state to `slo_forecasts` table (keyed by
     (check_name, niche_id)).
  4. If verdict = 'forecast_warning' or 'forecast_critical', also
     write a `forecast:{check_name}` row to pipeline_alerts so
     the operator sees it on the CriticalAlertsBanner.

Fail-open at every step. Never blocks other services.

## Usage

    uv run python scripts/run_slo_forecast.py
    uv run python scripts/run_slo_forecast.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger("run_slo_forecast")

# Which SLO check_names to forecast. Add as new checks ship.
TRACKED_CHECKS = (
    "zero_blueprints",
    "slo:zero_blueprints",
    "slo:p95_pipeline",
    "slo:other",
    "download_failures",
    "publish_silence",
    "content_gap",
    "yt_cookies_stale",
    "source_diversity_collapsed",
    "hook_training_failed",
    "strategist_llm_call_failed",
    "strategist_validation_failed",
)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args(argv)


def _forecast_for_check(conn, check_name: str, niche_id: str | None):
    from genlab_core.monitoring.slo_forecast import (
        bucket_by_day, compute_forecast,
    )
    try:
        if niche_id:
            rows = conn.execute(
                """
                SELECT created_at FROM pipeline_alerts
                WHERE check_name = %s AND niche_id = %s
                  AND created_at >= NOW() - INTERVAL '14 days'
                ORDER BY created_at
                """,
                (check_name, niche_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT created_at FROM pipeline_alerts
                WHERE check_name = %s
                  AND created_at >= NOW() - INTERVAL '14 days'
                ORDER BY created_at
                """,
                (check_name,),
            ).fetchall()
    except Exception as exc:
        logger.debug("[slo_forecast] query failed for %s: %s", check_name, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    events = [
        r.get("created_at") if hasattr(r, "get") else r[0] for r in rows
    ]
    daily_counts = bucket_by_day(events)
    return compute_forecast(check_name, daily_counts, niche_id=niche_id)


def _persist_forecast(conn, f) -> None:
    """Write to slo_forecasts table (upsert on
    (check_name, niche_id))."""
    conn.execute(
        """
        INSERT INTO slo_forecasts (
          check_name, niche_id, current_rate, forecast_rate,
          trend_pct, verdict, ttb_hours, computed_at
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (check_name, niche_id) DO UPDATE SET
          current_rate = EXCLUDED.current_rate,
          forecast_rate = EXCLUDED.forecast_rate,
          trend_pct = EXCLUDED.trend_pct,
          verdict = EXCLUDED.verdict,
          ttb_hours = EXCLUDED.ttb_hours,
          computed_at = EXCLUDED.computed_at
        """,
        (
            f.check_name, f.niche_id or "", f.current_rate,
            f.forecast_rate, f.trend_pct, f.verdict,
            f.ttb_hours,
        ),
    )


def _emit_forecast_alert(conn, f) -> None:
    """Write a `forecast:{check_name}` pipeline_alerts row so
    operator sees the projection on the standard alerts banner.
    Deduped per (check_name, niche_id)."""
    fc_check = f"forecast:{f.check_name}"
    severity = "critical" if f.verdict == "forecast_critical" else "warning"
    msg = (
        f"SLO forecast for {f.check_name}: current={f.current_rate:.2f}/day, "
        f"projected {f.forecast_rate:.2f}/day in 24h "
        f"({f.trend_pct:+.0f}%). "
        + (
            f"Breach in ~{f.ttb_hours:.0f}h at current trend."
            if f.ttb_hours is not None else ""
        )
    )
    try:
        # Dedup: skip if an open forecast alert already exists
        existing = conn.execute(
            """
            SELECT 1 FROM pipeline_alerts
            WHERE check_name = %s
              AND COALESCE(niche_id, '') = %s
              AND resolved_at IS NULL
            LIMIT 1
            """,
            (fc_check, f.niche_id or ""),
        ).fetchone()
        if existing:
            return
        conn.execute(
            """
            INSERT INTO pipeline_alerts (
              niche_id, check_name, severity, message, details
            ) VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                f.niche_id or None, fc_check, severity, msg,
                json.dumps({
                    "current_rate": f.current_rate,
                    "forecast_rate": f.forecast_rate,
                    "trend_pct": f.trend_pct,
                    "ttb_hours": f.ttb_hours,
                }),
            ),
        )
        logger.info("[slo_forecast] emitted %s alert for %s", severity, fc_check)
    except Exception as exc:
        logger.warning("[slo_forecast] alert emit failed for %s: %s", fc_check, exc)


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

    stats = {"forecasts": 0, "warnings": 0, "criticals": 0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # First: system-wide (niche_id=None)
        for check_name in TRACKED_CHECKS:
            f = _forecast_for_check(conn, check_name, niche_id=None)
            if f is None or f.verdict == "insufficient_data":
                continue
            stats["forecasts"] += 1
            if args.dry_run:
                print(
                    f"  {check_name:35s} niche=all "
                    f"current={f.current_rate:.2f} "
                    f"forecast={f.forecast_rate:.2f} "
                    f"trend={f.trend_pct:+.0f}% verdict={f.verdict}"
                )
                continue
            _persist_forecast(conn, f)
            if f.verdict in ("forecast_warning", "forecast_critical"):
                _emit_forecast_alert(conn, f)
                stats["warnings" if f.verdict == "forecast_warning" else "criticals"] += 1

        # Then per-niche for niche-scoped checks
        for niche in ("ai_creators", "anime", "gaming", "movies", "sports"):
            for check_name in TRACKED_CHECKS:
                f = _forecast_for_check(conn, check_name, niche_id=niche)
                if f is None or f.verdict == "insufficient_data":
                    continue
                stats["forecasts"] += 1
                if args.dry_run:
                    print(
                        f"  {check_name:35s} niche={niche:12s} "
                        f"current={f.current_rate:.2f} "
                        f"forecast={f.forecast_rate:.2f} "
                        f"verdict={f.verdict}"
                    )
                    continue
                _persist_forecast(conn, f)
                if f.verdict in ("forecast_warning", "forecast_critical"):
                    _emit_forecast_alert(conn, f)
                    stats["warnings" if f.verdict == "forecast_warning" else "criticals"] += 1

        if not args.dry_run:
            conn.commit()

    logger.info(
        "slo_forecast: done — %d forecasts, %d warnings, %d criticals emitted",
        stats["forecasts"], stats["warnings"], stats["criticals"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
