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
from datetime import UTC, datetime, timedelta

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
    cutoff = datetime.now(UTC) - timedelta(days=days)
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
        # WARP is the most common root cause of download failures on the
        # Hetzner datacenter (YouTube blocks the datacenter IP without it).
        # Skip the yt-dlp update if WARP is down — updating the downloader
        # binary doesn't fix a broken network proxy, and "yt-dlp update:
        # success" would be a misleading message.
        warp_alerts = check_warp_health()
        if warp_alerts:
            alert.auto_fix = (
                "skipped yt-dlp update — WARP proxy is down (see warp_down "
                "alert). yt-dlp can't fix a network-layer outage."
            )
        else:
            try:
                _uv = os.environ.get("UV_PATH", "/usr/local/bin/uv")
                result = subprocess.run(
                    [_uv, "pip", "install", "--upgrade", "yt-dlp"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab"),
                )
                if result.returncode == 0:
                    # Don't claim "success" — the pip install succeeded but
                    # that doesn't mean downloads will work.  Acknowledge
                    # the scope honestly.
                    alert.auto_fix = (
                        "yt-dlp updated (binary-level only — does not "
                        "address network/proxy/credential failures)"
                    )
                else:
                    alert.auto_fix = f"yt-dlp update failed (rc={result.returncode})"
            except Exception as e:
                alert.auto_fix = f"yt-dlp update failed: {e}"
        alerts.append(alert)
    return alerts


def check_zero_blueprints(reports: list[dict], niche_id: str) -> list[Alert]:
    """Alert on runs producing 0 blueprints.

    Previously required 3 consecutive zero-blueprint runs before alerting,
    which meant up to 72 hours of silent content loss. Now alerts on the
    first occurrence — severity scales with consecutive count:
        1 run  → warning (could be transient)
        2 runs → critical
        3+ runs → critical + diagnosis
    """
    alerts = []
    consecutive_zero = 0
    for r in reports:
        if r["metrics"]["blueprints_count"] == 0 and r["metrics"]["stories_count"] > 0:
            consecutive_zero += 1
        else:
            break

    if consecutive_zero == 0:
        return alerts

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

    severity = "warning" if consecutive_zero == 1 else "critical"
    alerts.append(
        Alert(
            check="zero_blueprints",
            severity=severity,
            message=(
                f"{consecutive_zero} consecutive run(s) with 0 blueprints. "
                f"Likely: {', '.join(diagnosis) or 'unknown'}"
            ),
            niche_id=niche_id,
            details={"consecutive_zero": consecutive_zero, "diagnosis": diagnosis},
        )
    )
    return alerts


def check_qc_collapse(reports: list[dict], niche_id: str) -> list[Alert]:
    """Check for QC pass rate at 0% across the most recent consecutive runs.

    Uses the same "break on first non-failing run" pattern as
    check_download_failures.  A previous version summed 0% runs from a
    5-run sliding window which made the alert sticky — historical 0% runs
    kept it firing for days after recovery (the 2026-05-17 sports
    incident: latest run 25% pass, but two pre-WARP 0% runs in the window
    kept qc_collapse alerting until manually cleared).

    With this shape, a single non-zero QC run automatically clears the
    alert.  Reflects the fact that the most recent run state is what
    matters for triage — fixing the upstream issue should be visible
    immediately.
    """
    alerts = []
    consecutive_zero = 0
    for r in reports:
        if r.get("metrics", {}).get("qc", {}).get("pass_rate") == "0.0%":
            consecutive_zero += 1
        else:
            break

    if consecutive_zero >= 2:
        alerts.append(
            Alert(
                check="qc_collapse",
                severity="critical",
                message=f"QC at 0% for {consecutive_zero} consecutive runs",
                niche_id=niche_id,
                details={"consecutive_zero_qc": consecutive_zero},
            )
        )
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
                    alerts.append(
                        Alert(
                            check="source_starvation",
                            severity="warning",
                            message=f"Only {len(vids)} videos fetched (< 3 minimum)",
                            niche_id=niche_id,
                        )
                    )
                # Check single-source dependency
                channels = set(v.get("channel_name", "") for v in vids)
                if len(channels) == 1 and len(vids) > 3:
                    alerts.append(
                        Alert(
                            check="single_source",
                            severity="warning",
                            message=f"All {len(vids)} videos from single channel: {channels.pop()}",
                            niche_id=niche_id,
                        )
                    )
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
            days_stale = (datetime.now(UTC) - last_update).days
            if days_stale > 7:
                alerts.append(
                    Alert(
                        check="bandit_stale",
                        severity="warning",
                        message=f"Bandit arms not updated in {days_stale} days",
                        niche_id=niche_id,
                        details={"days_stale": days_stale, "last_update": last_update.isoformat()},
                    )
                )
    except Exception as e:
        logger.debug("Bandit staleness check failed: %s", e)
    return alerts


def check_bandit_posterior_drift(niche_id: str) -> list[Alert]:
    """Flag bandit arms whose posteriors are stuck near the prior despite
    reward signal being available.

    Background: ``check_bandit_staleness`` reads ``updated_at`` — a row
    can be touched (e.g. by config_writer) without its posterior moving.
    The 2026-03-17 → 2026-05-19 outage looked healthy by that metric
    because the rows existed, but ``alpha + beta == 2.0`` betrayed that
    no rewards had been applied. This check catches that pattern by
    cross-referencing recent ``pending_feedback`` activity with arm
    posterior shape: if rewards are flowing into PF but arms remain at
    the uniform prior, the update pipeline is broken somewhere between
    ``update_window`` and ``_default_bandit_updater``.
    """
    alerts = []
    try:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()

        # Count pending_feedback rows for this niche where reward_48h
        # was computed in the last 14 days. If zero, the publisher
        # hasn't fed the loop in two weeks — that's a separate issue
        # (publish_failures / content_gap) and this check is moot.
        cur.execute(
            """
            SELECT COUNT(*) FROM pending_feedback
            WHERE niche_id = %s
              AND reward_48h IS NOT NULL
              AND updated_at > NOW() - INTERVAL '14 days'
            """,
            (niche_id,),
        )
        recent_rewards = cur.fetchone()[0] or 0

        if recent_rewards >= 5:
            # CONTENT ARMS: only flag arms that have *their own* PF
            # rows but haven't moved. A cold arm with zero PF rows
            # (e.g. esports_highlight when no esports content shipped)
            # is not a bug — it's just unused. The previous version
            # of this check counted niche-level rewards against every
            # arm and produced false-positive ERRORs on unused arms.
            cur.execute(
                """
                SELECT b.arm_id, b.alpha, b.beta, b.n_plays,
                       (SELECT COUNT(*) FROM pending_feedback p
                        WHERE p.niche_id = b.niche_id
                          AND p.arm_id = b.arm_id
                          AND p.reward_48h IS NOT NULL
                          AND p.updated_at > NOW() - INTERVAL '14 days'
                       ) AS pf_for_arm
                FROM bandit_arms b
                WHERE b.niche_id = %s
                  AND (b.alpha + b.beta) < 3.0
                  AND b.arm_id NOT LIKE 'style:%%'
                ORDER BY b.arm_id
                """,
                (niche_id,),
            )
            content_unmoved = [
                (arm, alpha, beta, n_plays, pf_count)
                for arm, alpha, beta, n_plays, pf_count in cur.fetchall()
                if pf_count > 0
            ]

            if content_unmoved:
                alerts.append(
                    Alert(
                        check="bandit_posterior_drift",
                        severity="error",
                        message=(
                            f"{len(content_unmoved)} content arm(s) at uniform "
                            f"prior despite arm-specific rewards in last 14 days"
                        ),
                        niche_id=niche_id,
                        details={
                            "unmoved_arms": [
                                {"arm_id": a, "n_plays": n, "pf_rows_for_arm": p}
                                for a, _, _, n, p in content_unmoved[:10]
                            ],
                        },
                    )
                )

            # STYLE ARMS: bandit_context.extra_arms is the only path
            # that credits them. Loop over rows separately because
            # arm_id is in jsonb not a column.
            cur.execute(
                """
                SELECT arm_id, alpha, beta, n_plays
                FROM bandit_arms
                WHERE niche_id = %s
                  AND (alpha + beta) < 3.0
                  AND arm_id LIKE 'style:%%'
                ORDER BY arm_id
                """,
                (niche_id,),
            )
            style_unmoved = [r[0] for r in cur.fetchall()]
            # Style arms are warning-level: extra_arms only fires when
            # the publisher writes it. Persistent zero plays across
            # multiple style arms after 50+ rewards is the signature
            # of the 2026-05-20 hook_style-not-propagated bug — but a
            # small number can legitimately be cold.
            if style_unmoved and recent_rewards >= 50:
                alerts.append(
                    Alert(
                        check="bandit_posterior_drift",
                        severity="warning",
                        message=(
                            f"{len(style_unmoved)} style:* arms unmoved after "
                            f"{recent_rewards} rewards — check that publisher "
                            "writes bandit_context.extra_arms"
                        ),
                        niche_id=niche_id,
                        details={
                            "unmoved_arms": style_unmoved[:10],
                            "recent_rewards": recent_rewards,
                        },
                    )
                )
        conn.close()
    except Exception as e:
        logger.debug("Bandit posterior drift check failed: %s", e)
    return alerts


# ── Publishing checks ─────────────────────────────────────────────────


def check_missing_media(niche_id: str) -> list[Alert]:
    """Check VISUAL_READY blueprints for missing video files.

    SAFETY: Bails out entirely if more than 50% of files appear missing
    OR the media root mount/symlink seems broken. A mass missing-file
    event is almost always a mount/symlink issue, not real data loss —
    auto-archiving in that scenario destroys recoverable blueprints.
    """
    alerts = []
    try:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, extra->>'visual_paths', scheduled_for FROM blueprints "
            "WHERE niche_id = %s AND status = 'VISUAL_READY'",
            (niche_id,),
        )
        rows = cur.fetchall()
        broken = []
        scheduled_broken = []  # R-79: scheduled posts are sacred — never auto-archive
        total_with_paths = 0
        for bp_id, _title, vp, scheduled_for in rows:
            is_broken = False
            if not vp:
                is_broken = True
            else:
                total_with_paths += 1
                try:
                    paths = json.loads(vp) if vp.startswith("[") else [vp]
                except (json.JSONDecodeError, ValueError):
                    paths = [vp]
                if not any(p and pathlib.Path(p).exists() for p in paths):
                    is_broken = True
            if is_broken:
                broken.append(bp_id)
                if scheduled_for:
                    scheduled_broken.append(bp_id)

        # SAFETY GATE 1: bail out on mass-failure patterns that look like a mount
        # issue rather than genuine per-row media loss. Two patterns trigger:
        #   (a) >=25% of a non-trivial batch (>=4 rows) is broken
        #   (b) 100% of any batch (>=1 row) is broken — covers the small-batch
        #       case the >=4 guard used to drop. The Mac/Hetzner split-brain
        #       incident on 2026-04-29 surfaced this: 3 gaming blueprints with
        #       Mac-host paths slipped past the gate and were auto-archived.
        rate_gate = total_with_paths >= 4 and len(broken) * 4 >= total_with_paths
        all_broken_gate = total_with_paths >= 1 and len(broken) == total_with_paths
        if rate_gate or all_broken_gate:
            pct = (len(broken) * 100 // total_with_paths) if total_with_paths else 0
            alerts.append(
                Alert(
                    check="missing_media_mass",
                    severity="critical",
                    message=(
                        f"{len(broken)}/{total_with_paths} blueprints appear to have "
                        f"missing media ({pct}%) — likely a symlink/mount/host issue, "
                        f"NOT auto-archiving"
                    ),
                    niche_id=niche_id,
                    details={
                        "broken_count": len(broken),
                        "total": total_with_paths,
                        "trigger": "all_broken" if all_broken_gate and not rate_gate else "rate",
                    },
                )
            )
            conn.close()
            return alerts

        # SAFETY GATE 2: Verify the media root mount is actually accessible
        media_root = pathlib.Path(os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")) / ".tmp"
        if not media_root.exists():
            alerts.append(
                Alert(
                    check="media_root_missing",
                    severity="critical",
                    message=f"Media root {media_root} does not exist — symlink broken",
                    niche_id=niche_id,
                )
            )
            conn.close()
            return alerts

        if broken:
            # R-79: NEVER auto-archive a scheduled post (cleanup_safety.md).
            # Archive only unscheduled broken blueprints; surface scheduled-broken
            # ones as an alert so an operator can fix them without losing the slot.
            _scheduled = set(scheduled_broken)
            unscheduled_broken = [b for b in broken if b not in _scheduled]
            if unscheduled_broken:
                cur.execute(
                    "UPDATE blueprints SET status = 'ARCHIVED', "
                    "action_taken = 'auto_archived_missing_media' "
                    "WHERE id = ANY(%s)",
                    (unscheduled_broken,),
                )
                conn.commit()
                alerts.append(
                    Alert(
                        check="missing_media",
                        severity="critical",
                        message=f"{len(unscheduled_broken)} VISUAL_READY blueprints with missing media files",
                        niche_id=niche_id,
                        auto_fix=f"Archived {len(unscheduled_broken)} blueprints",
                    )
                )
            if scheduled_broken:
                alerts.append(
                    Alert(
                        check="missing_media_scheduled",
                        severity="critical",
                        message=(
                            f"{len(scheduled_broken)} SCHEDULED blueprints have missing media — "
                            "NOT auto-archiving (scheduled posts are sacred); manual fix needed"
                        ),
                        niche_id=niche_id,
                        details={"blueprint_ids": scheduled_broken},
                    )
                )
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
            alerts.append(
                Alert(
                    check="content_gap",
                    severity="warning",
                    message="No approved+scheduled content for next 48 hours",
                    niche_id=niche_id,
                )
            )
    except Exception as e:
        logger.debug("Content gap check failed: %s", e)
    return alerts


def check_stuck_publishing(niche_id: str) -> list[Alert]:
    """Recover blueprints stuck in PUBLISHING state for >30 minutes.

    The publisher has its own in-process recovery loop at the top of
    publish_all_platforms(), but that only runs when the publisher itself
    runs. If a niche's publisher is broken or hasn't fired for a day,
    stuck PUBLISHING rows are never rescued. This check closes that gap
    by running on the hourly health-monitor timer regardless of publisher
    state.

    Safety: mirrors the publisher's recovery semantics exactly so the
    two paths stay consistent.
    """
    alerts = []
    try:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        # publish_attempts lives in the extra JSONB field on Postgres, not as
        # a top-level column. The publisher's own recovery reads it via
        # fields.get("publish_attempts", 0) and defaults to 0, so we mirror
        # that semantic: missing = zero attempts.
        cur.execute(
            "SELECT id, title, updated_at, "
            "COALESCE((extra->>'publish_attempts')::int, 0), "
            "platform_publish_status FROM blueprints "
            "WHERE niche_id = %s AND status = 'PUBLISHING' "
            "AND updated_at < NOW() - INTERVAL '30 minutes'",
            (niche_id,),
        )
        rows = cur.fetchall()
        if not rows:
            conn.close()
            return alerts

        for bp_id, title, updated_at, attempts, pps_raw in rows:
            attempts = int(attempts or 0)
            # Parse per-platform status to detect partial success
            pps = {}
            if pps_raw:
                try:
                    pps = json.loads(pps_raw) if isinstance(pps_raw, str) else (pps_raw or {})
                except (json.JSONDecodeError, TypeError):
                    pps = {}
            has_published = any(
                v == "PUBLISHED" or (isinstance(v, dict) and v.get("status") == "PUBLISHED")
                for v in pps.values()
            )
            if has_published:
                new_status = "PUBLISHED"
                reason = "partial success detected — marking PUBLISHED"
            elif attempts >= 3:
                new_status = "PUBLISH_FAILED"
                reason = f"{attempts} attempts exhausted — marking PUBLISH_FAILED"
            else:
                new_status = "VISUAL_READY"
                reason = f"stuck >30min ({attempts} prior attempts) — resetting to VISUAL_READY"

            cur.execute(
                "UPDATE blueprints SET status = %s WHERE id = %s",
                (new_status, bp_id),
            )
            alerts.append(
                Alert(
                    check="stuck_publishing",
                    severity="warning",
                    message=f"Recovered stuck PUBLISHING '{(title or '')[:60]}': {reason}",
                    niche_id=niche_id,
                    details={
                        "blueprint_id": str(bp_id),
                        "new_status": new_status,
                        "updated_at": str(updated_at),
                        "attempts": attempts,
                    },
                    auto_fix=f"status={new_status}",
                )
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("[health_monitor] check_stuck_publishing failed for %s: %s", niche_id, e)
    return alerts


def archive_orphan_drafts(niche_id: str) -> list[Alert]:
    """Auto-archive DRAFTED blueprints that can't reach VISUAL_READY.

    Three flavours of orphan accumulate at DRAFTED indefinitely:

      * **No-video drafts** (Branch 1) — typically Steam-spike or
        RSS-source rows that never found a downloadable video. With
        ``video_gate: require`` they can never progress. 7-day age
        threshold (the original cleanup).

      * **Render-never-completed drafts** (Branch 2a, added 2026-06-14)
        — rows with a ``video_id`` (a YouTube/Twitch/etc. video was
        identified) but NO ``visual_paths`` entry in ``extra`` (the
        render binary never produced an output file: yt-dlp download
        failure, ffmpeg crash, OOM, etc). 7-day age threshold — same
        as no-video because YouTube trending churn means the same
        video won't be re-fetched on later runs anyway.

      * **Failed-video drafts** (Branch 2, R-81) — rows where a render
        landed AND a ``visual_paths`` entry exists, but
        ``video_validation.valid`` came back False (VMAF below
        threshold, wrong dims, audio spec failure, etc.). R-47 made
        those rows stay DRAFTED instead of incorrectly going
        VISUAL_READY, so they began accumulating slowly. 14-day age
        threshold — twice the others, reflecting that more pipeline
        effort was invested and human triage might want to override.

    Safety (all branches): ``cleanup_safety.md`` forbids touching
    anything with ``scheduled_for`` set, regardless of value — every
    UPDATE explicitly filters that out. All also use age >= threshold
    so a pipeline mid-flight on a fresh story can't be raced.

    Returns a warning Alert per branch that actually archived rows so
    the operator sees the cleanup in the daily health report.
    """
    alerts = []
    try:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()

        # Branch 1 (original): no-video drafts, 7-day age.
        cur.execute(
            """
            UPDATE blueprints
            SET status = 'ARCHIVED',
                action_taken = 'auto_archived_orphan',
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE niche_id = %s
              AND status = 'DRAFTED'
              AND (video_id IS NULL OR video_id = '')
              AND scheduled_for IS NULL
              AND created_at < NOW() - INTERVAL '7 days'
            RETURNING id
            """,
            (niche_id,),
        )
        no_video_archived = cur.fetchall()

        # Branch 2a (2026-06-14): render-never-completed drafts, 7-day age.
        # A DRAFTED row with a ``video_id`` but NO entry in
        # ``extra->'visual_paths'`` means the render binary never produced
        # an output file (yt-dlp download failed, ffmpeg crash, transcode
        # OOM, etc). Distinct from Branch 2 (where rendered_path landed
        # but validation rejected it). YouTube trending churn means the
        # same video won't be re-fetched on later runs, so 7d is the
        # right cutoff — patience past that point yields nothing.
        #
        # Discovered when a 2026-06-14 audit found 94 stuck youtube_trending
        # DRAFTED accumulated during the WARP outage + deploy-gap window
        # (2026-06-01 to 2026-06-09). Branch 2's 14d cutoff was too
        # patient: most rows were 7-13d old at audit time.
        # See [[session-2026-06-14-deploy-pipeline-gap]] for the wider
        # incident context.
        cur.execute(
            """
            UPDATE blueprints
            SET status = 'ARCHIVED',
                action_taken = 'auto_archived_render_never_completed',
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE niche_id = %s
              AND status = 'DRAFTED'
              AND video_id IS NOT NULL AND video_id != ''
              AND scheduled_for IS NULL
              AND created_at < NOW() - INTERVAL '7 days'
              AND (
                  extra->>'visual_paths' IS NULL
                  OR extra->>'visual_paths' = ''
                  OR extra->>'visual_paths' = '[]'
              )
            RETURNING id
            """,
            (niche_id,),
        )
        render_never_completed_archived = cur.fetchall()

        # Branch 2 (R-81): failed-video drafts, 14-day age. A DRAFTED row
        # WITH a video_id past 14d that ALSO has a ``visual_paths`` entry
        # almost certainly hit validation failure — the publishable gate
        # (push_to_backlog ``_is_publishable``) writes DRAFTED only when
        # ``video_validation.valid is False`` alongside a present
        # ``rendered_path``. 14d is the deliberate safety buffer giving
        # human triage a wider window to override.
        #
        # Branch 2a above catches the render-never-completed subset at
        # 7d; this branch is the backstop for the residual rows where
        # render DID produce output but validation rejected it.
        cur.execute(
            """
            UPDATE blueprints
            SET status = 'ARCHIVED',
                action_taken = 'auto_archived_failed_video',
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE niche_id = %s
              AND status = 'DRAFTED'
              AND video_id IS NOT NULL AND video_id != ''
              AND scheduled_for IS NULL
              AND created_at < NOW() - INTERVAL '14 days'
            RETURNING id
            """,
            (niche_id,),
        )
        failed_video_archived = cur.fetchall()

        conn.commit()
        conn.close()

        if no_video_archived:
            alerts.append(
                Alert(
                    check="orphan_drafts_archived",
                    severity="warning",
                    message=(
                        f"auto-archived {len(no_video_archived)} stale DRAFTED "
                        "orphans (>7d, no video, no schedule)"
                    ),
                    niche_id=niche_id,
                    details={"count": len(no_video_archived)},
                    auto_fix="archived",
                )
            )
        if render_never_completed_archived:
            alerts.append(
                Alert(
                    check="render_never_completed_drafts_archived",
                    severity="warning",
                    message=(
                        f"auto-archived {len(render_never_completed_archived)} "
                        "stale DRAFTED rows (>7d, video identified but render "
                        "never produced an output file)"
                    ),
                    niche_id=niche_id,
                    details={"count": len(render_never_completed_archived)},
                    auto_fix="archived",
                )
            )
        if failed_video_archived:
            alerts.append(
                Alert(
                    check="failed_video_drafts_archived",
                    severity="warning",
                    message=(
                        f"auto-archived {len(failed_video_archived)} stale DRAFTED "
                        "rows with video (>14d, validation-failed, no schedule)"
                    ),
                    niche_id=niche_id,
                    details={"count": len(failed_video_archived)},
                    auto_fix="archived",
                )
            )
    except Exception as e:
        logger.debug("Orphan-draft archive failed: %s", e)
    return alerts


def archive_orphan_intake_stories(niche_id: str) -> list[Alert]:
    """R-81: archive INTAKE-status stories that are safely done with.

    The audit (R-81 LOW, Partly-corrected) flagged that ``stories``
    rows sit at ``status="INTAKE"`` for their entire lifecycle —
    ``update_story_status`` exists in the store API but no live code
    calls it (grep-verified). So the status field alone can't
    distinguish "story was never used" from "story finished its
    rotation"; we have to look at the **blueprint-reference graph**.

    A story is safe to archive iff EITHER:

      * no blueprint references it (true orphan — story was created
        but the pipeline rejected it before reaching ``PushToBacklog``),
        OR
      * every blueprint pointing at it is in a terminal state
        (``PUBLISHED`` or ``ARCHIVED``) — the rotation is done.

    Non-terminal blueprint states (``DRAFTED``, ``VISUAL_READY``,
    ``PUBLISHING``, ``PUBLISH_FAILED``) protect the story:
    DRAFTED/VISUAL_READY/PUBLISHING are in flight, PUBLISH_FAILED
    can be revived. Notably the ``cleanup_safety.md`` "scheduled
    posts are sacred" rule applies **transitively**: a scheduled
    blueprint is ``VISUAL_READY`` (non-terminal), so its parent
    story is automatically protected by this check — no separate
    ``scheduled_for`` predicate needed on stories (which don't
    have that column anyway).

    Age threshold: 30 days — longer than ``archive_orphan_drafts``
    because stories live longer in the pipeline conceptually
    (multiple blueprints can be drawn from one story over time).
    """
    alerts = []
    try:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE stories
            SET status = 'ARCHIVED',
                updated_at = NOW()
            WHERE niche_id = %s
              AND status = 'INTAKE'
              AND created_at < NOW() - INTERVAL '30 days'
              AND NOT EXISTS (
                SELECT 1 FROM blueprints
                WHERE blueprints.story_id = stories.story_id
                  AND blueprints.niche_id = stories.niche_id
                  AND blueprints.status NOT IN ('PUBLISHED', 'ARCHIVED')
              )
            RETURNING id
            """,
            (niche_id,),
        )
        archived = cur.fetchall()
        conn.commit()
        conn.close()

        if archived:
            alerts.append(
                Alert(
                    check="orphan_intake_stories_archived",
                    severity="warning",
                    message=(
                        f"auto-archived {len(archived)} INTAKE stories "
                        "(>30d, no non-terminal blueprint refs)"
                    ),
                    niche_id=niche_id,
                    details={"count": len(archived)},
                    auto_fix="archived",
                )
            )
    except Exception as e:
        logger.debug("Orphan-INTAKE-story archive failed: %s", e)
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
            alerts.append(
                Alert(
                    check="publish_failures",
                    severity="critical",
                    message=f"{failed} FAILED publishes in last 24h",
                    niche_id=niche_id,
                    details={"status_breakdown": by_status},
                )
            )
    except Exception as e:
        logger.debug("Publish failures check failed: %s", e)
    return alerts


# ── System checks ─────────────────────────────────────────────────────


def check_disk() -> list[Alert]:
    """Check disk usage on root and media volumes.

    Thresholds are read from ``alerting.yaml`` (audit M-1). The warning
    threshold maps to ``thresholds.disk_usage_pct``; critical fires +10
    points above it. Operators can tune without a deploy.
    """
    from genlab_core.monitoring.alerting_config import get_alerting_config

    cfg = get_alerting_config().thresholds
    warn_pct = cfg.disk_usage_pct  # was hardcoded 85
    crit_pct = min(100, warn_pct + 10)  # was hardcoded 90

    alerts = []
    try:
        result = subprocess.run(
            ["df", "--output=pcent,target", "/", "/mnt/genlab-media"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                pct = int(parts[0].replace("%", ""))
                mount = parts[1]
                if pct > warn_pct:
                    alerts.append(
                        Alert(
                            check="disk_pressure",
                            severity="critical" if pct > crit_pct else "warning",
                            message=f"{mount} at {pct}% usage",
                        )
                    )
    except Exception as e:
        logger.debug("Disk check failed: %s", e)
    return alerts


def check_services() -> list[Alert]:
    """Check for failed systemd services and attempt restart."""
    alerts = []
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "genlab-*", "--state=failed", "--no-pager", "--plain"],
            capture_output=True,
            text=True,
            timeout=10,
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
                capture_output=True,
                text=True,
                timeout=15,
            )
            fix_status = "restarted" if fix_result.returncode == 0 else "restart failed"
            alerts.append(
                Alert(
                    check="service_down",
                    severity="critical",
                    message=f"{unit} is in failed state",
                    auto_fix=fix_status,
                )
            )
    except Exception as e:
        logger.debug("Service check failed: %s", e)
    return alerts


def _attempt_warp_restart() -> str:
    """Try ``sudo -n systemctl restart warp-svc`` and report the truthful
    outcome as a short string suitable for the Alert.auto_fix field.

    Returns one of:

      * ``"restarted warp-svc, daemon now active"`` — restart succeeded
        AND the post-restart ``systemctl is-active`` check confirms the
        unit is running. This is the only "happy path" string.

      * ``"restart applied but warp-svc still inactive after 3s"`` —
        the restart command succeeded (rc=0) but the unit didn't come
        back up. Suggests a startup misconfiguration; operator should
        check ``journalctl -u warp-svc``.

      * ``"sudoers not configured — add: genlab ALL=(root) NOPASSWD: /bin/systemctl restart warp-svc"`` —
        ``sudo -n`` returned the canonical "a password is required"
        marker. This is the single most likely failure on day one and
        gives the operator the exact line to add to ``/etc/sudoers.d/``.

      * ``"restart failed (rc=N): <stderr-snippet>"`` — fallback for
        any other non-zero exit (e.g. unit-not-found, daemon-reload
        needed). Includes a trimmed stderr so the operator has a
        starting point without digging into the journal.

      * ``"restart raised: <exception>"`` — subprocess itself failed
        (PATH issue, permission denied at the OS level, etc.). Bubbles
        the exception class + message instead of swallowing.

    Always returns a non-empty string — the Alert.auto_fix field must
    be informative even when nothing improved.
    """
    import time

    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "warp-svc"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return f"restart raised: {type(exc).__name__}: {exc}"

    if result.returncode != 0:
        stderr_lc = (result.stderr or "").lower()
        # sudo -n emits one of these markers when NOPASSWD isn't set;
        # both shapes are documented in sudo(8) and appear depending
        # on distro / sudo version.
        if "password is required" in stderr_lc or "a terminal is required" in stderr_lc:
            return (
                "sudoers not configured — add: "
                "genlab ALL=(root) NOPASSWD: /bin/systemctl restart warp-svc"
            )
        # Trim stderr to one line + cap at 120 chars so the alert
        # row stays scannable.
        stderr_snippet = (result.stderr or "").strip().splitlines()[:1]
        snippet = stderr_snippet[0][:120] if stderr_snippet else "no stderr"
        return f"restart failed (rc={result.returncode}): {snippet}"

    # Restart command succeeded — give warp-svc a moment to come up,
    # then verify. 3s is empirically enough for the daemon's normal
    # init sequence on prod (Cloudflare's startup is sub-second
    # typically; the buffer protects against systemd's own latency).
    time.sleep(3)
    try:
        verify = subprocess.run(
            ["systemctl", "is-active", "warp-svc"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        # Restart said it worked; verification raised. Report that
        # honestly rather than claim victory.
        return f"restart applied, verification raised: {type(exc).__name__}: {exc}"

    if verify.returncode == 0 and verify.stdout.strip() == "active":
        return "restarted warp-svc, daemon now active"
    return "restart applied but warp-svc still inactive after 3s"


def check_warp_health() -> list[Alert]:
    """Detect WARP SOCKS proxy outages within minutes, not days.

    yt-dlp routes all video downloads through Cloudflare WARP at
    127.0.0.1:40000 to bypass YouTube's bot-detection on Hetzner
    datacenter IPs. When WARP goes down (daemon stops, mode flips
    away from proxy, port closes), every pipeline that runs in the
    next 24h fails downloads silently, then 6h later the existing
    ``check_download_failures`` fires with a misleading "yt-dlp
    update: success" auto-fix message that doesn't address the
    real network-layer issue.

    History: 2026-05-11 17:33 IST WARP stopped, was disabled at the
    systemd level, never restarted. 5 days of pipeline runs failed
    downloads (4/5 niches at zero blueprints/day) before the audit
    caught it on 2026-05-17.

    This check fires CRITICAL immediately on either:
      * ``warp-svc`` systemd unit not active
      * 127.0.0.1:40000 not in LISTEN state

    Skipped silently when ``warp-svc`` isn't installed at all (dev
    environments don't need WARP — only the Hetzner production
    host routes through it).
    """
    alerts: list[Alert] = []
    try:
        # Is warp-svc installed?  list-unit-files exits 0 even when
        # the unit is missing; check via show + LoadState instead.
        show = subprocess.run(
            [
                "systemctl",
                "show",
                "warp-svc.service",
                "--property=LoadState,ActiveState,SubState",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        kv = dict(
            (line.split("=", 1)[0], line.split("=", 1)[1])
            for line in show.stdout.strip().split("\n")
            if "=" in line
        )
        if kv.get("LoadState") in ("not-found", "masked", ""):
            # WARP not installed — skip silently (dev environments).
            return alerts

        active = kv.get("ActiveState") == "active"
        if not active:
            # Attempt auto-restart via ``sudo -n systemctl restart``.
            # ``-n`` (non-interactive) ensures we fail fast if NOPASSWD
            # isn't configured — we don't want to hang waiting for a
            # password prompt that has nowhere to go.
            #
            # health_monitor.service runs as ``User=genlab``, so the
            # restart needs a sudoers entry. The auto_fix message
            # documents the exact entry to add so the first-time
            # operator action takes seconds:
            #
            #   genlab ALL=(root) NOPASSWD: /bin/systemctl restart warp-svc
            #
            # Until that lands, the message is still strictly better
            # than the prior "not attempted" string — we tried, this
            # is what blocked us, here's the exact fix.
            fix_msg = _attempt_warp_restart()

            alerts.append(
                Alert(
                    check="warp_down",
                    severity="critical",
                    message=(
                        f"warp-svc not active (ActiveState={kv.get('ActiveState')}, "
                        f"SubState={kv.get('SubState')}). All yt-dlp downloads "
                        "will fail with 'curl: (7) connection refused' until "
                        "the daemon is restored. Run: systemctl restart warp-svc"
                    ),
                    details={
                        "load_state": kv.get("LoadState"),
                        "active_state": kv.get("ActiveState"),
                        "sub_state": kv.get("SubState"),
                    },
                    auto_fix=fix_msg,
                )
            )
            # Don't bother checking port if daemon is down.
            return alerts

        # Daemon is up — verify the SOCKS port is actually listening.
        # WARP defaults to whole-OS tunnel mode; needs explicit
        # `warp-cli mode proxy` + `warp-cli proxy port 40000` to
        # surface the SOCKS endpoint.
        ss = subprocess.run(
            ["ss", "-tln"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        port_listening = any(
            "127.0.0.1:40000" in line and "LISTEN" in line for line in ss.stdout.split("\n")
        )
        if not port_listening:
            alerts.append(
                Alert(
                    check="warp_port_closed",
                    severity="critical",
                    message=(
                        "warp-svc is active but SOCKS port 40000 is not "
                        "listening. WARP likely flipped to whole-OS tunnel "
                        "mode. Run: warp-cli mode proxy && warp-cli proxy "
                        "port 40000 && warp-cli connect"
                    ),
                    details={"active_state": "active", "port_40000_listening": False},
                )
            )
    except Exception as e:
        logger.debug("WARP health check failed: %s", e)
    return alerts


def check_git_drift() -> list[Alert]:
    """Detect uncommitted working-tree changes on the production host.

    Without this check, ad-hoc edits accumulate in the working tree
    indefinitely.  History from 2026-05-17 audit: 25+ Python source
    files had real forward-fixes (LinUCB numerical guards,
    frame_compositor drawtext escaping, etc.) sitting uncommitted for
    months — invisible to ``systemctl`` and ``journalctl``.

    Categories matter more than raw counts:
      * YAML config drift is expected — production has prefix env values
        and per-host overrides that won't be in git (the ``assume-unchanged``
        pattern). Limit yaml-drift alerts to "very many" to avoid noise.
      * Python source drift is the real signal. Any uncommitted .py
        accumulation in genlab-core/, scripts/, or dashboard/ means an
        edit happened directly on prod and never made it back to the repo.

    Thresholds (tunable per-deployment):
      * ≥ 5 modified python source files → warning
      * ≥ 15 modified python source files → critical
      * ≥ 30 yaml configs modified → warning (well above normal override count)
    """
    alerts: list[Alert] = []
    project_root = os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")

    try:
        result = subprocess.run(
            ["git", "-C", project_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            # Not a git repo, or git not available — silently skip.
            return alerts

        lines = [ln for ln in result.stdout.split("\n") if ln.strip()]
        if not lines:
            return alerts

        # Each line: 'XY filename' where XY is the two-char status.
        py_src_modified: list[str] = []
        yaml_modified: list[str] = []
        for ln in lines:
            if len(ln) < 4:
                continue
            status, _, path = ln[:2], ln[2], ln[3:]
            # Skip untracked-only entries (??) — those are noisier and
            # often legitimate (.tmp files, local notes, etc.).
            if status.strip() == "??":
                continue
            if path.endswith(".py") and (
                path.startswith("genlab-core/")
                or path.startswith("scripts/")
                or path.startswith("dashboard/")
                or path.startswith("BlackboxBrief/")
                or path.startswith("CriticalRush/")
            ):
                py_src_modified.append(path)
            elif path.endswith(".yaml") or path.endswith(".yml"):
                yaml_modified.append(path)

        py_count = len(py_src_modified)
        yaml_count = len(yaml_modified)

        if py_count >= 15:
            alerts.append(
                Alert(
                    check="git_drift",
                    severity="critical",
                    message=(
                        f"{py_count} uncommitted .py files on prod — edits "
                        f"are being made directly on the production host and "
                        f"never reaching the repo. Top 3: "
                        f"{', '.join(py_src_modified[:3])}"
                    ),
                    details={
                        "py_count": py_count,
                        "yaml_count": yaml_count,
                        "py_files": py_src_modified[:10],
                    },
                )
            )
        elif py_count >= 5:
            alerts.append(
                Alert(
                    check="git_drift",
                    severity="warning",
                    message=(
                        f"{py_count} uncommitted .py files on prod. Top 3: "
                        f"{', '.join(py_src_modified[:3])}"
                    ),
                    details={
                        "py_count": py_count,
                        "yaml_count": yaml_count,
                        "py_files": py_src_modified[:10],
                    },
                )
            )

        if yaml_count >= 30:
            alerts.append(
                Alert(
                    check="git_drift_yaml",
                    severity="warning",
                    message=(
                        f"{yaml_count} uncommitted yaml configs on prod — well "
                        "above expected per-host override count."
                    ),
                    details={"yaml_count": yaml_count, "yaml_files": yaml_modified[:10]},
                )
            )
    except Exception as e:
        logger.debug("Git drift check failed: %s", e)
    return alerts


def check_swap() -> list[Alert]:
    """Check if swap usage is high (memory pressure).

    Thresholds are read from ``alerting.yaml`` (audit M-1):
    ``thresholds.swap_critical_pct`` (default 0.9 — fraction of total
    swap for the imminent-OOM warning) and ``thresholds.swap_warning_mb``
    (default 500 — absolute MB for the soft warning).
    """
    from genlab_core.monitoring.alerting_config import get_alerting_config

    cfg = get_alerting_config().thresholds
    critical_pct = cfg.swap_critical_pct  # was hardcoded 0.9
    warning_mb = cfg.swap_warning_mb  # was hardcoded 500

    alerts = []
    try:
        result = subprocess.run(["free", "-b"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            if line.startswith("Swap:"):
                parts = line.split()
                total = int(parts[1])
                used = int(parts[2])
                # R-67/R-03: a near-full swap on the 4GB box is an imminent-OOM
                # signal and must reach notify() (which forwards only criticals),
                # not sit as an unactioned warning.
                if total > 0 and used > critical_pct * total:
                    alerts.append(
                        Alert(
                            check="swap_pressure",
                            severity="critical",
                            message=f"Swap CRITICAL: {used // (1024 * 1024)}MB / "
                            f"{total // (1024 * 1024)}MB (>{int(critical_pct * 100)}%) — imminent OOM",
                        )
                    )
                elif total > 0 and used > warning_mb * 1024 * 1024:
                    alerts.append(
                        Alert(
                            check="swap_pressure",
                            severity="warning",
                            message=f"Swap at {used // (1024 * 1024)}MB / {total // (1024 * 1024)}MB",
                        )
                    )
    except Exception as e:
        logger.debug("Swap check failed: %s", e)
    return alerts


# ── Orchestrator ──────────────────────────────────────────────────────


def check_foreign_host_writes() -> list[Alert]:
    """Detect rows arriving from any host other than `hetzner-vps`.

    The DB trigger `tag_host_id` populates `extra->>'host_id'` on every
    INSERT/UPDATE. Anything other than `hetzner-vps` here means a process
    on another machine (Mac, dev laptop, attacker) has written to the
    shared DB — the exact split-brain pattern that took out 12 blueprints
    on 2026-04-29 morning before the Mac plists were disabled.

    Returns one critical alert per foreign host_id seen in the last hour.
    """
    alerts: list[Alert] = []
    try:
        import psycopg

        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""))
        cur = conn.cursor()
        cur.execute(
            """
            SELECT extra->>'host_id' AS host, count(*)
            FROM blueprints
            WHERE created_at > NOW() - INTERVAL '1 hour'
              AND extra ? 'host_id'
              AND extra->>'host_id' NOT IN ('hetzner-vps', '')
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
        for host, count in cur.fetchall():
            alerts.append(
                Alert(
                    check="foreign_host_write",
                    severity="critical",
                    message=(
                        f"{count} blueprint(s) written from foreign host '{host}' "
                        f"in the last hour — split-brain in progress"
                    ),
                    details={"host": host, "count": count},
                )
            )
        conn.close()
    except Exception as e:
        logger.debug("Foreign host check failed: %s", e)
    return alerts


def archive_stranded_engagement_reviews(niche_id: str) -> list[Alert]:
    """2026-06-14: Auto-archive ``pending_engagement`` rows stuck in
    ``pending_review`` for >7 days.

    The engagement-loop audit found 10 stranded ``pending_review`` items
    in ``pending_engagement`` since 2026-05-21 (24 days). No SLA, no
    operator alert — they just sat there. After 7 days a review item is
    effectively stale (the original comment is old, the engagement
    moment passed). Auto-archive + emit one Alert per archive batch so
    the operator sees the cleanup activity in the daily health report.

    Safety: only touches ``pending_engagement`` rows (which have no
    ``scheduled_for`` field, so the cleanup_safety.md rule doesn't
    apply). The 7-day window is conservative — same threshold the
    Branch 1 / Branch 2a of ``archive_orphan_drafts`` use.
    """
    alerts: list[Alert] = []
    try:
        import psycopg

        with psycopg.connect(os.environ.get("DATABASE_URL", "")) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pending_engagement
                    SET status = 'auto_archived_stranded',
                        updated_at = NOW()
                    WHERE niche_id = %s
                      AND status = 'pending_review'
                      AND created_at < NOW() - INTERVAL '7 days'
                    RETURNING id
                    """,
                    (niche_id,),
                )
                archived = cur.fetchall()
                conn.commit()
        if archived:
            alerts.append(
                Alert(
                    check="stranded_engagement_reviews_archived",
                    severity="warning",
                    message=(
                        f"auto-archived {len(archived)} stranded pending_review "
                        f"engagement items (>7d, no operator action)"
                    ),
                    niche_id=niche_id,
                    details={"count": len(archived)},
                    auto_fix="archived",
                )
            )
    except Exception as e:
        logger.debug("Stranded-engagement archive failed: %s", e)
    return alerts


def detect_dead_pollers(niche_id: str) -> list[Alert]:
    """2026-06-14: surface pollers that have been emitting expired-token
    alerts for >7 days.

    The engagement-loop audit found Threads tokens expired around
    2026-05-21 and the same CRITICAL token_expired alert fired ~720
    times/day for 24 days. The poller wasn't dead per se — it kept
    trying — but the platform was effectively unreachable. After 7 days
    of continuous unresolved expired-token alerts for the same niche ×
    platform, this check raises a SEPARATE escalation alert so the
    operator sees a distinct "this has been broken for a week" signal
    on top of the repeating "token expired" one.

    Pure detection — does NOT auto-disable the poller (that would mask
    the root cause and the operator can never see when the situation
    recovers). The PR #198 auto-refresh is the real fix; this check is
    the visibility layer for the days BEFORE auto-refresh has had time
    to kick in (or for cases where the refresh itself fails).
    """
    alerts: list[Alert] = []
    try:
        import psycopg

        with psycopg.connect(os.environ.get("DATABASE_URL", "")) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT details->>'platform' AS platform,
                           MIN(created_at) AS first_seen,
                           COUNT(*) AS occurrences
                    FROM pipeline_alerts
                    WHERE check_name = 'token_expired'
                      AND niche_id = %s
                      AND resolved_at IS NULL
                      AND created_at < NOW() - INTERVAL '7 days'
                    GROUP BY details->>'platform'
                    """,
                    (niche_id,),
                )
                rows = cur.fetchall()
        for platform, first_seen, count in rows:
            days_stuck = (datetime.now(UTC) - first_seen).days
            alerts.append(
                Alert(
                    check="dead_poller",
                    severity="critical",
                    message=(
                        f"{platform} poller for {niche_id} has been emitting "
                        f"token_expired for {days_stuck} days ({count} unresolved "
                        f"alerts). Auto-refresh (PR #198) should be checking daily; "
                        f"if it's enabled and still failing, refresh manually."
                    ),
                    niche_id=niche_id,
                    details={
                        "platform": platform,
                        "days_stuck": days_stuck,
                        "unresolved_token_alerts": count,
                    },
                )
            )
    except Exception as e:
        logger.debug("Dead-poller check failed: %s", e)
    return alerts


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
        all_alerts.extend(check_bandit_posterior_drift(nid))
        all_alerts.extend(check_missing_media(nid))
        all_alerts.extend(check_stuck_publishing(nid))
        all_alerts.extend(check_content_gap(nid))
        all_alerts.extend(check_publish_failures(nid))
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


if __name__ == "__main__":
    main()
