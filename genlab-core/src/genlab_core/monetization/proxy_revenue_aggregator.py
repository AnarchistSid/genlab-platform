"""Daily aggregator: write proxy revenue rows into ``affiliate_revenue``.

The flow this closes (audit R-32):

  click → log_click() → affiliate_clicks                       (R-23, done)
                                  ↓
                  THIS MODULE (aggregate yesterday's clicks
                  per niche/network, estimate revenue via
                  the shared YAML constants, UPSERT into
                  ``affiliate_revenue`` with source=click_proxy)
                                  ↓
       affiliate_revenue                                       (now populated)
                                  ↓
       dashboard "actual revenue" column lights up
       metric_collector / reward_shaper can read it for the
       monetization bonus signal

The rows we write are tagged ``extra->>'source' = 'click_proxy'`` so
future real-revenue ingestion (Amazon report API, manual import) can
co-exist alongside — and supersede — the proxy values. Re-running for
the same date is idempotent: the proxy rows for that date are
DELETEd in the same transaction before fresh ones are INSERTed.

CLI:
  python -m genlab_core.monetization.proxy_revenue_aggregator
  python -m genlab_core.monetization.proxy_revenue_aggregator --date 2026-06-08
  python -m genlab_core.monetization.proxy_revenue_aggregator --days 7

Typical cron: daily at 04:00 UTC (after midnight in IST, well before
the 06:30 publish window) covering ``yesterday``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row

from genlab_core.monetization.affiliate_economics import get_affiliate_economics
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-3

logger = logging.getLogger(__name__)

# We tag rows we write so they're distinguishable from real revenue.
PROXY_SOURCE = "click_proxy"


def aggregate_proxy_revenue(
    report_date: date,
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Aggregate a single day's clicks into proxy revenue rows.

    Args:
        report_date: the calendar date to aggregate (e.g. ``date.today() -
            timedelta(days=1)`` for yesterday).
        dsn: Postgres connection string. Defaults to ``DATABASE_URL`` env
            var, then ``dbname=genlab`` (the local-dev fallback used
            elsewhere in this package).

    Returns:
        Dict with ``rows_deleted`` (prior proxy rows for the date),
        ``rows_inserted`` (new proxy rows), and ``total_revenue_inr``
        for logging/observability.
    """
    economics = get_affiliate_economics()
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"

    with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn, conn.transaction():
        # Pull yesterday's clicks bucketed by (niche, network). blueprint_id
        # could be added to the GROUP BY but the dashboard's existing
        # estimate doesn't bucket on it, so we match its shape.
        cur = conn.cursor()
        cur.execute(
            """
            SELECT niche_id, network, COUNT(*) AS clicks
              FROM affiliate_clicks
             WHERE created_at::date = %s
             GROUP BY niche_id, network
            """,
            (report_date,),
        )
        click_rows = cur.fetchall()

        # Idempotency: blow away any prior proxy rows for this date so a
        # re-run produces the same final state. Real-revenue rows
        # (source != 'click_proxy') are preserved.
        cur.execute(
            """
            DELETE FROM affiliate_revenue
             WHERE date = %s
               AND extra->>'source' = %s
            """,
            (report_date, PROXY_SOURCE),
        )
        rows_deleted = cur.rowcount

        # Insert fresh proxy rows.
        rows_inserted = 0
        total_revenue = 0.0
        for r in click_rows:
            niche = r["niche_id"] or "unknown"
            network = r["network"] or "unknown"
            clicks = int(r["clicks"] or 0)
            revenue = round(
                economics.estimate_revenue(niche_id=niche, network=network, clicks=clicks),
                2,
            )
            # Conversions are estimated from the same conversion_rate;
            # we round to nearest int so the dashboard's
            # SUM(conversions) doesn't display fractional people.
            conversions = round(clicks * economics.conversion_rate)
            cur.execute(
                """
                INSERT INTO affiliate_revenue
                       (niche_id, network, revenue_amount, currency,
                        clicks, conversions, date, extra)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    niche,
                    network,
                    revenue,
                    economics.default_currency,
                    clicks,
                    conversions,
                    report_date,
                    _proxy_extra_payload(),
                ),
            )
            rows_inserted += 1
            total_revenue += revenue

        logger.info(
            "[proxy_revenue] %s: deleted=%d inserted=%d total=%.2f %s",
            report_date,
            rows_deleted,
            rows_inserted,
            total_revenue,
            economics.default_currency,
        )

    return {
        "report_date": str(report_date),
        "rows_deleted": rows_deleted,
        "rows_inserted": rows_inserted,
        "total_revenue_inr": round(total_revenue, 2),
    }


def aggregate_window(
    *,
    start_date: date,
    end_date: date,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Run :func:`aggregate_proxy_revenue` for each day in ``[start, end]``
    inclusive. Used for backfills."""
    results = []
    d = start_date
    while d <= end_date:
        results.append(aggregate_proxy_revenue(d, dsn=dsn))
        d += timedelta(days=1)
    return results


def _proxy_extra_payload() -> str:
    """The JSONB payload we stamp on proxy rows.

    Carries the source tag (so real-revenue rows are distinguishable)
    plus the timestamp the proxy was computed at (helps when debugging
    "why does this day look stale").
    """
    import json

    return json.dumps(
        {
            "source": PROXY_SOURCE,
            "computed_at": datetime.now(UTC).isoformat(),
        }
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m genlab_core.monetization.proxy_revenue_aggregator",
        description=(
            "Aggregate affiliate_clicks into proxy revenue rows in "
            "affiliate_revenue. Default: yesterday."
        ),
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--date",
        help="Single date to aggregate (YYYY-MM-DD). Defaults to yesterday.",
    )
    g.add_argument(
        "--days",
        type=int,
        help=("Backfill: aggregate the last N days ending yesterday (useful after first install)."),
    )
    p.add_argument(
        "--dsn",
        help="Override DATABASE_URL.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    yesterday = date.today() - timedelta(days=1)
    if args.days and args.days > 0:
        start = yesterday - timedelta(days=args.days - 1)
        results = aggregate_window(start_date=start, end_date=yesterday, dsn=args.dsn)
        for r in results:
            print(
                f"{r['report_date']}: deleted={r['rows_deleted']} "
                f"inserted={r['rows_inserted']} total={r['total_revenue_inr']} INR"
            )
        return 0
    target = date.fromisoformat(args.date) if args.date else yesterday
    result = aggregate_proxy_revenue(target, dsn=args.dsn)
    print(
        f"{result['report_date']}: deleted={result['rows_deleted']} "
        f"inserted={result['rows_inserted']} total={result['total_revenue_inr']} INR"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(main())
