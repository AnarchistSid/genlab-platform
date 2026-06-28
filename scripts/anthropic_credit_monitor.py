#!/usr/bin/env python3
"""CLI wrapper for ``anthropic_credit_monitor.scan_and_alert_on_credit_exhaustion``.

Wired as ``genlab-anthropic-credit-monitor.service`` and fired every
15 min via the matching ``.timer`` unit. See the library module
docstring for the full motivation (today's 2026-06-28 incident where
ai_creators + anime drained the day's Anthropic credit and
gaming/sports/movies silently produced 0 blueprints).

## Exit code

Always 0. This monitor is informational — failure must not cascade
into a systemd-failed state, because then the ``OnFailure=`` chain
fires the ``genlab-service-failure-alert@`` writer, which would mask
the actual credit-low signal we're trying to surface with a confusing
``systemd_unit_failed`` row.

Compare with ``niche_pause_sweeper`` which exits 1 on its sentinel:
that one is GC of operator-owned state. This one is purely an extra
signal layer on top of journalctl — a silent failure means the
operator's Mission Control banner takes 15 more minutes to light up,
which is operationally fine because the next sweep will catch up.

## Usage

    python scripts/anthropic_credit_monitor.py                       # apply
    python scripts/anthropic_credit_monitor.py --dry-run             # scan + log, no INSERT
    python scripts/anthropic_credit_monitor.py --window-minutes 120  # 2-hour lookback
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="anthropic_credit_monitor",
        description=(
            "Detect Anthropic credit-balance exhaustion in journalctl "
            "and surface as a CRITICAL alert in pipeline_alerts."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan + log without executing the INSERT.",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=60,
        help=(
            "How far back to scan journalctl (default: 60 — 4x headroom "
            "over the 15-min timer interval)."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Lazy import so the CLI stays fast to start in tests + the
    # import cost only lands when main() actually runs.
    from genlab_core.monitoring.anthropic_credit_monitor import (
        _summary_json,
        scan_and_alert_on_credit_exhaustion,
    )

    summary = scan_and_alert_on_credit_exhaustion(
        window_minutes=args.window_minutes,
        dry_run=args.dry_run,
    )
    # Single-line JSON summary so log-aggregation tooling can scrape
    # it cleanly (matches the metrics-writer + alert_auto_resolver
    # JSONL convention).
    print(_summary_json(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover — covered via main() in tests
    sys.exit(main(sys.argv[1:]))
