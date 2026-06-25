"""CLI entrypoint for the daily compliance digest Slack message.

PR after #584 (2026-06-25). Companion to the block-decision
notifier — fires once per day on a systemd timer to push
operators a summary of the prior 24h's compliance activity.

## Usage

    python -m genlab_core.compliance.compliance_digest_sender
    python -m genlab_core.compliance.compliance_digest_sender --window-days 7

## Exit codes

  * ``0`` — digest sent successfully OR no-op (env unset).
    Both are 'normal operation' outcomes — operators who
    haven't activated the webhook see exit-0 silently.
  * ``1`` — webhook POST failed (network error, non-2xx).
    Systemd 'failed' state surfaces so operators can fix
    a revoked / malformed webhook URL.

## Why split the CLI from the library

Same rationale as ``niche_pause_sweeper`` (PR #580): the library
function shouldn't depend on ``logging.basicConfig`` or argparse —
those are CLI concerns. Splitting them keeps the library
importable by other callers (e.g. a future "send digest now"
dashboard button) without dragging in CLI machinery.

## Default window: 1 day

Matches the typical 'daily digest' cadence. Operators wiring this
as a weekly summary instead can pass ``--window-days 7``.
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns 0 on success / no-op, 1 on POST failure.

    ``argv`` is accepted for tests (e.g. assert that --window-days
    propagates to the library call).
    """
    parser = argparse.ArgumentParser(
        prog="compliance-digest-sender",
        description="Send a Slack digest of compliance activity.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=1,
        help="Aggregation window in days (default: 1 = last 24h)",
    )
    args = parser.parse_args(argv)

    # Match the sweeper's INFO-level config so journalctl captures
    # the per-day summary line emitted by send_compliance_digest
    # on successful send.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Lazy imports keep the CLI fast to start when tests call main()
    from genlab_core.compliance.events import stats_by_niche
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    by_niche = stats_by_niche(window_days=args.window_days)
    sent = send_compliance_digest(by_niche, window_days=args.window_days)

    # Distinguish "didn't send because webhook unset" (exit 0,
    # normal no-op) from "tried and failed" (exit 1, operator-visible
    # failure). We re-check the env to draw the line — the library
    # function returns False in BOTH cases but for different reasons.
    import os

    webhook_set = bool(os.environ.get("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "").strip())
    if sent or not webhook_set:
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover — covered via main()
    sys.exit(main(sys.argv[1:]))
