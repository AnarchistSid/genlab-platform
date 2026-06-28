"""Detect Anthropic credit-balance exhaustion and surface as a CRITICAL
alert in pipeline_alerts.

Why this exists
---------------
On 2026-06-28 the production Anthropic API key returned, repeatedly:

    {"type":"error","error":{"type":"invalid_request_error",
     "message":"Your credit balance is too low to access the Anthropic
     API. Please go to Plans & Billing to upgrade or purchase credits."}}

The key was valid; only the credit balance was exhausted. Pipelines
silently degraded for ``gaming``, ``sports``, and ``movies`` (0 new
blueprints) because ``ai_creators`` and ``anime`` had already
consumed the remaining credit earlier in the schedule. The symptom
only surfaced because the operator traced ``gaming``'s
``zero_blueprints`` alert by hand and ran ``curl`` against the API.

The next time the credit balance falls low, the operator should learn
within minutes via Mission Control's CRITICAL alerts banner — not
days later from missing content. That's what this monitor does.

How it works
------------
Every 15 min via systemd timer:

  1. Run ``journalctl --since "60 minutes ago" --no-pager -q`` and
     scan stdout for the literal phrase ``"credit balance is too
     low"`` (verbatim text from the Anthropic API error body).
  2. If matches == 0 → return cleanly (nothing to do).
  3. If matches >= 1 → check ``pipeline_alerts`` for an existing
     UNRESOLVED ``check_name='anthropic_credit_exhausted'`` row
     created in the last 24h. If present, skip (Mission Control is
     already showing the banner; we don't need a second one).
  4. Otherwise → INSERT a CRITICAL row with operator-actionable text
     including the console billing URL + a truncated sample line
     from the journal.

Failure mode
------------
Fail-OPEN throughout. The whole point of this monitor is to ADD
signal, not REMOVE it — any error in scan or DB write is logged at
WARNING and counted in ``errors`` but does not raise. A miss costs
at most one timer interval (15 min) before the next sweep catches up.

Why 60-min window + 15-min interval
-----------------------------------
* 15-min cadence is the right speed for credit exhaustion: when
  credit runs out, *every* downstream LLM call fails in succession
  for the rest of the day. Slower detection costs an entire pipeline
  run-cycle's worth of content. Faster (e.g. 5 min) would spam
  ``journalctl`` calls during the steady state with no payoff.
* 60-min lookback window with 4x headroom over the 15-min interval
  gives us tolerance for a missed fire (e.g. post-reboot timer
  catchup) without re-detecting events the dedupe layer already
  handled.

Why dedupe by check_name + 24h + UNRESOLVED
-------------------------------------------
* ``check_name='anthropic_credit_exhausted'`` is unique to this
  monitor — no collision with the failure-alert wire or
  health-monitor checks.
* 24h dedupe matches operator expectations: if you topped up
  yesterday and it broke again, that IS a new incident worth a
  fresh CRITICAL row. If you haven't acted yet, no extra signal
  needed — the banner is still showing the original.
* UNRESOLVED filter means an operator who marked the row resolved
  (acknowledging + acting) will see a NEW row on the next
  occurrence — that's the right behavior; the resolved row is a
  closed historical incident.

Public surface
--------------
``scan_and_alert_on_credit_exhaustion(*, window_minutes=60,
dry_run=False) -> dict``
    Returns: ``{"matches_found": int, "alert_written": bool,
    "dedupe_skip": bool, "errors": int}``
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


# Literal phrase from the Anthropic API error body. Verified against
# prod 2026-06-28 via direct ``curl``. Matching the literal string is
# more robust than parsing JSON because the same text appears in
# Python tracebacks, structured-log entries, and platform-specific
# wrappers — all of which our pipelines emit at WARNING level when
# the SDK raises.
_CREDIT_LOW_PATTERN = "credit balance is too low"

# Truncate journal-sample lines to this length before storing in the
# alert message body. Prevents a runaway 8KB stacktrace from blowing
# up the pipeline_alerts.message column when the operator scrolls
# the banner.
_SAMPLE_LINE_MAX = 200


def _connect():
    """Open a psycopg connection from DATABASE_URL — returns None on
    missing DSN / connection failure. Caller fails OPEN.

    Mirrors ``alert_auto_resolver._connect`` so behavioural changes
    in the canonical pattern stay in sync."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[anthropic_credit_monitor] DATABASE_URL not set; monitor disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[anthropic_credit_monitor] connection failed: %s", exc)
        return None


def _scan_journal(window_minutes: int) -> tuple[int, str | None]:
    """Run ``journalctl`` for the last ``window_minutes`` and count
    occurrences of the credit-low pattern.

    Returns ``(matches_found, sample_line_or_none)``. ``sample_line``
    is the FIRST matching line, truncated to ``_SAMPLE_LINE_MAX``
    chars — provides operator context without bloating the alert
    message.

    Raises on subprocess failure (caller catches + counts as error).
    The whole call is wrapped in a 30s timeout to prevent journal
    locks from hanging the sweep."""
    proc = subprocess.run(
        [
            "journalctl",
            "--since",
            f"{window_minutes} minutes ago",
            "--no-pager",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # journalctl exit code is non-zero when the user lacks permission
    # OR when no entries match. Treat non-zero as "couldn't scan" and
    # bubble up so the caller's errors counter ticks — better to know
    # we missed a window than to silently report 0 matches.
    if proc.returncode != 0:
        logger.warning(
            "[anthropic_credit_monitor] journalctl exit=%d stderr=%r",
            proc.returncode,
            (proc.stderr or "")[:200],
        )
        # An empty journal range legitimately returns 0 + no output, so
        # only flag non-zero exit. (journalctl returns 0 even on empty
        # results in normal operation.)
        raise RuntimeError(f"journalctl exit={proc.returncode}")

    matches = 0
    sample_line: str | None = None
    for line in proc.stdout.splitlines():
        if _CREDIT_LOW_PATTERN in line:
            matches += 1
            if sample_line is None:
                # Strip control chars + trim to keep message tidy
                cleaned = "".join(c for c in line if c.isprintable())
                if len(cleaned) > _SAMPLE_LINE_MAX:
                    cleaned = cleaned[: _SAMPLE_LINE_MAX - 3] + "..."
                sample_line = cleaned
    return matches, sample_line


def _has_recent_unresolved_alert(conn) -> bool:
    """True if pipeline_alerts already has an UNRESOLVED row with
    ``check_name='anthropic_credit_exhausted'`` created within the
    last 24 hours. Caller uses this to skip duplicate inserts (the
    operator's banner is already lit).

    Returns False on any query error (fail-OPEN: better to write a
    duplicate alert than to miss a real exhaustion event)."""
    try:
        rows = conn.execute(
            """
            SELECT id
            FROM pipeline_alerts
            WHERE check_name = 'anthropic_credit_exhausted'
              AND resolved_at IS NULL
              AND created_at > NOW() - INTERVAL '24 hours'
            LIMIT 1
            """
        ).fetchall()
        return len(rows) > 0
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[anthropic_credit_monitor] dedupe SELECT failed: %s", exc)
        return False


def _build_message(
    *,
    matches: int,
    window_minutes: int,
    sample_line: str | None,
) -> str:
    """Compose the operator-actionable CRITICAL message body.

    Includes (a) what happened, (b) impact across the system, and
    (c) one-click action link. Format matches the rest of the alert
    suite's multi-line style."""
    from datetime import UTC, datetime

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_block = f"\nSample error line:\n{sample_line}" if sample_line else ""
    return (
        f"Anthropic API credit balance exhausted at {ts}.\n"
        f"{matches} occurrences in journalctl over last "
        f"{window_minutes}m.\n"
        "\n"
        "Impact: pipelines that depend on Anthropic (writing, hooks, "
        "enrichment, critique-rewriter, conformal router, Bayesian "
        "gate) will silently degrade or produce 0 blueprints. Today's "
        "IG publishes still work (pre-rendered content), but "
        "tomorrow's queues are at risk.\n"
        "\n"
        "Action: top up at "
        "https://console.anthropic.com/settings/billing"
        f"{sample_block}"
    )


def scan_and_alert_on_credit_exhaustion(
    *,
    window_minutes: int = 60,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan recent journalctl output for the Anthropic credit-low
    pattern; if matched and no alert already fired today, write a
    CRITICAL row to ``pipeline_alerts``.

    Parameters
    ----------
    window_minutes : int, default 60
        How far back to scan ``journalctl``. Default of 60 gives 4x
        headroom over the 15-min timer interval — enough to recover
        from a missed fire (e.g. post-reboot catchup) without
        re-detecting events the dedupe layer already handled.
    dry_run : bool, default False
        When True, the scan runs but no DB INSERT is executed.
        Returned ``alert_written`` reflects what WOULD have been
        written.

    Returns
    -------
    dict
        ``{"matches_found": int, "alert_written": bool,
        "dedupe_skip": bool, "errors": int}``

    Notes
    -----
    Never raises into the caller. Fail-OPEN on every external call.
    """
    summary: dict[str, Any] = {
        "matches_found": 0,
        "alert_written": False,
        "dedupe_skip": False,
        "errors": 0,
    }

    # ── 1. scan journalctl ─────────────────────────────────────────
    try:
        matches, sample_line = _scan_journal(window_minutes)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("[anthropic_credit_monitor] journalctl call failed: %s", exc)
        summary["errors"] = 1
        return summary
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[anthropic_credit_monitor] unexpected scan error: %s", exc)
        summary["errors"] = 1
        return summary

    summary["matches_found"] = matches

    if matches == 0:
        logger.debug(
            "[anthropic_credit_monitor] no credit-low entries in last %dm",
            window_minutes,
        )
        return summary

    # ── 2. dedupe + write ──────────────────────────────────────────
    conn_cm = _connect()
    if conn_cm is None:
        # No DB — log + early-return. The timer's next fire will
        # retry. We DO NOT increment errors here because absence of a
        # DSN is a configuration state, not an error per se (mirrors
        # alert_auto_resolver behavior).
        return summary

    try:
        with conn_cm as conn:
            if _has_recent_unresolved_alert(conn):
                logger.info(
                    "[anthropic_credit_monitor] %d credit-low matches "
                    "found but unresolved alert already exists (<24h); "
                    "skipping duplicate insert",
                    matches,
                )
                summary["dedupe_skip"] = True
                return summary

            message = _build_message(
                matches=matches,
                window_minutes=window_minutes,
                sample_line=sample_line,
            )

            if dry_run:
                logger.info(
                    "[anthropic_credit_monitor] DRY-RUN would write CRITICAL alert: matches=%d",
                    matches,
                )
                summary["alert_written"] = True
                return summary

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pipeline_alerts (
                        niche_id, check_name, severity, message,
                        created_at, resolved_at
                    ) VALUES (
                        %s, %s, %s, %s, NOW(), NULL
                    )
                    """,
                    (
                        "all",
                        "anthropic_credit_exhausted",
                        "critical",
                        message,
                    ),
                )
            logger.warning(
                "[anthropic_credit_monitor] wrote CRITICAL alert (matches=%d, window=%dm)",
                matches,
                window_minutes,
            )
            summary["alert_written"] = True
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[anthropic_credit_monitor] DB write path failed: %s", exc)
        summary["errors"] = 1

    return summary


def _summary_json(summary: dict[str, Any]) -> str:
    """Render a summary dict as a single-line JSON string for log
    aggregation tooling. Separated so the CLI wrapper can use it
    without re-importing json there."""
    import json

    return json.dumps(summary, sort_keys=True)


__all__ = ["scan_and_alert_on_credit_exhaustion"]
