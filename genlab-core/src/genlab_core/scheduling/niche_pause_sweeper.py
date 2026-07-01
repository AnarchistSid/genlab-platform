"""CLI entrypoint for the niche_pause expired-row sweeper.

See ``docs/SCHEDULING-CONTRACT.md`` for the auto-scheduling contract
this sweeper enforces (approved blueprints without scheduled_for get
slot assignment on the next daily fire at 02:00 UTC).

Designed to be wired as a systemd timer (daily at low-traffic
hours). The library function ``niche_pause.sweep_expired_pauses``
returns a count + logs; this module turns that into a CLI with
a sensible exit code so the timer surfaces failures without
spamming operators for "0 rows" successes.

## Usage

    python -m genlab_core.scheduling.niche_pause_sweeper

## Exit codes

  * ``0`` — success (deletion count >= 0). Operator only sees
    a log line; systemd shows ``status=0/SUCCESS``.
  * ``1`` — DB unavailable / SQL error (sentinel ``-1`` from the
    library). Systemd ``failed`` state; alerting / journalctl
    surfaces.

## Why a separate module

The library function in ``niche_pause`` shouldn't depend on
``logging.basicConfig`` or argparse — those are CLI concerns.
Keeping them in this sibling module lets callers (dashboard,
tests, future programmatic sweep triggers) import the library
function without dragging stdlib logging configuration into
their process.

## Why fail-CLOSED on -1

Operators wired the sweeper into a timer because they wanted
"expired rows go away on a schedule." A persistent silent
failure means the table grows unbounded and visual clutter
accumulates on the inspection view. Exit-non-zero ensures
systemd's failure-state surfacing happens — operators can
investigate via ``systemctl status``.
"""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns 0 on success, 1 on sweep-failure sentinel.

    ``argv`` is accepted for future expansion (e.g. ``--dry-run``)
    and to make the entrypoint test-callable. Today's MVP ignores
    args — the library function takes no parameters.
    """
    _ = argv  # reserved for future flags

    # Configure root logging at INFO so the sweep summary line
    # ("[niche_pause] sweep_expired_pauses deleted N row(s)") is
    # captured by journalctl. The library function logs at DEBUG
    # for the no-op case — operators don't need to see that.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Lazy import keeps the CLI fast to start when used in tests
    # (no DB module import unless main() actually runs).
    from genlab_core.scheduling.niche_pause import sweep_expired_pauses

    count = sweep_expired_pauses()
    if count < 0:
        # The library already WARN-logged the failure cause; the
        # non-zero exit is the systemd-visible signal.
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover — covered by the test harness via main()
    sys.exit(main(sys.argv[1:]))
