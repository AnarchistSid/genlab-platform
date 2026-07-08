#!/usr/bin/env python3
"""CLI wrapper for ``alert_auto_resolver.auto_resolve_systemd_unit_alerts``.

Wired as ``genlab-alert-auto-resolve.service`` and fired every 5 min
via the matching ``.timer`` unit. See the library module docstring
for the full motivation (today's 20-stale-alerts incident).

## Exit code

Always 0. The auto-resolve sweeper is informational — if it can't
run for any reason (DB unavailable, systemctl missing, etc.) that
does NOT justify failing systemd alarms. The library function
returns a counter dict; we log it and exit clean.

Compare with ``niche_pause_sweeper`` which exits 1 on its sentinel:
that one is GC of operator-owned state (expired pauses), so a
silent failure means table clutter that operators should notice.
This one is GC of MACHINE-owned state (alerts the machine wrote);
a silent failure here just means the operator's CriticalAlertsBanner
takes 5 more minutes to self-clean — recoverable on the next fire,
no operator action needed.

## Usage

    python scripts/auto_resolve_alerts.py            # apply
    python scripts/auto_resolve_alerts.py --dry-run  # log + count, no UPDATE
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="auto_resolve_alerts",
        description=(
            "Auto-resolve unresolved 'systemd_unit_failed' alerts whose "
            "underlying unit has since run successfully."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what WOULD be resolved without executing the UPDATE.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Lazy import so the CLI stays fast to start in tests + the import
    # cost only lands when main() actually runs.
    from genlab_core.observability.alert_auto_resolver import (
        _summary_json,
        auto_resolve_nightly_schedule_missing_slot_alerts,
        auto_resolve_systemd_unit_alerts,
    )

    # Two independent resolvers, each with distinct semantics.
    # Structured as a list of (label, callable) so adding a new
    # resolver in the future stays one-line.
    #
    # Each resolver's counter dict is emitted under its label so
    # log-aggregation can distinguish them. Failure of one does NOT
    # skip the next — same fail-open discipline as the resolvers
    # themselves.
    resolvers = [
        ("systemd_unit_failed", auto_resolve_systemd_unit_alerts),
        ("nightly_schedule_missing_slot", auto_resolve_nightly_schedule_missing_slot_alerts),
    ]

    combined: dict[str, dict[str, int]] = {}
    for label, resolver in resolvers:
        try:
            counters = resolver(dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 — fail-open per resolver
            logging.warning("[auto_resolve_alerts] %s resolver raised: %s", label, exc)
            counters = {"errors": 1}
        combined[label] = counters

    # Single-line JSON summary so log-aggregation tooling can scrape
    # it cleanly (matches the metrics-writer JSONL convention).
    print(_summary_json(combined))
    return 0


if __name__ == "__main__":  # pragma: no cover — covered via main() in tests
    sys.exit(main(sys.argv[1:]))
