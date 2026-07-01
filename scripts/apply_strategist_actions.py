#!/usr/bin/env python3
"""Apply operator-accepted Strategist proposals — materialise side effects.

Runs via systemd timer (daily 03:00 UTC = 08:30 IST — after Sunday's
Strategist fire at 02:00 UTC + operator's typical morning review window).
Iterates over strategist_reports that have proposals_accepted set and
applies each accepted proposal's side effect (currently: arm_add creates
new bandit_arms rows; other types are read-path via strategy_phase).

Exit codes:
  0 — completed (any counts, including 0)
  1 — feature-flag off (silent no-op is intentional)
  2 — DATABASE_URL missing

Idempotent per proposal via strategist_reports.extra->applied_indices.

Usage:
    python -m scripts.apply_strategist_actions
    python -m scripts.apply_strategist_actions --niche ai_creators
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        help="Only apply actions for this niche (default: all).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("apply_strategist_actions")

    from genlab_core.scheduling.strategist_actions import (
        _integration_enabled,
        apply_pending_actions,
    )

    if not _integration_enabled():
        logger.info("Feature flag off; nothing to apply")
        return 0

    counters = apply_pending_actions(niche_id=args.niche)
    logger.info("Applied: %s", counters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
