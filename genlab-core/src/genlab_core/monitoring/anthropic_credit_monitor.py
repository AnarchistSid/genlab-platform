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

# Live-credit-check (task #621, 2026-07-09): before writing an alert
# based on journal evidence, hit the API directly to see if credit is
# currently exhausted RIGHT NOW. The 60-min journal window includes
# stragglers from BEFORE any recent top-up, so ``matches > 0`` does
# NOT mean credit is currently exhausted — it just means credit WAS
# exhausted somewhere in the last hour.
#
# Cost per call is trivial (~$0.00001): ``max_tokens=1`` on the cheapest
# Haiku model at 96 fires/day = ~$0.001/day total. Well worth eliminating
# false-positive banner flicker after every top-up.
_LIVE_CHECK_MODEL = "claude-haiku-4-5-20251001"
_LIVE_CHECK_TIMEOUT_S = 10.0


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


def _live_credit_check() -> str:
    """Attempt a minimal API call to determine current credit state.

    Task #621 (2026-07-09) — added to prevent stale-journal false
    positives. The journal scan looks at the last 60 minutes for the
    credit-low pattern, but that window continues to hit for up to
    60 minutes AFTER the operator tops up. The live check answers
    "is credit CURRENTLY exhausted?" so we don't fire an alert when
    the API is working right now.

    Returns
    -------
    ``"flowing"`` — the API accepted a minimal ``max_tokens=1`` call
        (or a non-credit-related error came back — auth, rate limit,
        etc — all of which mean credit itself isn't the current issue).
    ``"exhausted"`` — the API returned a body containing the
        credit-low pattern (real exhaustion right now).
    ``"unknown"`` — network error, missing API key, timeout, or any
        other issue that prevents us from knowing. Caller treats
        as fall-through: proceed to write alert based on journal
        evidence, since we can't rule out exhaustion. That's the
        safe default — better a false alert than a missed one.

    Cost per call: ~$0.00001 (5 input tokens + 1 output token on
    Haiku). At 96 fires/day: ~$0.001/day. Negligible.

    Uses ``urllib`` deliberately — no anthropic SDK dependency, so
    a broken SDK install (or a future SDK API break) can't disable
    the monitor's live check. The API is versioned via header so
    the shape stays stable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.debug(
            "[anthropic_credit_monitor] ANTHROPIC_API_KEY not set — "
            "live-credit-check returns 'unknown'"
        )
        return "unknown"

    try:
        import json
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "model": _LIVE_CHECK_MODEL,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "."}],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_LIVE_CHECK_TIMEOUT_S) as resp:
            # 200 status = call accepted = credit is flowing. We don't
            # care about the response body — just that Anthropic didn't
            # refuse it on billing grounds.
            if 200 <= resp.status < 300:
                return "flowing"
            return "unknown"
    except urllib.error.HTTPError as exc:
        # 4xx/5xx path. Inspect the error body for the credit-low
        # pattern — 400 "credit balance is too low" is the exact
        # shape we're trying to detect. Any other HTTP error (auth,
        # rate limit, server error) is NOT a credit issue and should
        # not fire this specific alert.
        try:
            body = exc.read().decode("utf-8", errors="replace").lower()
            if _CREDIT_LOW_PATTERN in body:
                return "exhausted"
        except Exception:  # noqa: BLE001 — body read is best-effort
            pass
        logger.debug(
            "[anthropic_credit_monitor] live check got HTTP %s (non-credit); treating as 'unknown'",
            exc.code,
        )
        return "unknown"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug(
            "[anthropic_credit_monitor] live check network/timeout: %s — 'unknown'",
            exc,
        )
        return "unknown"
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.debug(
            "[anthropic_credit_monitor] live check unexpected error: %s — 'unknown'",
            exc,
        )
        return "unknown"


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


def _auto_resolve_stale_alerts(
    conn,
    *,
    cooldown_minutes: int,
    dry_run: bool,
) -> int:
    """Auto-resolve unresolved credit-exhaustion alerts older than the
    cooldown window. Called only when the journal scan finds 0 matches
    — the absence of new errors means credit has been topped up. The
    cooldown gives the operator a chance to see the alert before it
    self-clears (prevents flapping during a brief transient).

    Returns the number of rows resolved (0 if dry_run, or on error).
    Fail-OPEN on every external call — a failed auto-resolve leaves
    the operator-visible alert intact, which is the safer state.
    """
    try:
        if dry_run:
            # Count what WOULD be resolved without executing the UPDATE.
            rows = conn.execute(
                """
                SELECT id
                FROM pipeline_alerts
                WHERE check_name = 'anthropic_credit_exhausted'
                  AND resolved_at IS NULL
                  AND created_at < NOW() - make_interval(mins => %s)
                """,
                (cooldown_minutes,),
            ).fetchall()
            return len(rows)

        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y-%m-%d")
        note = (
            f"\n\n[AUTO-RESOLVED {ts}: monitor scanned recent journalctl "
            "and found 0 credit-low matches; credit appears restored.]"
        )
        cursor = conn.execute(
            """
            UPDATE pipeline_alerts
            SET resolved_at = NOW(),
                message = message || %s
            WHERE check_name = 'anthropic_credit_exhausted'
              AND resolved_at IS NULL
              AND created_at < NOW() - make_interval(mins => %s)
            """,
            (note, cooldown_minutes),
        )
        # psycopg autocommits in 'autocommit' mode; if not, explicitly
        # commit here. The _connect() helper opens transaction mode by
        # default, so commit is required.
        try:
            conn.commit()
        except Exception:
            pass  # already-autocommit mode, no-op
        count = cursor.rowcount or 0
        if count > 0:
            logger.info(
                "[anthropic_credit_monitor] auto-resolved %d stale credit-low "
                "alert(s) (older than %dm, no new matches in scan window)",
                count,
                cooldown_minutes,
            )
        return count
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[anthropic_credit_monitor] auto-resolve failed: %s (alert left intact)",
            exc,
        )
        return 0


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
    resolve_cooldown_minutes: int = 30,
    enable_live_check: bool = True,
) -> dict[str, Any]:
    """Scan recent journalctl output for the Anthropic credit-low
    pattern; if matched and no alert already fired today, write a
    CRITICAL row to ``pipeline_alerts``. When NO matches are found
    AND an unresolved alert exists older than ``resolve_cooldown_minutes``,
    auto-resolve it — the absence of new errors means credit was
    topped up and the operator no longer needs the banner.

    Parameters
    ----------
    window_minutes : int, default 60
        How far back to scan ``journalctl``. Default of 60 gives 4x
        headroom over the 15-min timer interval — enough to recover
        from a missed fire (e.g. post-reboot catchup) without
        re-detecting events the dedupe layer already handled.
    dry_run : bool, default False
        When True, the scan runs but no DB INSERT or UPDATE is
        executed. Returned ``alert_written`` / ``alerts_resolved``
        reflect what WOULD have happened.
    resolve_cooldown_minutes : int, default 30
        Minimum alert age before auto-resolution fires. Prevents
        flapping during transient credit dips — the operator gets at
        least 30 minutes to see the alert before it self-clears.
        Default 30 = 2× the timer interval, balancing operator
        awareness against banner-cleanup latency.

    Returns
    -------
    dict
        ``{"matches_found": int, "alert_written": bool,
        "dedupe_skip": bool, "alerts_resolved": int, "errors": int}``

    Notes
    -----
    Never raises into the caller. Fail-OPEN on every external call.
    """
    summary: dict[str, Any] = {
        "matches_found": 0,
        "alert_written": False,
        "dedupe_skip": False,
        "alerts_resolved": 0,
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

    # ── 2. branch: 0 matches → auto-resolve path; >0 matches → write path ──
    conn_cm = _connect()
    if conn_cm is None:
        # No DB — log + early-return. The timer's next fire will
        # retry. We DO NOT increment errors here because absence of a
        # DSN is a configuration state, not an error per se (mirrors
        # alert_auto_resolver behavior).
        if matches == 0:
            logger.debug(
                "[anthropic_credit_monitor] no credit-low entries in last %dm "
                "(DB unavailable; no auto-resolve attempted)",
                window_minutes,
            )
        return summary

    if matches == 0:
        # 0 matches AND DB available → try auto-resolve stale alerts.
        # Credit appears restored (no new errors in the scan window);
        # any existing unresolved alert older than the cooldown is
        # safe to clear automatically.
        try:
            with conn_cm as conn:
                resolved = _auto_resolve_stale_alerts(
                    conn,
                    cooldown_minutes=resolve_cooldown_minutes,
                    dry_run=dry_run,
                )
                summary["alerts_resolved"] = resolved
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "[anthropic_credit_monitor] auto-resolve DB session failed: %s",
                exc,
            )
            summary["errors"] = 1
        return summary

    # ── 3. Live-credit-check (task #621, 2026-07-09) ─────────────────
    # ``matches > 0`` at this point only means credit-low errors appeared
    # in the last 60 minutes of journal — NOT that credit is currently
    # exhausted. After a top-up, journal stragglers persist for up to 60
    # minutes and would trigger a false alert every 15 minutes. Ask the
    # API directly: is credit flowing RIGHT NOW?
    #
    # Same discipline as ``alert_auto_resolver.auto_resolve_systemd_unit_alerts``
    # which asks systemd "has this unit had a successful run since?"
    # before considering an alert stale. Alerts should reflect CURRENT
    # state, not the last N minutes of noise.
    if enable_live_check:
        live_state = _live_credit_check()
        if live_state == "flowing":
            logger.info(
                "[anthropic_credit_monitor] %d journal matches BUT live API "
                "check returned OK — credit is currently flowing. Treating "
                "as stale-journal signal; auto-resolving any existing "
                "alerts (matches window includes pre-topup stragglers).",
                matches,
            )
            try:
                with conn_cm as conn:
                    resolved = _auto_resolve_stale_alerts(
                        conn,
                        cooldown_minutes=resolve_cooldown_minutes,
                        dry_run=dry_run,
                    )
                    summary["alerts_resolved"] = resolved
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.warning(
                    "[anthropic_credit_monitor] auto-resolve after live-check "
                    "DB session failed: %s",
                    exc,
                )
                summary["errors"] = 1
            return summary
        # else: live_state is "exhausted" or "unknown" — proceed to
        # write path. "exhausted" is definitive. "unknown" is the safe
        # default — better a false alert than a missed real one.
        logger.info(
            "[anthropic_credit_monitor] live check returned %r; proceeding "
            "to write alert based on %d journal matches",
            live_state,
            matches,
        )

    # ── 4. dedupe + write (matches > 0 AND live-check didn't clear) ──
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
