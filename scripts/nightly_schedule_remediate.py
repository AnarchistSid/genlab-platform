#!/usr/bin/env python3
"""Remediation companion for ``genlab-nightly-schedule.service`` failure
(task #612, 2026-07-09).

Purpose
-------
When the nightly scheduler exits 1, this script runs as a systemd
OnFailure companion. It writes a HIGHER-SIGNAL alert to
``pipeline_alerts`` than the generic ``systemd_failure_alert.sh``,
because it can:

1. **Parse the journal** to extract exactly which niche(s) missed the
   slot ("No schedulable candidate for: ['sports']").
2. **Query the DB** for pull-back candidates AT ``target + 1 day`` —
   which is MORE AGGRESSIVE than the automated pull-back branch's
   ``MIN_PULLBACK_DAYS = 2`` cascade guard (see task #611). The
   operator gets the final say on whether cascade risk is worth
   today's fill; the machine offers suggestions.
3. **Emit a niche-specific alert** with (a) missing niche, (b) hours
   until publisher fires, (c) suggested SQL to run, (d) available
   pull-back candidates. Dashboard's ``CriticalAlertsBanner`` picks
   it up via ``pipeline_alerts.severity = 'critical'``.

Why not just enhance ``systemd_failure_alert.sh``?
--------------------------------------------------
That script is generic and fires for every ``genlab-*`` failure.
Enhancing it with nightly-schedule-specific parsing would couple the
generic path to a specific unit. Sibling remediate scripts (one per
service-with-known-remediation) is a cleaner architecture and each
one owns its own detection + suggestion logic.

Composes with — does NOT replace — the generic ``systemd_failure_alert.sh``.
The .service file has both OnFailure chains, so operator sees the
generic "unit failed" alert PLUS this specialized alert with actionable
context.

Exit codes
----------
* ``0`` — remediation alert written (or no missing niches detected —
  the parent runner may have failed for a different reason).
* ``0`` — journal parse found no niches and no candidates. Alert-only
  path, always exits 0. Never fail the failure-remediation.

Never exits non-zero: that would trigger a cascading OnFailure alert
about THIS unit, and we'd lose sight of the original failure. Same
discipline as ``systemd_failure_alert.sh``.

Usage
-----
Invoked by systemd via ``OnFailure=genlab-nightly-schedule-remediate.service``.
For manual test:

::

    uv run python3 scripts/nightly_schedule_remediate.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")

# The parent runner emits this exact log line via ``print()`` when it
# can't find a schedulable candidate for a niche. Pin the regex to
# the shape of that line so a refactor over there triggers the pin
# in tests (below) rather than a silent parse miss in production.
#
# Example matched line:
#   ⚠️  No schedulable candidate for: ['sports', 'gaming'] (empty ...
_MISSING_NICHES_RE = re.compile(
    r"No schedulable candidate for:\s*\[([^\]]+)\]",
    re.IGNORECASE,
)


def _load_env_file(path: Path) -> None:
    """Same shape as sibling scripts — dep-free .env loader."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _read_journal(unit: str, since_minutes: int = 10) -> str:
    """Read journal output for ``unit`` over the last ``since_minutes``.

    Returns empty string on failure — never raises. We never want the
    remediation script to fail because of a journalctl hiccup; the
    surviving alert path (generic systemd_failure_alert.sh) still
    covers the operator.
    """
    try:
        out = subprocess.run(
            [
                "journalctl",
                "-u",
                unit,
                "--since",
                f"{since_minutes} minutes ago",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout or ""
    except Exception:
        return ""


def parse_missing_niches(journal_text: str) -> set[str]:
    """Extract missing niches from parent runner's log line.

    Returns empty set if no matching line found. Empty set is a valid
    (though unusual) result — parent may have failed for a different
    reason (DB timeout, argparse error, etc.), in which case the
    generic ``systemd_failure_alert.sh`` covers the operator and we
    have nothing niche-specific to add.
    """
    match = _MISSING_NICHES_RE.search(journal_text)
    if not match:
        return set()
    inner = match.group(1)
    # inner looks like: 'sports', 'gaming' (single-quoted names)
    niche_names = re.findall(r"'([^']+)'", inner)
    return {n for n in niche_names if n in NICHES}


def compute_target_slot(now_utc: datetime | None = None) -> datetime:
    """Same shape as the parent runner's compute_target_slot. Keep both
    in sync — a divergence here would suggest SQL to fill the wrong
    date."""
    if now_utc is None:
        now_utc = datetime.now(UTC)
    tomorrow_utc: date = now_utc.date() + timedelta(days=1)
    return datetime.combine(tomorrow_utc, time(6, 0, 0), tzinfo=UTC)


def find_pullback_candidates(cur, niche: str, target_date: date) -> list[dict]:
    """Return operator-suggestible candidates for the missing slot.

    MORE AGGRESSIVE than the automated pull-back at
    ``pick_top_per_niche`` (task #611): includes candidates at
    ``target + 1 day`` (bypasses the ``MIN_PULLBACK_DAYS = 2`` cascade
    guard). Operator gets to decide whether shifting tomorrow's slot
    to fill today is worth the cascade risk.

    Returns UP TO 3 candidates, earliest first — enough for the
    operator to compare hooks without dumping the whole queue.
    """
    min_pullback = target_date + timedelta(days=1)
    cur.execute(
        """
        SELECT id::text, scheduled_for, priority_score, hook
        FROM blueprints
        WHERE niche_id = %s
          AND status = 'VISUAL_READY'
          AND scheduled_for::date >= %s
          AND (action_taken IS NULL OR action_taken NOT IN ('rejected', 'archived'))
          AND hook IS NOT NULL
          AND hook NOT ILIKE 'I need to stop%%'
          AND hook NOT ILIKE 'I cannot%%'
          AND hook NOT ILIKE 'I can''t%%'
          AND hook NOT ILIKE 'I am unable%%'
          AND hook NOT ILIKE 'I''m sorry%%'
          AND hook NOT ILIKE 'I apologize%%'
          AND length(hook) BETWEEN 15 AND 100
        ORDER BY scheduled_for ASC, priority_score DESC NULLS LAST
        LIMIT 3
        """,
        (niche, min_pullback),
    )
    return list(cur.fetchall())


def build_alert_message(
    niche: str,
    target_slot: datetime,
    candidates: list[dict],
    now_utc: datetime,
) -> str:
    """Compose an actionable alert body. Sections:

    * WHAT HAPPENED — one-line summary
    * TIME TO PUBLISHER — hours until 06:35 UTC target-date fire
    * SUGGESTED SQL — one-liner the operator can copy-paste
    * CANDIDATES — up to 3 rows with hooks + current scheduled_for

    Message is plain text (no HTML). Dashboard's ``CriticalAlertsBanner``
    renders it verbatim in a ``<pre>`` block.
    """
    hours_to_fire = _hours_to_publisher(target_slot, now_utc)
    lines = [
        f"Nightly scheduler could not fill {target_slot.date().isoformat()} slot for '{niche}'.",
        f"Publisher fires 06:35 UTC in ~{hours_to_fire:.1f} hours.",
        "",
    ]
    if candidates:
        top = candidates[0]
        lines.append(
            f"SUGGESTED SQL — shift the earliest far-future '{niche}' blueprint back to fill today:"
        )
        lines.append(
            f"  UPDATE blueprints SET scheduled_for = '{target_slot.isoformat()}', "
            f"updated_at = NOW() WHERE id = '{top['id']}';"
        )
        lines.append("")
        lines.append(f"CANDIDATES for '{niche}' (up to 3, earliest first):")
        for i, row in enumerate(candidates, start=1):
            when = row["scheduled_for"].date().isoformat() if row["scheduled_for"] else "?"
            score = row["priority_score"] if row["priority_score"] is not None else 0.0
            hook = (row["hook"] or "")[:60]
            lines.append(f"  {i}. [{when}] score={score:.3f} hook={hook!r}")
    else:
        lines.append(
            f"NO PULL-BACK CANDIDATES for '{niche}' — pipeline is stalled AND "
            "the far-future queue is empty."
        )
        lines.append(
            f"Investigate: systemctl status genlab-pipeline-{niche}.service && "
            f"journalctl -u genlab-pipeline-{niche}.service -n 100"
        )
        lines.append(
            f"Fresh run: systemctl start genlab-pipeline-{niche}.service (will take 20-40 min)"
        )
    lines.append("")
    lines.append(
        "Automated pull-back branch (task #611) already ran with "
        "MIN_PULLBACK_DAYS=2. This alert offers +1 day candidates "
        "the machine wouldn't take without operator override."
    )
    return "\n".join(lines)


def _hours_to_publisher(target_slot: datetime, now_utc: datetime) -> float:
    """Hours between now and the 06:35 UTC publisher fire on the
    target date. Publisher fires 35 min after target_slot (which is
    06:00 UTC). Returns 0.0 if publisher already fired (should be rare —
    remediate runs from 22:00 IST = 16:30 UTC previous day)."""
    publisher_fire = target_slot + timedelta(minutes=35)
    delta = publisher_fire - now_utc
    return max(0.0, delta.total_seconds() / 3600.0)


def write_alert(cur, niche: str, message: str) -> None:
    """Insert one critical row into pipeline_alerts.

    ``check_name='nightly_schedule_missing_slot'`` distinguishes this
    from the generic ``systemd_unit_failed`` written by
    ``systemd_failure_alert.sh``. Dashboard filters on check_name so
    operator sees BOTH: the generic "unit failed" AND this actionable
    suggestion.

    ``resolved_at=NULL`` — auto-resolves when the operator fills the
    slot (a separate sweeper task).
    """
    cur.execute(
        """
        INSERT INTO pipeline_alerts (
            niche_id, check_name, severity, message, created_at, resolved_at
        ) VALUES (
            %s, 'nightly_schedule_missing_slot', 'critical', %s, NOW(), NULL
        )
        """,
        (niche, message),
    )


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set; source /opt/genlab/.env before running.")
    return psycopg.connect(url, row_factory=dict_row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview alerts; do NOT write to database",
    )
    ap.add_argument(
        "--env-file",
        default="/opt/genlab/.env",
        help="Path to .env (default /opt/genlab/.env)",
    )
    ap.add_argument(
        "--unit",
        default="genlab-nightly-schedule.service",
        help="Failing unit to read journal from",
    )
    args = ap.parse_args()

    _load_env_file(Path(args.env_file))

    journal_text = _read_journal(args.unit, since_minutes=10)
    if not journal_text:
        print(
            f"WARN: no journal output for {args.unit} in last 10 min. "
            "Generic systemd_failure_alert.sh covers this case.",
            file=sys.stderr,
        )
        return 0

    missing = parse_missing_niches(journal_text)
    if not missing:
        print(
            "WARN: parent runner failed but no 'No schedulable candidate' "
            "line found. Failure was likely something else (DB, argparse). "
            "Generic systemd_failure_alert.sh covers this case.",
            file=sys.stderr,
        )
        return 0

    target_slot = compute_target_slot()
    target_date = target_slot.date()
    now_utc = datetime.now(UTC)
    print(f"Missing niches: {sorted(missing)}")
    print(f"Target slot: {target_slot.isoformat()}")

    try:
        with _connect() as conn, conn.cursor() as cur:
            for niche in sorted(missing):
                candidates = find_pullback_candidates(cur, niche, target_date)
                message = build_alert_message(niche, target_slot, candidates, now_utc)
                if args.dry_run:
                    print(f"--- DRY: alert for {niche} ---")
                    print(message)
                    continue
                write_alert(cur, niche, message)
                print(f"✓ alert written for {niche} ({len(candidates)} candidates)")
            if not args.dry_run:
                conn.commit()
    except Exception as exc:
        # Never fail the remediation — that would cascade OnFailure
        # alerts about this unit and lose the original signal.
        print(f"WARN: alert write failed: {exc}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
