"""Per-niche per-platform publishing-health aggregator (PR B, 2026-06-27).

Foundation for the Mission Control PerPlatformHealthCard. The existing
``/api/v1/metrics/publishing`` endpoint aggregates ACROSS niches × platforms,
so a single niche × platform pair that's been silently failing for days
(e.g. ai_creators Instagram failed 3 times in the last 7 days — see today's
investigation that surfaced error code 2207077) is invisible at-a-glance:
its noise gets averaged into the global per-platform rate.

This module computes the fine-grained matrix the global query throws away:
one row per (niche, platform), with 14d success rate, last-success/last-
failure timestamps, and the most-recent failure error_message. The card
colors each cell red/yellow/green per the thresholds in ``THRESHOLDS``.

## Why a separate query, not just a bigger ``metrics.py`` grouping

  * Cardinality is small (5 niches × ~6 platforms = ~30 rows). One query
    fits comfortably in the dashboard's 60s polling budget.
  * The shape (matrix-style for the card, not flat-trend for metrics.py)
    diverges enough that branching the existing query into both shapes
    would be more spaghetti than worth.
  * Lets ``metrics.py`` keep its single-purpose contract (publish-rate +
    daily trend) without growing a per-(niche, platform) responsibility.

## SKIPPED handling

  Matches the post-2026-06-25-audit fix in ``metrics.py``: SKIPPED
  records (CREDENTIAL-class failures like YT token expired) are EXCLUDED
  from this card's success-rate denominator. The card is specifically a
  "publish-attempt success rate" view — SKIPPED isn't a publish attempt,
  it's a deliberate "we didn't try because creds were rot." The global
  metrics card already surfaces SKIPPED counts separately; per-niche
  per-platform is the wrong place to re-litigate that decision.

  Future revision: a fourth color (grey/blue) for "all SKIPPED, no attempts"
  may help when an operator wonders why a cell is "—". Today's threshold
  pings the cell as insufficient-data which is correct enough for v1.

## Insufficient-data threshold

  ``MIN_SAMPLES_FOR_RATE = 3`` — fewer than 3 publish attempts in the
  window → ``success_rate_pct=None``. With our 1-per-day-per-channel cap,
  3 publishes is ~3 days of activity per platform. Anything less is too
  noisy for red/yellow/green to be meaningful (1/1 = 100% would falsely
  show green for a platform that just happened to ship once).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# Per-cell color thresholds, surfaced in the API response so the
# frontend doesn't hardcode them. If we ever tune these (e.g. relax
# yellow to 70% for a chattier niche), the card picks it up via API.
THRESHOLDS = {"red_below_pct": 50, "yellow_below_pct": 80}

# Below this sample count, the cell renders as insufficient-data ("—").
# See module docstring for rationale.
MIN_SAMPLES_FOR_RATE = 3


def fetch_per_niche_platform_health(*, days: int = 14) -> list[dict[str, Any]]:
    """Query Postgres for per-(niche, platform) publishing-health rows.

    Args:
      days: lookback window in days. Clamped [1, 90] defensively.

    Returns:
      List of row dicts. Each row:
        {
          niche_id: str,
          platform: str,
          success_count: int,
          failed_count: int,
          success_rate_pct: float | None,   # None if <3 samples
          last_success_at: str | None,       # ISO-8601 or None
          last_failure_at: str | None,
          last_failure_error: str | None,    # full error_message
        }

    Fail-OPEN: on DB error or no DSN, returns ``[]``. The card renders
    its zero-state ("Insufficient data" everywhere) rather than vanishing
    or erroring out — same convention as ``stats_by_niche`` in
    ``genlab_core.compliance.events``.
    """
    # Defence: clamp window. The endpoint also clamps but the library
    # function should be safe to call directly from a script too.
    days = max(1, min(90, int(days)))

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("DATABASE_URL not configured — returning empty per-niche health")
        return []

    try:
        # Note: we intentionally use a parameterised %s placeholder for
        # the days value AND for the days arg to the subquery, even
        # though they're the same value. psycopg requires every
        # placeholder to be supplied separately.
        #
        # INSIGHTS_24H / INSIGHTS_48H / INSIGHTS_168H are lifecycle-mutated
        # SUCCESS rows from the metric_collector — once a publish ages past
        # the 24h insight window, its status column flips from SUCCESS to
        # INSIGHTS_24H, then INSIGHTS_48H at 48h, then INSIGHTS_168H at 7d.
        # Treating those as not-success would understate success rates
        # ~5× for any platform measured >24h after publish: e.g. anime
        # YouTube last 60d was 5 FAILED + 15 INSIGHTS_* (all originally
        # SUCCESS) and the card was showing 0% instead of ~75%.
        # See ``docs/publishing_analytics_status_taxonomy.md`` /
        # PR D (2026-06-27) for the full lifecycle. SKIPPED is still
        # excluded — see module docstring for rationale.
        sql = """
            SELECT
                niche_id,
                platform,
                COUNT(*) FILTER (WHERE status='SUCCESS' OR status LIKE 'INSIGHTS_%%')        AS success_count,
                COUNT(*) FILTER (WHERE status='FAILED')                                       AS failed_count,
                MAX(created_at) FILTER (WHERE status='SUCCESS' OR status LIKE 'INSIGHTS_%%') AS last_success_at,
                MAX(created_at) FILTER (WHERE status='FAILED')                                AS last_failure_at,
                (
                    SELECT p2.error_message
                    FROM publishing_analytics p2
                    WHERE p2.niche_id = p.niche_id
                      AND p2.platform = p.platform
                      AND p2.status = 'FAILED'
                      AND p2.created_at >= NOW() - make_interval(days => %s)
                    ORDER BY p2.created_at DESC
                    LIMIT 1
                ) AS last_failure_error
            FROM publishing_analytics p
            WHERE (status IN ('SUCCESS', 'FAILED') OR status LIKE 'INSIGHTS_%%')
              AND created_at >= NOW() - make_interval(days => %s)
            GROUP BY niche_id, platform
            ORDER BY niche_id, platform
        """

        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            # RLS bypass — we want the full matrix, not one niche's slice.
            # Endpoint layer applies SR-F post-filter for restricted ops.
            conn.execute("SELECT set_config('app.niche_id', 'all', true)")
            rows = conn.execute(sql, (days, days)).fetchall()
    except Exception as exc:  # pragma: no cover — defensive fail-OPEN
        logger.error("per-niche-platform health query failed: %s", exc, exc_info=True)
        return []

    result: list[dict[str, Any]] = []
    for r in rows:
        success = int(r["success_count"] or 0)
        failed = int(r["failed_count"] or 0)
        total = success + failed
        if total < MIN_SAMPLES_FOR_RATE:
            # Insufficient data — rate is meaningless with 1-2 samples.
            # Card renders "—" with greyed cell so operator knows the
            # combo isn't "0%" (which would be alarming red).
            success_rate_pct: float | None = None
        else:
            # Round to 1dp — operator doesn't need 4 digits of false
            # precision on a 14-day count.
            success_rate_pct = round(100.0 * success / total, 1)

        last_success_at = r["last_success_at"]
        last_failure_at = r["last_failure_at"]
        result.append(
            {
                "niche_id": r["niche_id"],
                "platform": r["platform"],
                "success_count": success,
                "failed_count": failed,
                "success_rate_pct": success_rate_pct,
                "last_success_at": last_success_at.isoformat() if last_success_at else None,
                "last_failure_at": last_failure_at.isoformat() if last_failure_at else None,
                # error_message returned in full; frontend truncates for tooltip
                "last_failure_error": r["last_failure_error"],
            }
        )

    return result
