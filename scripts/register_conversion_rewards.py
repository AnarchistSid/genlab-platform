#!/usr/bin/env python3
"""Register real-revenue reward on product arms (L3 PR 11, 2026-07-07).

The escalation from L3 PR 8's click-only reward to Bernoulli
purchase-signal reward. Reads affiliate_clicks + affiliate_conversions
per (niche_id, product_slug) and updates bandit_arms:

    alpha = 1.0 + n_purchases       (when conversions exist)
          or 1.0 + n_clicks         (click-only fallback)

    beta  = 1.0 + max(0, n_clicks - n_purchases)   (Bernoulli)
          or 1.0                                    (click-only)

## Why Bernoulli vs click-only

Beta bandits converge to meaningful mid-range posteriors when they see
BOTH success + failure events. L3 PR 8 shipped click-only (all events
map to reward=1, β stays at 1.0, posterior mean asymptotes to 1.0).
That distinguishes "products with clicks" from "products without"
but doesn't distinguish "10 clicks 8 sales" from "10 clicks 1 sale".

With conversion data, we now get a real Bernoulli signal per product:

* Each click is a Bernoulli trial (click occurred)
* Each purchase (items_shipped - items_returned) is a success
* Each click WITHOUT a purchase is a failure

Posterior mean = expected purchase rate | click. This is the number
the ProductSelector's value_weight multiplies against to get expected
revenue per surface. A product with 20% conversion rate outranks one
with 5% conversion at the same price × commission.

## Fallback semantics

When ``affiliate_conversions`` has NO rows for a product_slug (either
no purchases YET or ASIN not mapped to the catalog), the runner falls
back to L3 PR 8's click-only formula. This is critical for rollout:

* Day 1 after L3 PR 11 lands: zero conversions, EVERY arm falls back
  to click-only. Same behavior as L3 PR 8 — zero risk of surprising
  the operator.
* Once the operator uploads Amazon CSVs and conversions accumulate,
  specific arms upgrade to Bernoulli reward AS THE DATA ARRIVES.
* Arms without conversion data STAY on click-only indefinitely.
  That's fine — they were never in the "would use Bernoulli" set
  because there's nothing to compute the rate over.

## Idempotence by construction

Same as L3 PR 8: the formulas are pure recomputes from
``affiliate_clicks`` + ``affiliate_conversions`` truth. Rerunning
converges. If Amazon revises a previous day's numbers (they routinely
do 24-72h after the row's date to account for cancellations), the
next run picks up the new totals automatically because
``affiliate_conversions`` gets an updated hash from the re-imported
CSV.

## Deprecates (v2 of) L3 PR 8's runner

``register_click_rewards.py`` (L3 PR 8) does the same thing as this
runner MINUS conversion escalation. Both are safe to run — this
runner produces STRICTLY BETTER-INFORMED posteriors when conversion
data exists, and IDENTICAL posteriors when it doesn't.

**Operator migration path**: switch the systemd unit
``genlab-register-click-rewards.service`` to point at THIS script
(replacing the L3 PR 8 script name). Old script stays committed for
a release cycle in case rollback is needed.

## Exit codes

    0 — success (or no arms to update)
    1 — DATABASE_URL unset / connection failure
    2 — SQL execution failure
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("register_conversion_rewards")


# The core reward SQL. ONE UPDATE that handles both cases in a
# single query via a LEFT JOIN + CASE expression. When
# affiliate_conversions has data for a (niche, product_slug), the
# CASE uses the Bernoulli formula. Otherwise it falls back to L3
# PR 8's click-only semantics.
#
# Notes on the SQL:
# * The click subquery `cc` is the primary — no arm gets updated
#   unless it has at least one click. An arm with no clicks stays
#   at Beta(1,1) uniform prior (correct — we have no observation).
# * The conversion subquery `cv` is LEFT JOIN'd because most
#   products won't have conversion data yet at rollout.
# * SUM(GREATEST(0, items_shipped - items_returned)) handles the
#   return case: a shipment that gets returned isn't a purchase.
#   The GREATEST(0, ...) prevents negative counts if Amazon's
#   report has more returns than ships in a window (rare but
#   documented; happens across month boundaries).
# * COALESCE picks n_purchases when non-null, else n_clicks.
# * CASE handles beta differently in the two branches.
_REWARD_SQL = """
    UPDATE bandit_arms ba SET
        alpha  = 1.0 + COALESCE(cv.n_purchases, cc.n_clicks),
        beta   = CASE
            WHEN cv.n_purchases IS NULL THEN 1.0
            ELSE 1.0 + GREATEST(0, cc.n_clicks - cv.n_purchases)
        END,
        n_plays = cc.n_clicks,
        updated_at = NOW()
    FROM (
        SELECT niche_id, product_id, COUNT(*) AS n_clicks
        FROM affiliate_clicks
        WHERE product_id IS NOT NULL
          AND product_id != ''
        GROUP BY niche_id, product_id
    ) cc
    LEFT JOIN (
        SELECT niche_id, product_slug,
               SUM(GREATEST(0, items_shipped - items_returned)) AS n_purchases
        FROM affiliate_conversions
        WHERE product_slug IS NOT NULL
        GROUP BY niche_id, product_slug
    ) cv ON cv.niche_id = cc.niche_id
        AND cv.product_slug = cc.product_id
    WHERE ba.arm_type = 'product'
      AND ba.niche_id = cc.niche_id
      AND ba.arm_id   = 'product__' || cc.product_id
    RETURNING ba.niche_id, ba.arm_id, ba.n_plays, ba.alpha, ba.beta,
              cv.n_purchases;
"""


# Preview SQL — same JOIN + CASE, but SELECT-only for dry-run mode.
_PREVIEW_SQL = """
    SELECT ba.niche_id, ba.arm_id,
           ba.n_plays        AS current_n_plays,
           cc.n_clicks       AS would_set_n_plays,
           ba.alpha          AS current_alpha,
           1.0 + COALESCE(cv.n_purchases, cc.n_clicks) AS would_set_alpha,
           ba.beta           AS current_beta,
           CASE
               WHEN cv.n_purchases IS NULL THEN 1.0
               ELSE 1.0 + GREATEST(0, cc.n_clicks - cv.n_purchases)
           END               AS would_set_beta,
           cv.n_purchases,
           cc.n_clicks
    FROM bandit_arms ba
    JOIN (
        SELECT niche_id, product_id, COUNT(*) AS n_clicks
        FROM affiliate_clicks
        WHERE product_id IS NOT NULL
          AND product_id != ''
        GROUP BY niche_id, product_id
    ) cc ON ba.niche_id = cc.niche_id
        AND ba.arm_id   = 'product__' || cc.product_id
    LEFT JOIN (
        SELECT niche_id, product_slug,
               SUM(GREATEST(0, items_shipped - items_returned)) AS n_purchases
        FROM affiliate_conversions
        WHERE product_slug IS NOT NULL
        GROUP BY niche_id, product_slug
    ) cv ON cv.niche_id = cc.niche_id
        AND cv.product_slug = cc.product_id
    WHERE ba.arm_type = 'product'
    ORDER BY cc.n_clicks DESC, ba.niche_id, ba.arm_id;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the UPDATE (default is dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without writing.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 1

    try:
        import psycopg
    except ImportError:
        logger.error("psycopg not installed")
        return 1

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                if not apply:
                    cur.execute(_PREVIEW_SQL)
                    rows = cur.fetchall()
                    if not rows:
                        logger.info("No click-attributed arms to update.")
                        return 0

                    n_bernoulli = sum(1 for r in rows if r[7] is not None)
                    n_click_only = len(rows) - n_bernoulli
                    logger.info(
                        "[DRY-RUN] %d arm(s) would be updated. "
                        "Bernoulli: %d, click-only fallback: %d. "
                        "Top 20 by click count:",
                        len(rows),
                        n_bernoulli,
                        n_click_only,
                    )
                    for row in rows[:20]:
                        (
                            niche,
                            arm_id,
                            cur_n,
                            would_n,
                            cur_a,
                            would_a,
                            cur_b,
                            would_b,
                            n_purchases,
                            n_clicks,
                        ) = row
                        mode = "BERNOULLI" if n_purchases is not None else "click-only"
                        logger.info(
                            "  %-12s %-45s n_plays=%d→%d  α=%.2f→%.2f  β=%.2f→%.2f  "
                            "[%s clicks=%s purchases=%s]",
                            niche,
                            arm_id,
                            cur_n or 0,
                            would_n,
                            float(cur_a or 1.0),
                            float(would_a),
                            float(cur_b or 1.0),
                            float(would_b),
                            mode,
                            n_clicks,
                            n_purchases if n_purchases is not None else "-",
                        )
                    logger.info("[DRY-RUN] Re-run with --apply to execute.")
                    return 0

                # Live UPDATE
                cur.execute(_REWARD_SQL)
                updated = cur.fetchall()
                conn.commit()

                if not updated:
                    logger.info("No click-attributed arms to update.")
                    return 0

                n_bernoulli = sum(1 for r in updated if r[5] is not None)
                n_click_only = len(updated) - n_bernoulli
                logger.info(
                    "Updated %d arm(s) from click observations "
                    "(Bernoulli: %d, click-only fallback: %d)",
                    len(updated),
                    n_bernoulli,
                    n_click_only,
                )
                top = sorted(updated, key=lambda r: r[2] or 0, reverse=True)[:10]
                for niche, arm_id, n_plays, alpha, beta, n_purchases in top:
                    mode = "BERN" if n_purchases is not None else "clk "
                    logger.info(
                        "  %s %s %-45s n=%d α=%.2f β=%.2f p=%s",
                        mode,
                        niche,
                        arm_id,
                        n_plays,
                        float(alpha),
                        float(beta),
                        n_purchases if n_purchases is not None else "-",
                    )
    except Exception as exc:  # noqa: BLE001
        logger.error("SQL execution failed: %s", exc)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
