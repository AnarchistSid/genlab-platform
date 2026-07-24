#!/usr/bin/env python3
"""Periodic scan of /opt/genlab/.runtime/*_last_error.txt for stale
error files that indicate a service has been failing silently.

Motivating class-of-bug: 2026-07-24 discovered
``auto_accept_strategist_proposals.service`` had been exit=3 every
fire since deploy (5 weeks). The durable-error file worked exactly
as designed — captured the KeyError traceback to
``/opt/genlab/.runtime/auto_accept_strategist_proposals_last_error.txt``.
But nobody read it. systemd's OnFailure handler DID fire
service_down alerts, but they were absorbed into the general
alarm-cascade noise + eventually silenced by rule #26 discipline.

This scanner is the missing feedback loop. Every 6h it:

1. Lists .runtime/*_last_error.txt files
2. Reads each file's mtime + first line (ISO timestamp of last error)
3. Classifies:
   * fresh (<6h old): recent failure, probably still being triaged
   * stale (>24h old): script hasn't succeeded in a day — surface it
4. For stale files, writes one pipeline_alerts row per script.

Idempotent: pipeline_alerts has no natural dedup key for this
scanner, so we DELETE prior 'stale_durable_error' rows for the
same script before inserting fresh. Alternative would be to check
`resolved_at IS NULL` first, but the delete-then-insert pattern
gives operator a fresh mtime on every fire.

Fail-open: any exception logs WARNING and continues. Never crashes
the service (rule #26 discipline). Exit code always 0.

Flag: no flag. Diagnostic is always safe to run.

Usage:
    python scripts/scan_stale_durable_errors.py         # dry-run
    python scripts/scan_stale_durable_errors.py --apply

Exit codes:
    0 — always (including nothing-to-do or unhandled exception per
        rule #26 discipline).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


_RUNTIME_ROOT = Path("/opt/genlab/.runtime")

# Files older than this are considered "stale" — script hasn't
# succeeded since. Below this threshold, the failure is fresh and
# likely still being triaged by an active operator.
_STALE_HOURS = 24


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Actually write pipeline_alerts rows. Default is dry-run.",
    )
    ap.add_argument(
        "--stale-hours",
        type=int,
        default=_STALE_HOURS,
        help=f"Files older than this many hours are stale (default {_STALE_HOURS})",
    )
    ap.add_argument(
        "--runtime-root",
        default=str(_RUNTIME_ROOT),
        help=f"Directory to scan (default {_RUNTIME_ROOT})",
    )
    args = ap.parse_args()

    root = Path(args.runtime_root)
    if not root.exists():
        logger.info("[scan] runtime root %s does not exist — nothing to scan", root)
        return 0

    now = datetime.now(timezone.utc)
    stale_threshold_seconds = args.stale_hours * 3600

    fresh: list[dict] = []
    stale: list[dict] = []

    for path in sorted(root.glob("*_last_error.txt")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            age_seconds = (now - mtime).total_seconds()
            script_name = path.stem.removesuffix("_last_error")
            # First line typically carries the ISO timestamp of the
            # last error write. Falls back to file mtime if the
            # file is empty or malformed.
            first_line = ""
            try:
                first_line = path.read_text().splitlines()[0][:80]
            except Exception:  # noqa: BLE001
                pass
            entry = {
                "script": script_name,
                "path": str(path),
                "mtime": mtime.isoformat(),
                "age_hours": round(age_seconds / 3600, 1),
                "first_line": first_line,
            }
            if age_seconds >= stale_threshold_seconds:
                stale.append(entry)
            else:
                fresh.append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scan] failed to inspect %s: %s", path, exc)

    logger.info(
        "[scan] found %d durable-error files: %d fresh (<%dh), %d stale (>=%dh)",
        len(fresh) + len(stale),
        len(fresh),
        args.stale_hours,
        len(stale),
        args.stale_hours,
    )
    for e in stale:
        logger.info(
            "  STALE script=%s age=%sh mtime=%s first=%s",
            e["script"],
            e["age_hours"],
            e["mtime"],
            e["first_line"],
        )
    for e in fresh:
        logger.info(
            "  fresh script=%s age=%sh mtime=%s",
            e["script"],
            e["age_hours"],
            e["mtime"],
        )

    if not args.apply:
        logger.info("DRY-RUN. Re-run with --apply to write pipeline_alerts.")
        return 0

    if not stale:
        logger.info("[scan] no stale files — nothing to alert on")
        return 0

    _emit_alerts(stale)
    return 0


def _emit_alerts(stale: list[dict]) -> None:
    """Write one pipeline_alerts row per stale script. Deletes prior
    stale-durable-error rows for the same script first (dedup
    without needing a UNIQUE key)."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.warning("[scan] DATABASE_URL not set — skipping alert emit")
        return

    try:
        import json

        import psycopg
    except ImportError as exc:
        logger.warning("[scan] psycopg import failed: %s", exc)
        return

    check_name = "stale_durable_error"
    try:
        with psycopg.connect(dsn) as conn:
            for e in stale:
                # Dedup: remove any prior alert for this script that
                # hasn't been resolved. Fresh row on every fire so
                # operator sees the latest mtime + age.
                conn.execute(
                    """
                    DELETE FROM pipeline_alerts
                    WHERE check_name = %s
                      AND details->>'script' = %s
                      AND resolved_at IS NULL
                    """,
                    (check_name, e["script"]),
                )
                conn.execute(
                    """
                    INSERT INTO pipeline_alerts
                        (niche_id, check_name, severity, message, details)
                    VALUES (NULL, %s, 'warning', %s, %s::jsonb)
                    """,
                    (
                        check_name,
                        (
                            f"Durable error file for {e['script']} is "
                            f"{e['age_hours']}h old — script has been "
                            f"failing silently. Triage: "
                            f"sudo cat {e['path']}"
                        ),
                        json.dumps(e),
                    ),
                )
            conn.commit()
        logger.info("[scan] wrote %d stale_durable_error alerts", len(stale))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scan] alert emit failed: %s", exc)


def _main_with_durable_error() -> int:
    try:
        return main()
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        # Rule #26 discipline: never exit non-zero. This scanner IS
        # the last line of defense for silent failures — if IT fails
        # silently, that's an ironic bug of its own. Log traceback
        # via write_durable_error (dogfood the same tool it's
        # scanning for).
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "genlab-core" / "src"))
            from genlab_core.observability.durable_error import write_durable_error

            write_durable_error("scan_stale_durable_errors", exc)
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc(file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
