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
  0 — completed successfully (any counts, including scanned=0)
  2 — high error rate (>50% of scanned blueprints hit an error path).
      Distinguishes "runner ran but its work failed systematically" from
      "runner exited SUCCESS". Wired via systemd OnFailure= to
      systemd-failure-alert@%n.service so the operator sees CRITICAL
      alerts on the Mission Control dashboard.

      Added 2026-07-02 after a 7-week SQL bug (see
      [[late-reward-sql-bug-2026-07-02]]) had every blueprint error
      out silently while the runner exited 0 — systemd saw SUCCESS,
      no alert fired, no telemetry produced.
"""

from __future__ import annotations

import argparse
import logging
import sys

# Health-check tuning knobs — module-level so tests can monkey-patch
# without duplicating the logic below in main().
_MIN_SCANNED_FOR_HEALTHCHECK = 3
_MAX_ERROR_RATE = 0.5
_EXIT_HIGH_ERROR_RATE = 2


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

    # 2026-07-02 hardening — see [[late-reward-sql-bug-2026-07-02]].
    #
    # If we scanned >= _MIN_SCANNED_FOR_HEALTHCHECK blueprints and
    # more than _MAX_ERROR_RATE of them errored, exit nonzero so
    # systemd's OnFailure= fires the CRITICAL alert. Without this,
    # a systematic bug (bad SQL, network flake, permission drift)
    # can silently produce zero telemetry for weeks — because the
    # runner's own success path counts errors WITHIN the loop but
    # exits 0 unconditionally.
    #
    # Threshold rationale:
    #   * min_scanned=3 avoids flapping on days with tiny samples
    #     (empty window, weekend traffic dip, etc)
    #   * max_rate=0.5 is aggressive but appropriate: at 50%+ error
    #     rate the runner isn't producing useful telemetry — the
    #     operator should investigate BEFORE the next 24h fire.
    scanned = int(counters.get("scanned", 0) or 0)
    errors = int(counters.get("errors", 0) or 0)
    if scanned >= _MIN_SCANNED_FOR_HEALTHCHECK and errors / scanned > _MAX_ERROR_RATE:
        logger.error(
            "High error rate: %d/%d blueprints failed (>%.0f%% threshold). "
            "See WARN lines above for individual failures. Exiting %d so "
            "systemd OnFailure alert fires.",
            errors,
            scanned,
            _MAX_ERROR_RATE * 100,
            _EXIT_HIGH_ERROR_RATE,
        )
        return _EXIT_HIGH_ERROR_RATE
    return 0


if __name__ == "__main__":
    sys.exit(main())
