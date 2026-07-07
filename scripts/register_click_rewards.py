#!/usr/bin/env python3
"""Register affiliate-click rewards on product arms (L3 PR 8).

The final piece of the Monetization Layer 3 reward loop:

    click → attributed → product bandit arm reward → selector uses signal

Runs periodically (daily cron via systemd — see the .timer sibling to
this script for the schedule). For every registered product arm
(``arm_type='product'``), sets its posterior to reflect the cumulative
click count observed against that arm's slug:

    alpha  = 1.0 + n_clicks
    beta   = 1.0
    n_plays = n_clicks

## Why click = success (v1)

Beta bandits need BOTH success + failure events to converge to a
meaningful mid-range posterior. In v1 we only have click events —
we can't observe an "absence of a click" as a discrete event, so we
can't produce β updates. All clicks map to reward=1; posterior mean
asymptotes to 1.0 for products with any click volume.

This is a REASONABLE ranking signal:

* Products with clicks rank strictly above products without clicks.
* The number of clicks doesn't strongly differentiate (10 clicks and
  100 clicks both → posterior ≈ 1.0). That's what
  :func:`_value_weight` in ProductSelector handles instead.

**L3 PR 11** (real-revenue reward escalation) fixes this: once the
Amazon Associates Report API is wired, purchases become the success
event and clicks-without-purchase become the failure event. That's
when the bandit gets a real Bernoulli signal to converge on.

## Idempotence by construction

The formulas above are computed FROM ``affiliate_clicks`` truth.
Rerunning the script recomputes the same posterior for the same
click history — no "already processed" marker needed.

If a click gets deleted (e.g., anti-fraud sweep), the next run
naturally regresses the arm's posterior. If new clicks arrive
between runs, the next run picks them up. Zero coordination
overhead with click-tracking side.

## The JOIN key alignment (load-bearing)

The SQL below matches on:

    ba.arm_id = 'product__' || cc.product_id

* ``ba.arm_id``          — registered by L3 PR 3 (``product__<slug>``)
* ``cc.product_id``      — slug written by bio-link hub click handler
                           (L3 PR 7's ``sanitize_blueprint_id`` guard
                           doesn't touch product_id — the slug is
                           still a plain lowercase-hyphen string)
* ``blueprints.product_slug`` — written by L3 PR 2

All three converge on the same slug via
:func:`slugify_product_name`. Any drift here silently degrades
attribution.

## Usage

Dry-run (preview counts, no writes):

    python scripts/register_click_rewards.py --dry-run

Apply (production):

    python scripts/register_click_rewards.py --apply

Exit codes:
    0 — success (or nothing to do)
    1 — DATABASE_URL unset / connection failure
    2 — SQL execution failure
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("register_click_rewards")


# The core reward SQL. One UPDATE, uses an in-flight CTE to compute
# per-(niche, product) click counts then joins onto bandit_arms via
# the composite arm_id.
_REWARD_SQL = """
    UPDATE bandit_arms ba SET
        alpha  = 1.0 + cc.n_clicks,
        beta   = 1.0,
        n_plays = cc.n_clicks,
        updated_at = NOW()
    FROM (
        SELECT niche_id, product_id, COUNT(*) AS n_clicks
        FROM affiliate_clicks
        WHERE product_id IS NOT NULL
          AND product_id != ''
        GROUP BY niche_id, product_id
    ) cc
    WHERE ba.arm_type = 'product'
      AND ba.niche_id = cc.niche_id
      AND ba.arm_id   = 'product__' || cc.product_id
    RETURNING ba.niche_id, ba.arm_id, ba.n_plays;
"""


# Preview SQL — the same JOIN but returns rows without writing.
_PREVIEW_SQL = """
    SELECT ba.niche_id, ba.arm_id,
           ba.n_plays        AS current_n_plays,
           cc.n_clicks       AS would_set_n_plays,
           ba.alpha          AS current_alpha,
           1.0 + cc.n_clicks AS would_set_alpha
    FROM bandit_arms ba
    JOIN (
        SELECT niche_id, product_id, COUNT(*) AS n_clicks
        FROM affiliate_clicks
        WHERE product_id IS NOT NULL
          AND product_id != ''
        GROUP BY niche_id, product_id
    ) cc ON ba.niche_id = cc.niche_id
        AND ba.arm_id   = 'product__' || cc.product_id
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
        help="Preview counts without writing. (Default when --apply is absent.)",
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
                    logger.info(
                        "[DRY-RUN] %d arm(s) would be updated. Top 20 by click count:",
                        len(rows),
                    )
                    for row in rows[:20]:
                        niche, arm_id, cur_n, would_n, cur_a, would_a = row
                        logger.info(
                            "  %s  %-50s  n_plays: %d → %d  alpha: %.2f → %.2f",
                            niche,
                            arm_id,
                            cur_n or 0,
                            would_n,
                            float(cur_a or 1.0),
                            float(would_a),
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

                logger.info("Updated %d arm(s) from click observations", len(updated))
                # Top-10 by n_plays for quick visual verification.
                top = sorted(updated, key=lambda r: r[2] or 0, reverse=True)[:10]
                for niche, arm_id, n_plays in top:
                    logger.info("  %s  %-50s  n_plays=%d", niche, arm_id, n_plays)

    except Exception as exc:  # noqa: BLE001
        logger.error("SQL execution failed: %s", exc)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
