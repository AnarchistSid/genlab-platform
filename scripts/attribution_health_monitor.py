#!/usr/bin/env python3
"""CLI wrapper for ``attribution_health_monitor.scan_and_alert``.

Wired as ``genlab-attribution-health-monitor.service`` and fired every
30 min via the matching ``.timer`` unit. Same operational contract
as ``anthropic_credit_monitor``: exit 0 always; failure surfaces
via journalctl only, not systemd_unit_failed.

## Usage

    python scripts/attribution_health_monitor.py                 # apply
    python scripts/attribution_health_monitor.py --dry-run       # scan + log, no INSERT
    python scripts/attribution_health_monitor.py --window-hours 48   # 2-day lookback
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="attribution_health_monitor",
        description=__doc__,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--window-hours", type=int, default=24)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from genlab_core.monitoring.attribution_health_monitor import scan_and_alert

    summary = scan_and_alert(
        window_hours=args.window_hours,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
