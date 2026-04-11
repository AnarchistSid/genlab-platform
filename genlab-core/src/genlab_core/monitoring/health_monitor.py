"""Pipeline health monitor — detects failures and attempts auto-remediation.

Runs as:
  - Post-pipeline check (fast, single-niche)
  - Standalone timer (full cross-run + system health)

Usage:
    python -m genlab_core.monitoring.health_monitor          # full check
    python -m genlab_core.monitoring.health_monitor --niche anime  # single niche
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

RUNS_DIR = pathlib.Path(os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")) / ".tmp" / "runs"
NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]


class Alert:
    """A detected health issue."""

    def __init__(
        self,
        check: str,
        severity: str,
        message: str,
        niche_id: str = "",
        details: dict | None = None,
        auto_fix: str = "",
    ):
        self.check = check
        self.severity = severity  # "critical" or "warning"
        self.message = message
        self.niche_id = niche_id
        self.details = details or {}
        self.auto_fix = auto_fix

    def __repr__(self) -> str:
        n = f"[{self.niche_id}] " if self.niche_id else ""
        fix = f" (auto-fix: {self.auto_fix})" if self.auto_fix else ""
        return f"[{self.severity.upper()}] {n}{self.check}: {self.message}{fix}"


def _load_recent_reports(niche_id: str, days: int = 3) -> list[dict]:
    """Load run_report.json files for a niche from the last N days."""
    prefix = f"{niche_id}_"
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    reports = []
    if not RUNS_DIR.exists():
        return reports
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not d.name.startswith(prefix) or not d.is_dir():
            continue
        report = d / "run_report.json"
        if not report.exists():
            continue
        try:
            data = json.loads(report.read_text())
            ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                break
            data["_run_dir"] = str(d)
            reports.append(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return reports


def _load_clip_index(run_dir: str) -> dict:
    """Load clip_index.json from a run directory."""
    ci = pathlib.Path(run_dir) / "clip_index.json"
    if ci.exists():
        try:
            return json.loads(ci.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


# ── Pipeline checks (per-run) ────────────────────────────────────────


def check_download_failures(reports: list[dict], niche_id: str) -> list[Alert]:
    """Check if yt-dlp downloads are consistently failing."""
    alerts = []
    consecutive_fails = 0
    for r in reports:
        ci = _load_clip_index(r.get("_run_dir", ""))
        total = ci.get("videos_total", 0)
        downloaded = ci.get("videos_downloaded", 0)
        if total > 0 and downloaded == 0:
            consecutive_fails += 1
        else:
            break

    if consecutive_fails >= 2:
        alert = Alert(
            check="download_failure",
            severity="critical",
            message=f"{consecutive_fails} consecutive runs with 0 clip downloads",
            niche_id=niche_id,
            details={"consecutive_fails": consecutive_fails},
        )
        # Auto-fix: try updating yt-dlp
        try:
            _uv = os.environ.get("UV_PATH", "/usr/local/bin/uv")
            result = subprocess.run(
                [_uv, "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=60,
                cwd=os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab"),
            )
            alert.auto_fix = f"yt-dlp update: {'success' if result.returncode == 0 else 'failed'}"
        except Exception as e:
            alert.auto_fix = f"yt-dlp update failed: {e}"
        alerts.append(alert)
    return alerts


def check_zero_blueprints(reports: list[dict], niche_id: str) -> list[Alert]:
    """Check for consecutive runs producing 0 blueprints."""
    alerts = []
    consecutive_zero = 0
    for r in reports:
        if r["metrics"]["blueprints_count"] == 0 and r["metrics"]["stories_count"] > 0:
            consecutive_zero += 1
        else:
            break

    if consecutive_zero >= 3:
        # Diagnose which stage lost the content
        latest = reports[0] if reports else {}
        m = latest.get("metrics", {})
        diagnosis = []
        ci = _load_clip_index(latest.get("_run_dir", ""))
        if ci.get("videos_total", 0) > 0 and ci.get("videos_downloaded", 0) == 0:
            diagnosis.append("downloads failing (yt-dlp?)")
        qc = m.get("qc", {})
        if qc.get("pass_rate") == "0.0%":
            diagnosis.append("QC 0% (no content written?)")
        if m.get("stories_count", 0) > 0 and m.get("blueprints_count", 0) == 0:
            diagnosis.append("stories created but 0 blueprints (dedup?)")

        alerts.append(Alert(
            check="zero_blueprints",
            severity="critical",
            message=f"{consecutive_zero} consecutive runs with 0 blueprints. Likely: {', '.join(diagnosis) or 'unknown'}",
            niche_id=niche_id,
            details={"consecutive_zero": consecutive_zero, "diagnosis": diagnosis},
        ))
    return alerts


def check_qc_collapse(reports: list[dict], niche_id: str) -> list[Alert]:
    """Check for QC pass rate at 0% for multiple runs."""
    alerts = []
    zero_qc_runs = sum(
        1 for r in reports[:5]
        if r.get("metrics", {}).get("qc", {}).get("pass_rate") == "0.0%"
    )
    if zero_qc_runs >= 2:
        alerts.append(Alert(
            check="qc_collapse",
            severity="critical",
            message=f"QC at 0% for {zero_qc_runs} of last {min(5, len(reports))} runs",
            niche_id=niche_id,
        ))
    return alerts


def check_source_starvation(reports: list[dict], niche_id: str) -> list[Alert]:
    """Check if source fetch is returning too few videos."""
    alerts = []
    if reports:
        latest = reports[0]
        tv_path = pathlib.Path(latest.get("_run_dir", "")) / "trending_videos.json"
        if tv_path.exists():
            try:
                vids = json.loads(tv_path.read_text())
                if len(vids) < 3:
                    alerts.append(Alert(
                        check="source_starvation",
                        severity="warning",
                        message=f"Only {len(vids)} videos fetched (< 3 minimum)",
                        niche_id=niche_id,
                    ))
                # Check single-source dependency
                channels = set(v.get("channel_name", "") for v in vids)
                if len(channels) == 1 and len(vids) > 3:
                    alerts.append(Alert(
                        check="single_source",
                        severity="warning",
                        message=f"All {len(vids)} videos from single channel: {channels.pop()}",
                        niche_id=niche_id,
                    ))
            except (json.JSONDecodeError, ValueError):
                pass
    return alerts


def check_bandit_staleness(niche_id: str) -> list[Alert]:
    """Check if bandit arms haven't been updated recently."""
    alerts = []
    try:
        import psycopg
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            "SELECT max(updated_at) FROM bandit_arms WHERE niche_id = %s",
            (niche_id,),
        )
        last_update = cur.fetchone()[0]
        conn.close()
        if last_update:
            days_stale = (datetime.now(timezone.utc) - last_update).days
            if days_stale > 7:
                alerts.append(Alert(
                    check="bandit_stale",
                    severity="warning",
                    message=f"Bandit arms not updated in {days_stale} days",
                    niche_id=niche_id,
                    details={"days_stale": days_stale, "last_update": last_update.isoformat()},
                ))
    except Exception as e:
        logger.debug("Bandit staleness check failed: %s", e)
    return alerts


# ── Publishing checks ─────────────────────────────────────────────────


def check_missing_media(niche_id: str) -> list[Alert]:
    """Check VISUAL_READY blueprints for missing video files."""
    alerts = []
    try:
        import psycopg
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, extra->>'visual_paths' FROM blueprints "
            "WHERE niche_id = %s AND status = 'VISUAL_READY'",
            (niche_id,),
        )
        broken = []
        for bp_id, title, vp in cur.fetchall():
            if not vp:
                broken.append(bp_id)
                continue
            try:
                paths = json.loads(vp) if vp.startswith("[") else [vp]
            except (json.JSONDecodeError, ValueError):
                paths = [vp]
            if not any(p and pathlib.Path(p).exists() for p in paths):
                broken.append(bp_id)

        if broken:
            # Auto-fix: archive broken blueprints
            cur.execute(
                "UPDATE blueprints SET status = 'ARCHIVED', "
                "action_taken = 'auto_archived_missing_media' "
                "WHERE id = ANY(%s)",
                (broken,),
            )
            conn.commit()
            alerts.append(Alert(
                check="missing_media",
                severity="critical",
                message=f"{len(broken)} VISUAL_READY blueprints with missing media files",
                niche_id=niche_id,
                auto_fix=f"Archived {len(broken)} blueprints",
            ))
        conn.close()
    except Exception as e:
        logger.debug("Missing media check failed: %s", e)
    return alerts


def check_content_gap(niche_id: str) -> list[Alert]:
    """Check if a niche has no scheduled content for the next 48h."""
    alerts = []
    try:
        import psycopg
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM blueprints "
            "WHERE niche_id = %s AND status = 'VISUAL_READY' "
            "AND action_taken = 'approved' AND scheduled_for IS NOT NULL "
            "AND scheduled_for BETWEEN NOW() AND NOW() + INTERVAL '48 hours'",
            (niche_id,),
        )
        count = cur.fetchone()[0]
        conn.close()
        if count == 0:
            alerts.append(Alert(
                check="content_gap",
                severity="warning",
                message="No approved+scheduled content for next 48 hours",
                niche_id=niche_id,
            ))
    except Exception as e:
        logger.debug("Content gap check failed: %s", e)
    return alerts


def check_publish_failures(niche_id: str) -> list[Alert]:
    """Check for high publish failure rate in last 24h."""
    alerts = []
    try:
        import psycopg
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            "SELECT status, count(*) FROM publishing_analytics "
            "WHERE niche_id = %s AND created_at > NOW() - INTERVAL '24 hours' "
            "GROUP BY status",
            (niche_id,),
        )
        by_status = dict(cur.fetchall())
        conn.close()
        failed = by_status.get("FAILED", 0)
        if failed >= 5:
            alerts.append(Alert(
                check="publish_failures",
                severity="critical",
                message=f"{failed} FAILED publishes in last 24h",
                niche_id=niche_id,
                details={"status_breakdown": by_status},
            ))
    except Exception as e:
        logger.debug("Publish failures check failed: %s", e)
    return alerts


# ── System checks ─────────────────────────────────────────────────────


def check_disk() -> list[Alert]:
    """Check disk usage on root and media volumes."""
    alerts = []
    try:
        result = subprocess.run(
            ["df", "--output=pcent,target", "/", "/mnt/genlab-media"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                pct = int(parts[0].replace("%", ""))
                mount = parts[1]
                if pct > 85:
                    alerts.append(Alert(
                        check="disk_pressure",
                        severity="critical" if pct > 90 else "warning",
                        message=f"{mount} at {pct}% usage",
                    ))
    except Exception as e:
        logger.debug("Disk check failed: %s", e)
    return alerts


def check_services() -> list[Alert]:
    """Check for failed systemd services and attempt restart."""
    alerts = []
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "genlab-*", "--state=failed", "--no-pager", "--plain"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            if "failed" not in line.lower():
                continue
            unit = line.split()[0] if line.split() else ""
            if not unit:
                continue
            # Attempt restart
            fix_result = subprocess.run(
                ["systemctl", "restart", unit],
                capture_output=True, text=True, timeout=15,
            )
            fix_status = "restarted" if fix_result.returncode == 0 else "restart failed"
            alerts.append(Alert(
                check="service_down",
                severity="critical",
                message=f"{unit} is in failed state",
                auto_fix=fix_status,
            ))
    except Exception as e:
        logger.debug("Service check failed: %s", e)
    return alerts


def check_swap() -> list[Alert]:
    """Check if swap usage is high (memory pressure)."""
    alerts = []
    try:
        result = subprocess.run(["free", "-b"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            if line.startswith("Swap:"):
                parts = line.split()
                total = int(parts[1])
                used = int(parts[2])
                if total > 0 and used > 500 * 1024 * 1024:  # >500MB swap
                    alerts.append(Alert(
                        check="swap_pressure",
                        severity="warning",
                        message=f"Swap at {used // (1024*1024)}MB / {total // (1024*1024)}MB",
                    ))
    except Exception as e:
        logger.debug("Swap check failed: %s", e)
    return alerts


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
        all_alerts.extend(check_bandit_staleness(nid))
        all_alerts.extend(check_missing_media(nid))
        all_alerts.extend(check_content_gap(nid))
        all_alerts.extend(check_publish_failures(nid))

    # System-wide checks (only on full runs)
    if niche_id is None:
        all_alerts.extend(check_disk())
        all_alerts.extend(check_services())
        all_alerts.extend(check_swap())

    return all_alerts


def write_alerts_to_db(alerts: list[Alert]) -> int:
    """Write alerts to pipeline_alerts table. Returns count written."""
    if not alerts:
        return 0
    try:
        import psycopg
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
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

        written = 0
        for alert in alerts:
            # Deduplicate: don't create if an unresolved alert for same check+niche exists
            cur.execute(
                "SELECT id FROM pipeline_alerts "
                "WHERE check_name = %s AND niche_id IS NOT DISTINCT FROM %s "
                "AND resolved_at IS NULL",
                (alert.check, alert.niche_id or None),
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
                    json.dumps(alert.details) if alert.details else None,
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
        import psycopg
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
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

    # Output
    if args.json:
        import json as _json
        print(_json.dumps([
            {
                "check": a.check,
                "severity": a.severity,
                "niche_id": a.niche_id,
                "message": a.message,
                "auto_fix": a.auto_fix,
            }
            for a in alerts
        ], indent=2))
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


if __name__ == "__main__":
    main()
