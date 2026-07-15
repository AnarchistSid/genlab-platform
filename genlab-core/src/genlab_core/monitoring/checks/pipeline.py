"""Content-pipeline health checks.

Extracted from ``health_monitor.py`` (2026-07-08 god-module split, DEV-1).
Covers every check whose subject is a niche's *content pipeline* — from
YouTube fetch through render, blueprint state, and publisher outcomes.
Infrastructure (disk / swap / warp / git) lives in ``infrastructure.py``;
bandit + engagement in ``bandit_engagement.py``.

**Patch surface notes**: three functions here read facade-owned symbols
through the ``genlab_core.monitoring.health_monitor`` module (not their
own module) so existing test patches like
``patch("genlab_core.monitoring.health_monitor._load_clip_index")`` and
``patch("genlab_core.monitoring.health_monitor.check_warp_health")``
continue to affect behavior post-split:

  * ``check_download_failures`` — calls ``_facade._load_clip_index`` and
    ``_facade.check_warp_health``.
  * ``check_fetcher_stage_silent_failures`` — indirectly, via
    ``_load_recent_metrics`` which resolves ``RUNS_DIR`` through the
    facade so ``monkeypatch.setattr(health_monitor, "RUNS_DIR", …)``
    keeps working.
  * ``check_zero_blueprints`` — calls ``_facade._load_clip_index`` for
    the same reason.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
from datetime import UTC, datetime, timedelta

from genlab_core.monitoring._report_loaders import _load_recent_metrics
from genlab_core.monitoring.alerts import Alert
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-5

logger = logging.getLogger(__name__)


def check_download_failures(reports: list[dict], niche_id: str) -> list[Alert]:
    """Check if yt-dlp downloads are consistently failing."""
    # Late-bound facade lookup so tests that patch
    # ``genlab_core.monitoring.health_monitor._load_clip_index`` and
    # ``…check_warp_health`` continue to affect this call site after
    # the 2026-07-08 module split.
    from genlab_core.monitoring import health_monitor as _facade

    alerts = []
    consecutive_fails = 0
    for r in reports:
        ci = _facade._load_clip_index(r.get("_run_dir", ""))
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
        warp_alerts = _facade.check_warp_health()
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


_QUEUE_RUNWAY_SUPPRESSION_DAYS = 5
_QUEUE_RUNWAY_MIN_BLUEPRINTS = 3


def _count_future_queue_blueprints(niche_id: str) -> int | None:
    """Count blueprints for ``niche_id`` scheduled_for the next 5 days.

    Task #622 (2026-07-09) helper: distinguish "pipeline broken, no
    content" from "pipeline healthy, queue already full for the week."
    Only counts blueprints in states that count as future-committed
    content:

    * ``VISUAL_READY`` + ``action_taken='approved'`` — the current
      shape written by both the nightly scheduler (script #611) and
      the auto-approver
    * ``SCHEDULED`` / ``PUBLISHED`` — legacy shape, still counted

    Returns None on DB error (caller falls through to alerting — the
    safe default is to NOT suppress on infrastructure failure).
    """
    try:
        with pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)::int AS n FROM blueprints
                WHERE niche_id = %s
                  AND (
                    status IN ('SCHEDULED', 'PUBLISHED')
                    OR (status = 'VISUAL_READY' AND action_taken = 'approved')
                  )
                  AND scheduled_for BETWEEN NOW()
                    AND NOW() + INTERVAL '%s days'
                """,
                (niche_id, _QUEUE_RUNWAY_SUPPRESSION_DAYS),
            ).fetchone()
        # 2026-07-15: pg_connect returns positional tuples by default —
        # `row["n"]` raised TypeError, which the except handler caught +
        # returned None, silently disabling the queue-depth suppression
        # for weeks. Post-2026-07-15 deploy surfaced 3 warnings in one
        # health-monitor fire (ai_creators, sports, movies). Use
        # positional access.
        return int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001 — fail-open: alert if we can't check
        logger.warning(
            "[health.check_zero_blueprints] queue-depth query failed for "
            "%s: %s — proceeding to alert without suppression check",
            niche_id,
            exc,
        )
        return None


def check_zero_blueprints(reports: list[dict], niche_id: str) -> list[Alert]:
    """Alert on runs producing 0 blueprints.

    Previously required 3 consecutive zero-blueprint runs before alerting,
    which meant up to 72 hours of silent content loss. Now alerts on the
    first occurrence — severity scales with consecutive count:
        1 run  → warning (could be transient)
        2 runs → critical
        3+ runs → critical + diagnosis

    2026-07-09 (task #622): SUPPRESS the alert when the niche's forward
    queue already has ≥3 blueprints scheduled_for the next 5 days.
    Rationale: "0 new + queue full" is a HEALTHY steady state — the
    scheduler correctly declined to over-book because upcoming slots
    are already covered. Sports hit this exact false positive 3× on
    2026-07-09 because it had blueprints for 07-11 through 07-14 but
    the pipeline ran with 0 new (dedup found no new eligible clips).

    Threshold picking (3 days minimum runway):
      * Lower (1-2) is too aggressive — misses real pipeline breakage
        that would ALSO drain the near-term queue.
      * Higher (5+) is too lax — 4-day drift could hide a real regression.
      * 3 = daily-cadence + weekend buffer.

    DB errors during the suppression check fail-open — we still alert
    (safer to over-alert than to hide real content loss).
    """
    from genlab_core.monitoring import health_monitor as _facade

    alerts = []
    consecutive_zero = 0
    consecutive_fetch_outage = 0
    for r in reports:
        bp = r["metrics"]["blueprints_count"]
        st = r["metrics"]["stories_count"]
        if bp == 0 and st > 0:
            consecutive_zero += 1
        elif bp == 0 and st == 0:
            # 2026-07-14 (pipeline audit F2): distinguish "fetch outage"
            # (0 stories AND 0 blueprints) from "no trending today" —
            # both look like `st == 0` but a niche should never have
            # ZERO stories from ALL fetchers for consecutive days
            # unless every fetcher is broken (API 500s, circuit open,
            # missing creds). Track separately.
            consecutive_fetch_outage += 1
        else:
            break

    if consecutive_zero == 0 and consecutive_fetch_outage == 0:
        return alerts

    # Task #622 suppression: check forward queue depth BEFORE firing.
    queue_depth = _count_future_queue_blueprints(niche_id)
    if queue_depth is not None and queue_depth >= _QUEUE_RUNWAY_MIN_BLUEPRINTS:
        logger.debug(
            "[health.check_zero_blueprints] %s: %d consecutive zero-blueprint "
            "runs BUT forward queue has %d blueprints in next %d days "
            "(threshold %d). Suppressing alert — pipeline healthy, "
            "queue already covered.",
            niche_id,
            consecutive_zero,
            queue_depth,
            _QUEUE_RUNWAY_SUPPRESSION_DAYS,
            _QUEUE_RUNWAY_MIN_BLUEPRINTS,
        )
        return alerts

    # Diagnose which stage lost the content
    latest = reports[0] if reports else {}
    m = latest.get("metrics", {})
    diagnosis = []
    ci = _facade._load_clip_index(latest.get("_run_dir", ""))
    if ci.get("videos_total", 0) > 0 and ci.get("videos_downloaded", 0) == 0:
        diagnosis.append("downloads failing (yt-dlp?)")
    qc = m.get("qc", {})
    if qc.get("pass_rate") == "0.0%":
        diagnosis.append("QC 0% (no content written?)")
    if m.get("stories_count", 0) > 0 and m.get("blueprints_count", 0) == 0:
        diagnosis.append("stories created but 0 blueprints (dedup?)")

    if consecutive_zero > 0:
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
                details={
                    "consecutive_zero": consecutive_zero,
                    "diagnosis": diagnosis,
                    "forward_queue_depth": queue_depth,
                },
            )
        )

    # 2026-07-14 (pipeline audit F2): total-fetch-outage is a distinct
    # alert. If a niche has ZERO stories from ALL fetchers for 2+
    # consecutive runs, every fetcher is likely broken (API 500s,
    # circuit open, missing creds). Same forward-queue-depth
    # suppression applies (we already have a queue).
    if consecutive_fetch_outage >= 2:
        severity_f = "critical" if consecutive_fetch_outage >= 3 else "warning"
        alerts.append(
            Alert(
                check="fetch_outage",
                severity=severity_f,
                message=(
                    f"{consecutive_fetch_outage} consecutive run(s) with 0 stories "
                    f"AND 0 blueprints. Every fetcher likely broken (API "
                    f"outage, circuit open, missing creds)."
                ),
                niche_id=niche_id,
                details={
                    "consecutive_fetch_outage": consecutive_fetch_outage,
                    "forward_queue_depth": queue_depth,
                },
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


# Fetcher stages that read context['sources_config'] (or any other
# StageContext field that, if unpopulated, makes the stage silently
# no-op). Diagnostic signature: stage_timing < 0.001s in metrics.jsonl
# across multiple consecutive runs. See COMMIT 3 / B2 in the 2026-06-30
# Phase 1 hardening plan, and the sources_config postmortem in
# pipeline_runner._load_sources_yaml's docstring.
_FETCHER_STAGES_TO_MONITOR = (
    "FetchTrendingVideos",
    "FetchRedditClips",
    "FetchScoreBatHighlights",
    "FetchScorebat",  # alias used by some niches
    "FetchTMDBTrailers",
    "FetchAnimePromos",
    "FetchTwitchClips",
    "FetchSteamTrailers",
)

# A stage is considered "silently no-opping" if its duration falls
# below this threshold. The sources_config bug produced 0.0000s; a
# real fetcher with any HTTP work or YAML parse takes at least 5-10ms.
# 1ms is a very loose floor that only fires on truly-instant returns.
_SILENT_FAILURE_DURATION_MS = 1.0

# Number of consecutive runs the stage must appear silent before
# alerting. One off-run can be a legitimate cache hit or empty-source
# upstream; three consecutive zero-time runs is the actionable signal.
_SILENT_FAILURE_CONSECUTIVE_RUNS = 3


def check_fetcher_stage_silent_failures(niche_id: str) -> list[Alert]:
    """Detect fetcher stages running in <1ms across multiple consecutive runs.

    Catches the bug class shipped + fixed in PR ``2ac8dbb2``: a fetcher
    stage that reads a StageContext key the runner never populated will
    early-return on the empty dict, reporting near-zero duration in
    ``metrics.jsonl``. The original bug went undetected for weeks
    because each individual stage skip is silent — the YouTube fetcher
    kept working so pipelines kept producing blueprints, just from a
    single source.

    A stage fires this check when it appears in ``metrics.jsonl`` with
    ``duration_ms < 1.0`` across the most-recent
    ``_SILENT_FAILURE_CONSECUTIVE_RUNS`` (=3) runs in a row. A single
    fast run is normal (cache hit, empty upstream); three in a row is
    the actionable signal.

    Stages not present in a run's metrics are NOT counted as "silent" —
    a niche that doesn't run FetchRedditClips at all should not alert
    on its absence. Only when the stage HAS run and reported
    near-zero duration do we treat it as suspicious.

    Returns a list of warning alerts (one per suspect stage per niche).
    """
    runs = _load_recent_metrics(niche_id, max_runs=_SILENT_FAILURE_CONSECUTIVE_RUNS)
    if len(runs) < _SILENT_FAILURE_CONSECUTIVE_RUNS:
        # Not enough data yet — a new niche or a freshly-rolled VPS
        # won't have history. Don't false-alarm.
        return []

    alerts: list[Alert] = []
    for stage_name in _FETCHER_STAGES_TO_MONITOR:
        # For each stage: count how many of the last N runs report
        # this stage with duration < threshold AND status == "ok"
        # (an error counts as a legitimate non-silent run).
        silent_run_count = 0
        runs_with_stage = 0
        sample_durations: list[float] = []
        for _run_name, entries in runs:
            stage_entries = [e for e in entries if e.get("stage") == stage_name]
            if not stage_entries:
                continue
            runs_with_stage += 1
            # Use the LAST occurrence per run (a stage typically only
            # runs once per pipeline, but if duplicated, the last
            # measurement is the most-recent state).
            entry = stage_entries[-1]
            duration = float(entry.get("duration_ms", 0.0))
            status = entry.get("status", "ok")
            sample_durations.append(duration)
            if status == "ok" and duration < _SILENT_FAILURE_DURATION_MS:
                silent_run_count += 1

        # Only alert if the stage was present in ALL N runs AND silent
        # in ALL N runs. Partial coverage isn't actionable.
        if (
            runs_with_stage >= _SILENT_FAILURE_CONSECUTIVE_RUNS
            and silent_run_count >= _SILENT_FAILURE_CONSECUTIVE_RUNS
        ):
            alerts.append(
                Alert(
                    check="fetcher_silent_no_op",
                    severity="warning",
                    message=(
                        f"Fetcher stage {stage_name} returned in <1ms across "
                        f"{silent_run_count} consecutive runs — likely silent "
                        "no-op (sources_config-style bug). Inspect "
                        "context inputs the stage reads; verify the runner "
                        "populates them. See "
                        "docs/architecture/stage_context_population_audit.md"
                    ),
                    niche_id=niche_id,
                    details={
                        "stage": stage_name,
                        "consecutive_runs": silent_run_count,
                        "runs_inspected": len(runs),
                        "sample_durations_ms": sample_durations,
                    },
                )
            )
    return alerts


def check_missing_media(niche_id: str) -> list[Alert]:
    """Check VISUAL_READY blueprints for missing video files.

    SAFETY: Bails out entirely if more than 50% of files appear missing
    OR the media root mount/symlink seems broken. A mass missing-file
    event is almost always a mount/symlink issue, not real data loss —
    auto-archiving in that scenario destroys recoverable blueprints.
    """
    alerts = []
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, extra->>'visual_paths', scheduled_for FROM blueprints "
            "WHERE niche_id = %s AND status = 'VISUAL_READY'",
            (niche_id,),
        )
        rows = cur.fetchall()
        broken = []
        scheduled_broken = []  # R-79: scheduled posts are sacred — never auto-archive
        past_due_broken = []  # 2026-07-06: scheduled_for > 24h in the past AND
        # media missing = stuck-broken with no publish window left. Surface as
        # a distinct warning alert (not the CRITICAL missing_media_scheduled
        # noise) so operator visibility is retained without duplicate CRITICAL
        # fires on state that hasn't changed for days. Data is still not
        # touched — R-79 still applies; this only refines the ALERT taxonomy.
        # See task #535 + session-2026-07-06-evening-3-flag-activations.
        now_utc = datetime.now(UTC)
        past_due_cutoff = now_utc - timedelta(hours=24)
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
                    # Bucket by whether the publish window is still live.
                    # Compare tz-aware; scheduled_for is timestamptz. Handle
                    # string form for MagicMock-driven tests that pass ISO
                    # strings instead of real datetimes.
                    sf = scheduled_for
                    if isinstance(sf, str):
                        try:
                            sf = datetime.fromisoformat(sf.replace("Z", "+00:00"))
                        except ValueError:
                            sf = None
                    if sf is not None and sf < past_due_cutoff:
                        past_due_broken.append(bp_id)
                    else:
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
            # Past-due scheduled posts are also protected — they get a distinct
            # WARNING alert (see below) but data is NEVER touched. R-79 applies
            # to ALL blueprints with a scheduled_for timestamp, regardless of
            # whether the window is future or past.
            _protected = set(scheduled_broken) | set(past_due_broken)
            unscheduled_broken = [b for b in broken if b not in _protected]
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
            if past_due_broken:
                # Distinct WARNING alert for scheduled-broken blueprints whose
                # publish window has already closed (>24h in the past). These
                # can't publish anyway; separating them out prevents CRITICAL
                # alert noise on stuck state that hasn't changed for days.
                # R-79 still applies: NO data touched.
                alerts.append(
                    Alert(
                        check="missing_media_past_due",
                        severity="warning",
                        message=(
                            f"{len(past_due_broken)} past-due scheduled blueprints have missing "
                            "media — publish window closed >24h ago; safe to manually archive"
                        ),
                        niche_id=niche_id,
                        details={"blueprint_ids": past_due_broken},
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
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
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


def check_stale_drafted(niche_id: str) -> list[Alert]:
    """Detect blueprints stuck at DRAFTED for >5 days.

    2026-07-14 (pipeline audit F3): a story whose YouTube URL is
    permanently unrenderable (deleted video, DRM protection, etc.)
    retries every pipeline run forever without escalation. The
    pre_download_dedup gate deliberately allows retries (per docstring
    at pre_download_dedup.py:8-16 — this was the 2026 stuck-96-sports
    fix). Without an upper bound, DRAFTED accumulates silently.

    Fires at:
      * warning at 5-14 days stale (7 recent bad candidates in one
        niche = something's off but not urgent)
      * critical at >14 days stale (operator action required to demote
        or purge)

    Class-of-bug: alerts must reflect current state (not just fire
    count) — sibling to check_content_gap + check_stuck_publishing.
    """
    alerts = []
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM blueprints "
            "WHERE niche_id = %s AND status = 'DRAFTED' "
            "AND updated_at < NOW() - INTERVAL '5 days'",
            (niche_id,),
        )
        stale_5d = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM blueprints "
            "WHERE niche_id = %s AND status = 'DRAFTED' "
            "AND updated_at < NOW() - INTERVAL '14 days'",
            (niche_id,),
        )
        stale_14d = cur.fetchone()[0]
        conn.close()

        if stale_14d > 0:
            alerts.append(
                Alert(
                    check="stale_drafted",
                    severity="critical",
                    message=(
                        f"{stale_14d} blueprint(s) stuck at DRAFTED >14 days. "
                        f"Operator action required: demote/purge OR investigate "
                        f"why render keeps failing (unrenderable video? DRM?)."
                    ),
                    niche_id=niche_id,
                    details={"stale_14d": stale_14d, "stale_5d": stale_5d},
                )
            )
        elif stale_5d > 0:
            alerts.append(
                Alert(
                    check="stale_drafted",
                    severity="warning",
                    message=(
                        f"{stale_5d} blueprint(s) stuck at DRAFTED >5 days — "
                        f"render failing repeatedly. Check "
                        f"story.media.render_error for the reason."
                    ),
                    niche_id=niche_id,
                    details={"stale_5d": stale_5d},
                )
            )
    except Exception as exc:
        logger.warning(
            "check_stale_drafted failed for niche_id=%s: %s", niche_id, exc
        )
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
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
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
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
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
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
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
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
        cur = conn.cursor()
        cur.execute(
            "SELECT status, count(*) FROM publishing_analytics "
            "WHERE niche_id = %s AND created_at > NOW() - INTERVAL '24 hours' "
            "GROUP BY status",
            (niche_id,),
        )
        by_status = dict(cur.fetchall())
        conn.close()
        # 2026-07-14: include REMOVED_BY_META. Meta's 24h post-survival
        # check demotes silently-removed posts to REMOVED_BY_META — an
        # audience-facing failure that publish_silence already ignores
        # (line ~1051 allowlists INSIGHTS_*) but publish_failures was
        # not counting. Meta-takedown streaks are load-bearing signals
        # of content-policy drift that a niche's captions/hooks may need
        # adjustment.
        failed = by_status.get("FAILED", 0) + by_status.get("REMOVED_BY_META", 0)
        if failed >= 5:
            alerts.append(
                Alert(
                    check="publish_failures",
                    severity="critical",
                    message=f"{failed} publish failures (FAILED + REMOVED_BY_META) in last 24h",
                    niche_id=niche_id,
                    details={"status_breakdown": by_status},
                )
            )
    except Exception as e:
        # Round-3 audit P3 cleanup (2026-06-26): WARN, not DEBUG.
        # This check generates the "≥5 FAILED in 24h" critical alert.
        # When the check itself silently fails (DB hiccup,
        # publishing_analytics schema drift, RLS misconfig), the
        # critical alert NEVER FIRES and operator sees no signal
        # that publishing is degrading. The 2026-06-25 audit
        # specifically called out the YT 0% mystery as exactly this
        # class of "the alert mechanism is broken, not the
        # publishing".
        logger.warning(
            "[health] check_publish_failures DB query failed for niche=%s — "
            "publish-failure alerts will NOT fire for this run: %s",
            niche_id,
            e,
        )
    return alerts


def check_publish_silence(niche_id: str) -> list[Alert]:
    """Alert when a niche has produced ZERO successful publishes.

    ``check_publish_failures`` triggers on ≥5 FAILED in 24h, but a
    full pipeline outage that produces no publish ATTEMPTS at all
    would never trip the existing checks. The 2026-06-17 post-Wave-4
    audit confirmed this gap empirically: 2026-06-14, -15, -16 had
    1+0+1 publishes (full outage for 2 days, near-outage for 1) and
    zero alerts fired.

    Two-tier alert:
      * 0 SUCCESS in last 24h → warning
      * 0 SUCCESS in last 48h → critical (the publisher is REALLY broken)

    Rationale: 1/day is the baseline (5 niches × 1 reel each by design).
    A genuine 24h gap could be a slow operator review queue or a
    once-broken publisher that just self-healed. 48h is unambiguous
    breakage — by then the publisher's launchd has fired 7-10 times
    and produced nothing.

    Counts SUCCESS only (not the metric-flipped INSIGHTS_*H states)
    because we want to know "did publishing happen", not "did
    metric_collector run". Per the R-09 cap-bypass investigation
    (PR #220), status flips to INSIGHTS_6H ~5h post-publish, so any
    publish in the last 24h shows as either SUCCESS or INSIGHTS_6H
    depending on timing. We accept that fuzziness — a publish from 23h
    ago that's already INSIGHTS_6H will count as silence here, which
    nudges us toward false-positive (over-alerting) rather than
    false-negative (missing real outages). The 48h critical threshold
    sidesteps this concern entirely (48h >> 5h flip window).
    """
    alerts: list[Alert] = []
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
        cur = conn.cursor()
        # Count SUCCESS-flavoured statuses over 24h + 48h. R-09 lesson:
        # status flips post-publish, so allowlist all "did-publish"
        # states. Mirrors ``publishing/daily_cap._PUBLISHED_STATUSES``.
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                ) AS in_24h,
                COUNT(*) FILTER (
                    WHERE created_at > NOW() - INTERVAL '48 hours'
                ) AS in_48h
            FROM publishing_analytics
            WHERE niche_id = %s
              AND status IN (
                  'SUCCESS',
                  'INSIGHTS_6H',
                  'INSIGHTS_24H',
                  'INSIGHTS_48H',
                  'INSIGHTS_168H'
              )
            """,
            (niche_id,),
        )
        in_24h, in_48h = cur.fetchone()
        conn.close()
        if in_48h == 0:
            alerts.append(
                Alert(
                    check="publish_silence",
                    severity="critical",
                    message=(
                        f"No successful publishes for {niche_id} in "
                        "the last 48 hours — pipeline is silently dead"
                    ),
                    niche_id=niche_id,
                    details={"publishes_24h": int(in_24h), "publishes_48h": int(in_48h)},
                )
            )
        elif in_24h == 0:
            alerts.append(
                Alert(
                    check="publish_silence",
                    severity="warning",
                    message=(
                        f"No successful publishes for {niche_id} in "
                        "the last 24 hours — investigate before 48h critical"
                    ),
                    niche_id=niche_id,
                    details={"publishes_24h": int(in_24h), "publishes_48h": int(in_48h)},
                )
            )
    except Exception as e:
        # Round-3 audit P3 cleanup (2026-06-26): WARN, not DEBUG.
        # This check generates the "zero successful publishes in 24h"
        # warning alert — the closest thing to a full-outage alarm
        # the system has (per the 2026-06-17 audit that found
        # 1+0+1 publishes across 3 days with zero alerts firing).
        # Silently swallowing the check's own failure recreates that
        # blind spot.
        logger.warning(
            "[health] check_publish_silence DB query failed for niche=%s — "
            "publish-silence alerts will NOT fire for this run: %s",
            niche_id,
            e,
        )
    return alerts


def check_content_pool_health() -> list[Alert]:
    """System-wide health probe for the content_pool routing subsystem.

    PR #516 (2026-06-24): added to close the infrastructure-half-wired
    gap (Q4: loud probe). The AUTONOMY-GAP-ANALYSIS doc flagged that
    77% of blueprints bypass the cross-niche classifier because content
    pool's claim rate is only ~23%. Without a probe, this degradation
    is invisible until someone audits the data flow by hand.

    Two signals:

      * **Producer freshness** — at least 1 row written in the last
        24h. Absence means shared_ingestion isn't producing.
      * **Consumer claim rate** — over the last 7 days, what % of
        routed rows got claimed by trending_video_fetcher? Below 5%
        means consumer's nearly entirely bypassing pool.

    Same fail-open posture as ``check_engagement_health``.
    """
    alerts: list[Alert] = []
    try:
        from genlab_core.storage.tenant_context import pg_connect

        with pg_connect(os.environ.get("DATABASE_URL", ""), niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE fetched_at >= NOW() - INTERVAL '24 hours') AS last_24h,
                        COUNT(*) FILTER (WHERE fetched_at >= NOW() - INTERVAL '7 days') AS last_7d,
                        COUNT(*) FILTER (
                            WHERE fetched_at >= NOW() - INTERVAL '7 days'
                              AND status = 'claimed'
                        ) AS claimed_7d
                    FROM content_pool
                """)
                row = cur.fetchone()
                if row is None:
                    return alerts
                last_24h, last_7d, claimed_7d = row

                # Signal 1: producer freshness
                if last_24h == 0:
                    alerts.append(
                        Alert(
                            check="content_pool_producer_silent",
                            severity="warning",
                            message=(
                                "No content_pool writes in the last 24h. "
                                "shared_ingestion may have stopped firing — check "
                                "genlab-shared-ingestion.timer + journal for tracebacks."
                            ),
                            details={"last_24h_writes": int(last_24h)},
                        )
                    )

                # Signal 2: consumer claim rate
                if last_7d > 0:
                    claim_pct = 100.0 * claimed_7d / last_7d
                    if claim_pct < 5.0:
                        alerts.append(
                            Alert(
                                check="content_pool_consumer_bypass",
                                severity="warning",
                                message=(
                                    f"content_pool claim rate is only {claim_pct:.1f}% "
                                    f"({claimed_7d}/{last_7d} rows in last 7d). "
                                    "FetchTrendingVideos may be bypassing the pool — "
                                    "the cross-niche classifier is barely being "
                                    "exercised."
                                ),
                                details={
                                    "claim_pct": round(claim_pct, 1),
                                    "claimed_7d": int(claimed_7d),
                                    "total_7d": int(last_7d),
                                },
                            )
                        )
    except Exception as exc:
        logger.debug("[content_pool_health] probe failed: %s", exc)
    return alerts
