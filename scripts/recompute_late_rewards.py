#!/usr/bin/env python3
"""Recompute late-window reward for blueprints published 6-8 days ago.

Wired via systemd timer at 04:00 UTC daily. Iterates blueprints in the
6-8-day-ago window, fetches their extended-window metrics, computes
delta vs. stored reward_48h, and persists to late_reward_deltas audit
table.

By default: measurement only (fail-closed telemetry). Setting
GENLAB_MULTI_WINDOW_REWARD_ENABLED=true additionally pushes
significant-lift deltas (|delta_pct| > 20%) into the bandit posterior.

Safe rollout path (per Intervention 1 in the research doc):
  Week 1-2: run measurement-only, review distribution of delta_pct
    across niches / arms
  Week 3: if data supports it, flip the flag on ai_creators
  Week 4+: expand to other niches per operator judgment

Exit codes:
  0 — completed (any counts)
  1 — DATABASE_URL missing / DB unreachable
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days-ago-min",
        type=int,
        default=6,
        help="Lower bound of days-ago window (default: 6)",
    )
    parser.add_argument(
        "--days-ago-max",
        type=int,
        default=8,
        help="Upper bound of days-ago window (default: 8)",
    )
    parser.add_argument(
        "--force-push",
        action="store_true",
        help="Force bandit push regardless of feature flag (dev use only).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("recompute_late_rewards")

    from genlab_core.learning.late_reward import (
        _integration_enabled,
        process_late_reward_batch,
    )

    push = True if args.force_push else _integration_enabled()
    logger.info(
        "Starting: days_ago=%d-%d push_to_bandit=%s",
        args.days_ago_min,
        args.days_ago_max,
        push,
    )
    counters = process_late_reward_batch(
        days_ago_min=args.days_ago_min,
        days_ago_max=args.days_ago_max,
        push_to_bandit=push,
    )
    logger.info("Complete: %s", counters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
