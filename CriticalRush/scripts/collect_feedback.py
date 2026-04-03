#!/usr/bin/env python3
"""Standalone feedback collection script — run via cron.

Processes all pending feedback records whose time windows have elapsed,
fetches platform analytics, computes reward scores, and updates the
Thompson Sampling bandit.

Recommended cron schedule: every 2 hours.
    0 */2 * * * cd /path/to/CriticalRush && venv/bin/python scripts/collect_feedback.py

Exit codes:
    0 — success (0 or more records processed)
    1 — fatal error
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("collect_feedback")


def main() -> int:
    # Source .env files if DATABASE_URL not already set
    import os
    if not os.environ.get("DATABASE_URL"):
        try:
            from dotenv import load_dotenv
            genlab_root = Path(__file__).resolve().parents[2]
            load_dotenv(genlab_root / ".env", override=False)
        except ImportError:
            pass

    if not os.environ.get("DATABASE_URL"):
        logger.warning("DATABASE_URL not set — nothing to collect")
        return 0

    try:
        from genlab_core.learning.metric_collector import (
            _default_bandit_updater,
            collect_metrics,
        )
    except ImportError:
        logger.error("genlab_core.learning.metric_collector not available")
        return 1

    results = collect_metrics(bandit_updater=_default_bandit_updater)
    logger.info("Metric collection complete: %s", results)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)
