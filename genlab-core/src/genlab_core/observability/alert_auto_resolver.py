"""Auto-resolve stale ``pipeline_alerts`` rows.

Why this exists
---------------
``scripts/systemd_failure_alert.sh`` (wired via ``OnFailure=`` on every
``genlab-*`` service) writes a CRITICAL row to ``pipeline_alerts``
the instant a systemd unit transitions to ``failed`` state. That
script is intentionally write-only — once the row exists, NOTHING
clears it without operator action.

On 2026-06-27 a deploy's restart sweep fired 20 ``Type=oneshot``
services out-of-schedule (see ``scripts/deploy.sh`` Phase 7 comments
for the full story). Every one hit a transient TTS API 429, each
tripped its OnFailure handler, and 20 CRITICAL rows landed in
pipeline_alerts. Within minutes the next normal-schedule run of each
of those services succeeded — but the rows stayed in the table until
an operator cleared them via SQL UPDATE. Result: Mission Control's
CriticalAlertsBanner showed 20 unresolved CRITICAL alerts that were
all silently stale.

This module fixes the bleed-out path. It runs every 5 min via systemd
timer, walks every unresolved row per check_name, and marks any that
have since recovered as resolved.

Public surface
--------------
``auto_resolve_systemd_unit_alerts(*, dry_run=False) -> dict``
    For ``check_name = 'systemd_unit_failed'``. Asks systemd "has this
    unit had a successful run since the alert was created?" — and if
    yes, marks resolved.

``auto_resolve_nightly_schedule_missing_slot_alerts(*, dry_run=False) -> dict``
    For ``check_name = 'nightly_schedule_missing_slot'`` (task #613,
    2026-07-09). Extracts (target_date, niche) from the alert message,
    queries the blueprints table to check if the slot is now filled,
    and marks resolved if so. Companion resolver to PR #739 (task #612).

Both return counter dicts and never raise into the caller.

Failure mode
------------
Fail-OPEN throughout. Any per-row exception is logged at WARNING and
the loop continues. The function's contract is "best-effort cleanup";
the alternative (raising and aborting the whole sweep) would mean one
unparseable row could leave 50 other auto-resolvable ones stuck.

Race-condition safety
---------------------
We do NOT resolve rows whose unit is currently in ``active`` state.
If a unit is mid-run when the sweeper visits it, the next successful
``InactiveEnterTimestamp`` will arrive shortly; resolving while the
run is in-flight is premature. A failed-then-running-then-failed
sequence would still surface a CRITICAL row on the second failure
(the failure-alert wire fires every transition).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


# Regex to extract the failing unit name from the message text written
# by scripts/systemd_failure_alert.sh. The message starts with:
#   "Systemd unit <unit-name>.service failed at <ISO timestamp>."
# We allow lower-case alpha + digits + dash + dot + @ (for templated
# units like genlab-service-failure-alert@foo.service) so we don't have
# to special-case templates.
_UNIT_REGEX = re.compile(r"Systemd unit (genlab-[a-z0-9.@-]+\.service) failed at")


def _connect():
    """Open a psycopg connection from DATABASE_URL — returns None on
    missing DSN / connection failure. Caller fails OPEN.

    Mirrors ``niche_pause._connect`` so behavioural changes in the
    canonical pattern stay in sync."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[alert_auto_resolver] DATABASE_URL not set; auto-resolve disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alert_auto_resolver] connection failed: %s", exc)
        return None


def _extract_unit(message: str | None) -> str | None:
    """Pull the failing unit name out of a pipeline_alerts.message blob.

    Returns None when the message doesn't match the expected shape
    (unparseable row — caller increments errors counter)."""
    if not message:
        return None
    m = _UNIT_REGEX.search(message)
    if m is None:
        return None
    return m.group(1)


def _query_systemd_unit(unit: str) -> dict[str, str] | None:
    """Ask systemd for Result / ExecMainStatus / InactiveEnterTimestamp
    / ActiveState for ``unit``. Returns None on subprocess failure.

    Uses ``systemctl show`` with ``--property=`` to fetch exactly the
    fields we need in a single call (one process per row beats one
    per-property)."""
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=Result",
                "--property=ExecMainStatus",
                "--property=InactiveEnterTimestamp",
                "--property=ActiveState",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("[alert_auto_resolver] systemctl show %s failed: %s", unit, exc)
        return None

    if proc.returncode != 0:
        # Unit doesn't exist or systemd is unreachable. The stderr is
        # often empty for "unit not found" because systemctl writes to
        # stdout for show — log the stdout for debuggability.
        logger.warning(
            "[alert_auto_resolver] systemctl show %s exit=%d stdout=%r stderr=%r",
            unit,
            proc.returncode,
            proc.stdout[:200] if proc.stdout else "",
            proc.stderr[:200] if proc.stderr else "",
        )
        return None

    # Parse "Key=value" lines into a dict. Empty values legitimate
    # (e.g. InactiveEnterTimestamp is empty for a unit that's never
    # entered inactive state).
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()

    if not out:
        # Unit name was syntactically valid but systemd returned
        # nothing — treat as "not found".
        return None

    return out


def _parse_systemd_timestamp(value: str | None):
    """Parse a systemd InactiveEnterTimestamp into a datetime.

    Systemd writes timestamps as e.g. ``Fri 2026-06-27 09:30:12 UTC``.
    Returns None on empty / unparseable input.

    We use ``datetime`` only here to keep the dependency footprint
    minimal — psycopg's ``created_at`` round-trips as a timezone-aware
    ``datetime`` so we compare apples-to-apples."""
    if not value:
        return None
    from datetime import datetime

    # Try the canonical "Day YYYY-MM-DD HH:MM:SS TZ" shape first.
    # strptime can't parse short TZ names so we manually split.
    try:
        # Strip the weekday prefix ("Fri ") — three letters + space.
        if len(value) > 4 and value[3] == " ":
            value = value[4:]
        # The trailing timezone is typically "UTC" or "GMT"; both are
        # the same offset for our purposes (the sweeper runs <5min
        # after the unit's success so sub-second precision doesn't
        # matter). We split it off and assume UTC.
        parts = value.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isalpha():
            body, _tz = parts
        else:
            body = value
        dt = datetime.strptime(body, "%Y-%m-%d %H:%M:%S")
        # Make it timezone-aware (UTC) to match psycopg's behaviour
        # for TIMESTAMPTZ columns.
        from datetime import UTC

        return dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def auto_resolve_systemd_unit_alerts(*, dry_run: bool = False) -> dict[str, int]:
    """Walk unresolved ``systemd_unit_failed`` alerts; resolve any
    whose unit has since had a successful run.

    Parameters
    ----------
    dry_run : bool, default False
        When True, log every resolution decision but do NOT execute
        the UPDATE. The returned counters reflect what WOULD have
        happened.

    Returns
    -------
    dict
        ``{"checked": int, "resolved": int, "skipped_still_failed": int,
        "skipped_no_recent_run": int, "errors": int}``

    Notes
    -----
    Never raises into the caller. Any per-row error logs WARNING and
    the loop continues.
    """
    counters = {
        "checked": 0,
        "resolved": 0,
        "skipped_still_failed": 0,
        "skipped_no_recent_run": 0,
        "errors": 0,
    }

    conn_cm = _connect()
    if conn_cm is None:
        # No DB — log + early-return zero counters. The timer's next
        # fire will retry.
        return counters

    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT id, message, created_at
                FROM pipeline_alerts
                WHERE check_name = 'systemd_unit_failed'
                  AND resolved_at IS NULL
                ORDER BY created_at ASC
                """
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[alert_auto_resolver] SELECT failed: %s", exc)
        return counters

    if not rows:
        logger.debug("[alert_auto_resolver] no unresolved systemd_unit_failed alerts")
        return counters

    # We've successfully read the rows; reopen the conn for the per-
    # row updates because the SELECT block above closed the
    # connection. Reopening is cheap (pool reuse) and keeps the read
    # / write transactions cleanly separated.
    for row in rows:
        counters["checked"] += 1
        row_id = row.get("id")
        message = row.get("message")
        created_at = row.get("created_at")

        try:
            unit = _extract_unit(message)
            if unit is None:
                logger.warning(
                    "[alert_auto_resolver] row %s: could not parse unit name from message; skipping",
                    row_id,
                )
                counters["errors"] += 1
                continue

            status = _query_systemd_unit(unit)
            if status is None:
                # Unit not found OR systemctl failed. Skip — leave the
                # alert visible because we can't confirm a success.
                counters["errors"] += 1
                continue

            active_state = status.get("ActiveState", "")
            result = status.get("Result", "")
            exec_status = status.get("ExecMainStatus", "")
            inactive_enter = status.get("InactiveEnterTimestamp", "")

            # Race-condition safety: don't resolve a currently-running
            # unit. The next InactiveEnterTimestamp will arrive shortly
            # and the following sweep will handle it cleanly.
            if active_state == "active":
                logger.debug(
                    "[alert_auto_resolver] row %s: unit %s currently active; skipping",
                    row_id,
                    unit,
                )
                counters["skipped_no_recent_run"] += 1
                continue

            # Still in a failed state — operator must investigate.
            if result == "failed":
                logger.debug(
                    "[alert_auto_resolver] row %s: unit %s Result=failed; leaving alert visible",
                    row_id,
                    unit,
                )
                counters["skipped_still_failed"] += 1
                continue

            # Anything other than success → skip (signal/timeout/etc).
            if result != "success" or exec_status != "0":
                logger.debug(
                    "[alert_auto_resolver] row %s: unit %s Result=%r ExecMainStatus=%r; not a clean success; skipping",
                    row_id,
                    unit,
                    result,
                    exec_status,
                )
                counters["skipped_still_failed"] += 1
                continue

            # Successful run, but is it RECENT enough? Specifically:
            # did the success happen AFTER the alert was created? If
            # the only recorded success is older than the alert, we
            # haven't actually seen a recovery — skip and wait for the
            # next sweep.
            success_at = _parse_systemd_timestamp(inactive_enter)
            if success_at is None:
                logger.debug(
                    "[alert_auto_resolver] row %s: unit %s has no parseable InactiveEnterTimestamp; skipping",
                    row_id,
                    unit,
                )
                counters["skipped_no_recent_run"] += 1
                continue

            if created_at is None or success_at <= created_at:
                logger.debug(
                    "[alert_auto_resolver] row %s: unit %s last success %s is not after alert created_at %s; skipping",
                    row_id,
                    unit,
                    success_at,
                    created_at,
                )
                counters["skipped_no_recent_run"] += 1
                continue

            # Auto-resolve.
            from datetime import UTC, datetime

            today_str = datetime.now(UTC).strftime("%Y-%m-%d")
            note = (
                f"\n\n[AUTO-RESOLVED {today_str}: unit has since run "
                f"successfully (Result=success at {success_at.isoformat()}).]"
            )

            if dry_run:
                logger.info(
                    "[alert_auto_resolver] DRY-RUN would resolve row %s (unit=%s)",
                    row_id,
                    unit,
                )
                counters["resolved"] += 1
                continue

            try:
                with _connect() as write_conn:  # type: ignore[union-attr]
                    if write_conn is None:
                        # The earlier read succeeded but reconnection
                        # failed — log + bail on remaining updates.
                        logger.warning(
                            "[alert_auto_resolver] reconnect for UPDATE failed; row %s left unresolved",
                            row_id,
                        )
                        counters["errors"] += 1
                        continue
                    with write_conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pipeline_alerts
                            SET resolved_at = NOW(),
                                message = COALESCE(message, '') || %s
                            WHERE id = %s
                              AND resolved_at IS NULL
                            """,
                            (note, row_id),
                        )
                logger.info(
                    "[alert_auto_resolver] resolved row %s (unit=%s, success_at=%s)",
                    row_id,
                    unit,
                    success_at.isoformat(),
                )
                counters["resolved"] += 1
            except Exception as exc:  # noqa: BLE001 — fail-open per row
                logger.warning("[alert_auto_resolver] UPDATE row %s failed: %s", row_id, exc)
                counters["errors"] += 1

        except Exception as exc:  # noqa: BLE001 — fail-open per row
            logger.warning(
                "[alert_auto_resolver] unexpected error on row %s: %s",
                row_id,
                exc,
            )
            counters["errors"] += 1

    return counters


# ── nightly_schedule_missing_slot resolver (task #613, 2026-07-09) ──
#
# The specialized companion to PR #739 (task #612). When
# `scripts/nightly_schedule_remediate.py` writes a critical
# `nightly_schedule_missing_slot` alert, that alert stays visible in
# CriticalAlertsBanner until:
#
#   (a) the operator manually resolves it, or
#   (b) task #611's next-night pull-back branch (or a fresh pipeline)
#       fills the slot — but nothing was watching for that transition.
#
# This resolver watches for (b): every 5 min it walks unresolved
# `nightly_schedule_missing_slot` rows, extracts the (target_date,
# niche) pair from the message, queries the blueprints table to see
# if that slot is now filled, and auto-resolves the alert if so.
#
# Same fail-open discipline as auto_resolve_systemd_unit_alerts —
# the sweeper is informational and any per-row exception logs a
# WARNING and continues.

# Regex to extract the (target_date, niche) pair from the message body
# written by `nightly_schedule_remediate.build_alert_message`. The
# first line is:
#   "Nightly scheduler could not fill YYYY-MM-DD slot for 'niche'."
# We anchor on the specific phrase so a future format change to the
# non-first-line body doesn't break parsing.
_MISSING_SLOT_REGEX = re.compile(
    r"Nightly scheduler could not fill (\d{4}-\d{2}-\d{2}) slot for "
    r"'([a-z_]+)'"
)


def _extract_missing_slot(message: str | None) -> tuple[str, str] | None:
    """Pull ``(target_date_iso, niche)`` out of a
    ``nightly_schedule_missing_slot`` message body.

    Returns None when the message doesn't match the expected shape —
    caller increments errors counter and leaves the alert visible.
    """
    if not message:
        return None
    m = _MISSING_SLOT_REGEX.search(message)
    if m is None:
        return None
    return m.group(1), m.group(2)


def _query_slot_is_filled(niche: str, target_date_iso: str) -> bool | None:
    """Return True iff there's a blueprint for ``niche`` scheduled on
    ``target_date_iso`` in a "counts as scheduled" shape.

    Uses the SAME shape-match as ``nightly_schedule_top_per_niche.
    niches_needing_scheduling`` — a niche is considered "already
    scheduled" for a date if any blueprint has ``scheduled_for::date``
    matching AND is in one of:

      * Legacy: ``status IN ('SCHEDULED', 'PUBLISHED')``
      * Current: ``status = 'VISUAL_READY' AND action_taken = 'approved'``

    Returns None on DB error — caller treats as skip (leave alert
    visible).
    """
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM blueprints
                WHERE niche_id = %s
                  AND scheduled_for::date = %s
                  AND (
                    status IN ('SCHEDULED', 'PUBLISHED')
                    OR (status = 'VISUAL_READY' AND action_taken = 'approved')
                  )
                LIMIT 1
                """,
                (niche, target_date_iso),
            ).fetchone()
        return row is not None
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[alert_auto_resolver] slot-fill query failed niche=%s date=%s: %s",
            niche,
            target_date_iso,
            exc,
        )
        return None


def auto_resolve_nightly_schedule_missing_slot_alerts(*, dry_run: bool = False) -> dict[str, int]:
    """Walk unresolved ``nightly_schedule_missing_slot`` alerts;
    resolve any whose target slot has since been filled.

    Parameters
    ----------
    dry_run : bool, default False
        Log every resolution decision but do NOT execute the UPDATE.

    Returns
    -------
    dict
        ``{"checked": int, "resolved": int, "skipped_slot_still_empty": int,
        "skipped_query_failed": int, "errors": int}``

    Notes
    -----
    Never raises into the caller. Per-row error → WARNING + continue.

    Rationale for a separate function (vs a generic "resolve all"
    dispatcher): each resolver has different check-name-specific
    parsing + query semantics. Coupling them into one function would
    couple their failure modes; keeping them separate mirrors the
    "one script per remediation target" architecture from PR #739.
    """
    counters = {
        "checked": 0,
        "resolved": 0,
        "skipped_slot_still_empty": 0,
        "skipped_query_failed": 0,
        "errors": 0,
    }

    conn_cm = _connect()
    if conn_cm is None:
        return counters

    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT id, message, created_at, niche_id
                FROM pipeline_alerts
                WHERE check_name = 'nightly_schedule_missing_slot'
                  AND resolved_at IS NULL
                ORDER BY created_at ASC
                """
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[alert_auto_resolver] SELECT missing_slot alerts failed: %s", exc)
        return counters

    if not rows:
        logger.debug("[alert_auto_resolver] no unresolved nightly_schedule_missing_slot alerts")
        return counters

    for row in rows:
        counters["checked"] += 1
        row_id = row.get("id")
        message = row.get("message")
        # Prefer niche_id column (already normalized) over parsing from
        # message, but fall back to message parse if the column is
        # somehow empty/unusable.
        column_niche = row.get("niche_id") or ""

        try:
            parsed = _extract_missing_slot(message)
            if parsed is None:
                logger.warning(
                    "[alert_auto_resolver] row %s: missing_slot message unparseable; skipping",
                    row_id,
                )
                counters["errors"] += 1
                continue
            target_date_iso, parsed_niche = parsed
            # Prefer column when both agree; if they disagree, prefer
            # the column (structured data trumps parsed body — the
            # parse might match a niche name that appears in a
            # candidate hook body, though the anchoring regex above
            # makes that unlikely).
            niche = column_niche or parsed_niche

            filled = _query_slot_is_filled(niche, target_date_iso)
            if filled is None:
                # DB error querying blueprints — leave alert visible,
                # try again next sweep.
                counters["skipped_query_failed"] += 1
                continue
            if not filled:
                logger.debug(
                    "[alert_auto_resolver] row %s: niche=%s date=%s slot still empty; skipping",
                    row_id,
                    niche,
                    target_date_iso,
                )
                counters["skipped_slot_still_empty"] += 1
                continue

            # Slot IS filled — auto-resolve.
            from datetime import UTC, datetime

            today_str = datetime.now(UTC).strftime("%Y-%m-%d")
            note = (
                f"\n\n[AUTO-RESOLVED {today_str}: slot {target_date_iso} for "
                f"'{niche}' is now filled (blueprint scheduled + approved).]"
            )

            if dry_run:
                logger.info(
                    "[alert_auto_resolver] DRY-RUN would resolve row %s (niche=%s date=%s)",
                    row_id,
                    niche,
                    target_date_iso,
                )
                counters["resolved"] += 1
                continue

            try:
                with _connect() as write_conn:  # type: ignore[union-attr]
                    if write_conn is None:
                        logger.warning(
                            "[alert_auto_resolver] reconnect for missing_slot UPDATE failed; row %s left unresolved",
                            row_id,
                        )
                        counters["errors"] += 1
                        continue
                    with write_conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pipeline_alerts
                            SET resolved_at = NOW(),
                                message = COALESCE(message, '') || %s
                            WHERE id = %s
                              AND resolved_at IS NULL
                            """,
                            (note, row_id),
                        )
                logger.info(
                    "[alert_auto_resolver] resolved missing_slot row %s (niche=%s date=%s)",
                    row_id,
                    niche,
                    target_date_iso,
                )
                counters["resolved"] += 1
            except Exception as exc:  # noqa: BLE001 — fail-open per row
                logger.warning(
                    "[alert_auto_resolver] UPDATE missing_slot row %s failed: %s",
                    row_id,
                    exc,
                )
                counters["errors"] += 1

        except Exception as exc:  # noqa: BLE001 — fail-open per row
            logger.warning(
                "[alert_auto_resolver] unexpected error on missing_slot row %s: %s",
                row_id,
                exc,
            )
            counters["errors"] += 1

    return counters


# Re-export for callers that prefer importing the typing helper.
__all__ = [
    "auto_resolve_nightly_schedule_missing_slot_alerts",
    "auto_resolve_systemd_unit_alerts",
]


def _summary_json(counters: dict[str, Any]) -> str:
    """Render a counter dict as a single-line JSON string for log
    aggregation tooling. Separated so the CLI wrapper can use it
    without re-importing json there."""
    import json

    return json.dumps(counters, sort_keys=True)
