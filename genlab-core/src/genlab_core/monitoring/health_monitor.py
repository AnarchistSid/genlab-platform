"""Pipeline health monitor — detects failures and attempts auto-remediation.

Runs as:
  - Post-pipeline check (fast, single-niche)
  - Standalone timer (full cross-run + system health)

Usage:
    python -m genlab_core.monitoring.health_monitor          # full check
    python -m genlab_core.monitoring.health_monitor --niche anime  # single niche

**Module shape after the 2026-07-08 god-module split (DEV-1)**:
this file is now a facade + orchestrator. The individual check
implementations live in ``genlab_core.monitoring.checks.{pipeline,
infrastructure, bandit_engagement}`` and are re-exported here so
existing ``from genlab_core.monitoring.health_monitor import X``
callers (and tests that patch ``…health_monitor.X`` for late-bound
symbol lookups) keep working unchanged. The orchestrator
(``run_all_checks``), the DB write path (``write_alerts_to_db``),
the resolve sweep, the notify webhook, and the CLI ``main()`` all
stay here — they compose the check surface but never live inside
one check group.
"""

from __future__ import annotations

import json
import logging
import os

# ``subprocess`` is kept at module top level so tests that
# ``patch("genlab_core.monitoring.health_monitor.subprocess.run")`` and
# ``patch.object(health_monitor.subprocess, "run", …)`` continue to affect
# ``checks/infrastructure.py::check_swap`` (which resolves ``subprocess``
# through this facade). Do not remove even though nothing in this file
# calls ``subprocess`` directly.
import subprocess  # noqa: F401

from genlab_core.monitoring._report_loaders import (
    NICHES,
    RUNS_DIR,
    _load_clip_index,
    _load_recent_metrics,
    _load_recent_reports,
)
from genlab_core.monitoring.alerts import Alert
from genlab_core.monitoring.checks.bandit_engagement import (
    archive_stranded_engagement_reviews,
    check_bandit_posterior_drift,
    check_bandit_staleness,
    check_engagement_health,
    detect_dead_pollers,
)
from genlab_core.monitoring.checks.infrastructure import (
    _attempt_warp_restart,
    _check_warp_port_listening,
    check_disk,
    check_foreign_host_writes,
    check_git_drift,
    check_services,
    check_swap,
    check_warp_health,
)
from genlab_core.monitoring.checks.pipeline import (
    _FETCHER_STAGES_TO_MONITOR,
    _SILENT_FAILURE_CONSECUTIVE_RUNS,
    _SILENT_FAILURE_DURATION_MS,
    archive_orphan_drafts,
    archive_orphan_intake_stories,
    check_content_gap,
    check_content_pool_health,
    check_download_failures,
    check_fetcher_stage_silent_failures,
    check_missing_media,
    check_publish_failures,
    check_publish_silence,
    check_qc_collapse,
    check_source_starvation,
    check_stuck_publishing,
    check_zero_blueprints,
)
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-5

logger = logging.getLogger(__name__)


# ── Orchestrator ──────────────────────────────────────────────────────


def run_all_checks(niche_id: str | None = None) -> list[Alert]:
    """Run all health checks. If niche_id is None, checks all niches."""
    all_alerts: list[Alert] = []
    niches = [niche_id] if niche_id else NICHES

    for nid in niches:
        reports = _load_recent_reports(nid, days=3)
        all_alerts.extend(check_download_failures(reports, nid))
        all_alerts.extend(check_zero_blueprints(reports, nid))
        all_alerts.extend(check_qc_collapse(reports, nid))
        all_alerts.extend(check_source_starvation(reports, nid))
        # 2026-06-30 (COMMIT 3 / B2): detect silent-no-op fetchers
        # (sources_config-style bug). Reads .tmp/runs/*/metrics.jsonl
        # for each niche, fires warning if a fetcher stage reports
        # duration_ms < 1.0 across 3 consecutive runs.
        all_alerts.extend(check_fetcher_stage_silent_failures(nid))
        all_alerts.extend(check_bandit_staleness(nid))
        all_alerts.extend(check_bandit_posterior_drift(nid))
        all_alerts.extend(check_missing_media(nid))
        all_alerts.extend(check_stuck_publishing(nid))
        all_alerts.extend(check_content_gap(nid))
        all_alerts.extend(check_publish_failures(nid))
        all_alerts.extend(check_publish_silence(nid))
        all_alerts.extend(archive_orphan_drafts(nid))
        all_alerts.extend(archive_orphan_intake_stories(nid))
        # 2026-06-14 engagement-loop audit follow-ups (PR #199):
        all_alerts.extend(archive_stranded_engagement_reviews(nid))
        all_alerts.extend(detect_dead_pollers(nid))

    # System-wide checks (only on full runs)
    if niche_id is None:
        all_alerts.extend(check_disk())
        all_alerts.extend(check_services())
        all_alerts.extend(check_swap())
        all_alerts.extend(check_foreign_host_writes())
        all_alerts.extend(check_git_drift())
        all_alerts.extend(check_warp_health())
        # PR #516 (2026-06-24): infrastructure-half-wired audit probes
        all_alerts.extend(check_engagement_health())
        all_alerts.extend(check_content_pool_health())

    return all_alerts


def _alert_details_json_default(obj: object) -> str:
    """JSON-serialize objects that ``json.dumps`` doesn't handle natively.

    Some checks include UUIDs (e.g. blueprint record ids in archive
    counts) or datetimes (timestamp fields on the alert payload) in
    ``alert.details``. Default ``json.dumps`` raises ``TypeError:
    Object of type UUID is not JSON serializable`` on those, which
    caused ``write_alerts_to_db`` to fail the WHOLE INSERT (and the
    rest of the loop) on every hourly health_monitor run. Per the
    2026-06-14 prod log probe: 16+ occurrences in the visible
    window, one per fire.

    Coerce these to their canonical string form. Same shape the
    backlog_client's PostgresBackend serializer uses for the
    Postgres JSONB column path.
    """
    from datetime import date, datetime
    from uuid import UUID

    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    # Last-resort stringification — better to land a row with a
    # slightly-mangled detail than to drop the whole alert. The
    # check_name + message + severity carry the actionable signal;
    # details is the "nice to have" enrichment.
    return repr(obj)


def write_alerts_to_db(alerts: list[Alert]) -> int:
    """Write alerts to pipeline_alerts table. Returns count written."""
    if not alerts:
        return 0
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id="all")
        cur = conn.cursor()

        # Ensure table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_alerts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                niche_id TEXT,
                check_name TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                message TEXT NOT NULL,
                details JSONB,
                auto_fix_applied TEXT,
                auto_fix_result TEXT,
                resolved_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Grace period after manual resolve.  Configurable via env var; default
        # 1 hour.  When an operator resolves an alert, the next health monitor
        # tick would normally re-create it immediately if the underlying
        # condition is still observed (e.g. zero-download runs from this morning
        # still in the 3-day _load_recent_reports window).  The grace period
        # treats recently-resolved alerts as still-suppressed so manual
        # resolutions stick long enough for the underlying condition to clear.
        grace = os.environ.get("ALERT_RESOLVE_GRACE", "1 hour")

        written = 0
        for alert in alerts:
            # Deduplicate: skip if an unresolved alert exists OR a same-shape
            # alert was resolved within the grace window.
            cur.execute(
                "SELECT id FROM pipeline_alerts "
                "WHERE check_name = %s AND niche_id IS NOT DISTINCT FROM %s "
                "AND (resolved_at IS NULL OR resolved_at > NOW() - %s::interval)",
                (alert.check, alert.niche_id or None, grace),
            )
            if cur.fetchone():
                continue

            cur.execute(
                "INSERT INTO pipeline_alerts "
                "(niche_id, check_name, severity, message, details, auto_fix_applied) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    alert.niche_id or None,
                    alert.check,
                    alert.severity,
                    alert.message,
                    json.dumps(alert.details, default=_alert_details_json_default)
                    if alert.details
                    else None,
                    alert.auto_fix or None,
                ),
            )
            written += 1

        conn.commit()
        conn.close()
        return written
    except Exception as e:
        logger.error("Failed to write alerts to DB: %s", e)
        return 0


def resolve_stale_alerts() -> int:
    """Auto-resolve alerts older than 24h (they'll be re-created if still active)."""
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id="all")
        cur = conn.cursor()
        cur.execute(
            "UPDATE pipeline_alerts SET resolved_at = NOW() "
            "WHERE resolved_at IS NULL AND created_at < NOW() - INTERVAL '24 hours'"
        )
        resolved = cur.rowcount
        conn.commit()
        conn.close()
        return resolved
    except Exception:
        return 0


# ── CLI ───────────────────────────────────────────────────────────────


def notify(alerts: list[Alert]) -> bool:
    """Deliver CRITICAL alerts to a configured webhook (Slack-compatible).

    R-01: health_monitor previously only wrote to the ``pipeline_alerts`` table
    (which nothing reads), so a dark channel paged no one. This POSTs a summary
    of the critical alerts to ``GENLAB_ALERT_WEBHOOK_URL`` (e.g. a Slack incoming
    webhook — the same URL the dashboard stores as ``slack_webhook_url``). No-op
    when the URL is unset or there are no critical alerts. Best-effort: a delivery
    failure is logged, never raised.
    """
    url = os.environ.get("GENLAB_ALERT_WEBHOOK_URL", "").strip()
    critical = [a for a in alerts if a.severity == "critical"]
    if not url or not critical:
        return False

    lines = "\n".join(f"• {a}" for a in critical[:25])
    text = f"\U0001f6a8 Gen Lab health: {len(critical)} CRITICAL alert(s)\n{lines}"
    try:
        import requests

        requests.post(url, json={"text": text}, timeout=10)
        logger.info("Delivered %d critical alert(s) to webhook", len(critical))
        return True
    except Exception as e:
        logger.warning("Alert webhook delivery failed: %s", e)
        return False


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline health monitor")
    parser.add_argument("--niche", help="Check single niche (default: all)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Resolve old alerts first
    resolved = resolve_stale_alerts()
    if resolved:
        logger.info("Resolved %d stale alerts", resolved)

    # Run checks
    alerts = run_all_checks(niche_id=args.niche)

    # Write to DB
    written = write_alerts_to_db(alerts)

    # Deliver critical alerts to the configured webhook (R-01) — without this,
    # alerts only land in a table nothing reads and nobody is paged.
    notify(alerts)

    # Output
    if args.json:
        import json as _json

        print(
            _json.dumps(
                [
                    {
                        "check": a.check,
                        "severity": a.severity,
                        "niche_id": a.niche_id,
                        "message": a.message,
                        "auto_fix": a.auto_fix,
                    }
                    for a in alerts
                ],
                indent=2,
            )
        )
    else:
        if not alerts:
            print("All checks passed. No issues detected.")
        else:
            critical = [a for a in alerts if a.severity == "critical"]
            warnings = [a for a in alerts if a.severity == "warning"]
            if critical:
                print(f"\n{len(critical)} CRITICAL:")
                for a in critical:
                    print(f"  {a}")
            if warnings:
                print(f"\n{len(warnings)} WARNINGS:")
                for a in warnings:
                    print(f"  {a}")
            print(f"\n{written} new alerts written to DB.")


__all__ = [
    "NICHES",
    "RUNS_DIR",
    "Alert",
    "_FETCHER_STAGES_TO_MONITOR",
    "_SILENT_FAILURE_CONSECUTIVE_RUNS",
    "_SILENT_FAILURE_DURATION_MS",
    "_alert_details_json_default",
    "_attempt_warp_restart",
    "_check_warp_port_listening",
    "_load_clip_index",
    "_load_recent_metrics",
    "_load_recent_reports",
    "archive_orphan_drafts",
    "archive_orphan_intake_stories",
    "archive_stranded_engagement_reviews",
    "check_bandit_posterior_drift",
    "check_bandit_staleness",
    "check_content_gap",
    "check_content_pool_health",
    "check_disk",
    "check_download_failures",
    "check_engagement_health",
    "check_fetcher_stage_silent_failures",
    "check_foreign_host_writes",
    "check_git_drift",
    "check_missing_media",
    "check_publish_failures",
    "check_publish_silence",
    "check_qc_collapse",
    "check_services",
    "check_source_starvation",
    "check_stuck_publishing",
    "check_swap",
    "check_warp_health",
    "check_zero_blueprints",
    "detect_dead_pollers",
    "main",
    "notify",
    "resolve_stale_alerts",
    "run_all_checks",
    "write_alerts_to_db",
]


if __name__ == "__main__":
    main()
