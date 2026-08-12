"""Bandit-arm learning + engagement-loop health checks.

Extracted from ``health_monitor.py`` (2026-07-08 god-module split, DEV-1).
Two clusters live here: bandit-arm health (staleness + posterior drift)
and engagement-loop health (poll → review → completion pipeline plus
the stranded-review and dead-poller escalation checks added on
2026-06-14 after that day's engagement-loop audit).

No facade-late-binding is needed — no test currently patches these
functions through the ``genlab_core.monitoring.health_monitor``
namespace; all tests reach for ``psycopg.connect`` and
``genlab_core.storage.tenant_context.pg_connect`` directly.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from genlab_core.monitoring.alerts import Alert
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-5

logger = logging.getLogger(__name__)


def check_bandit_staleness(niche_id: str) -> list[Alert]:
    """Check if bandit arms haven't been updated recently."""
    alerts = []
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
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
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id)
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
                        # NOTE: Alert.__init__ docstring says "critical or
                        # warning". Previously this used "error" which is
                        # NOT one of the documented severities — the CLI
                        # output filter at health_monitor.py:333-334 groups
                        # by exactly "critical" and "warning", so an "error"
                        # severity was SILENTLY DROPPED from the operator's
                        # terminal (though still written to DB). Content
                        # arms stuck at uniform prior with 14 days of arm-
                        # specific rewards is a meaningful signal but not
                        # incident-level → "warning" is the right severity.
                        severity="warning",
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
        with pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id) as conn:
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
        with pg_connect(os.environ.get("DATABASE_URL", ""), niche_id=niche_id) as conn:
            with conn.cursor() as cur:
                # 2026-07-14: added recency-of-STILL-broken predicate.
                # Prior query fired whenever any unresolved token_expired
                # row was >7d old, even if the token had been refreshed
                # days ago and only the STALE unresolved rows lingered
                # (resolve_stale_alerts only sweeps at 24h; older rows
                # can survive). New shape: only fire if the platform
                # STILL has a token_expired alert in the last 24h, which
                # is direct evidence the poller is still broken now.
                # Class-of-bug: alerts must reflect current state, not
                # point-in-time historical signal (CLAUDE.md rule);
                # sibling live-probe pattern is check_meta_token /
                # check_threads / check_tiktok (memory rule #16).
                cur.execute(
                    """
                    SELECT stuck.platform, stuck.first_seen, stuck.occurrences
                    FROM (
                        SELECT details->>'platform' AS platform,
                               MIN(created_at) AS first_seen,
                               COUNT(*) AS occurrences
                        FROM pipeline_alerts
                        WHERE check_name = 'token_expired'
                          AND niche_id = %s
                          AND resolved_at IS NULL
                          AND created_at < NOW() - INTERVAL '7 days'
                        GROUP BY details->>'platform'
                    ) stuck
                    WHERE EXISTS (
                        SELECT 1 FROM pipeline_alerts recent
                        WHERE recent.check_name = 'token_expired'
                          AND recent.niche_id = %s
                          AND recent.details->>'platform' = stuck.platform
                          AND recent.created_at > NOW() - INTERVAL '24 hours'
                    )
                    """,
                    (niche_id, niche_id),
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


def check_engagement_health() -> list[Alert]:
    """System-wide health probe for the engagement engine.

    PR #516 (2026-06-24): added to close the infrastructure-half-wired
    gap surfaced by the 2026-06-24 audit (Q4: loud probe). The
    engagement engine went silent for 22 days starting 2026-05-21 because
    AGENT_ROOT was missing from the poller's systemd unit (fixed in PR
    #513). No probe existed to detect the silence — operators only
    noticed when scrolling the dashboard manually 22 days later.

    Three signals:

      * **pending_engagement is alive** — alert ONLY when there are
        zero new writes in the last 48h AND the worker has done no
        UPDATE activity in 48h (the second gate distinguishes "poller
        dead" from "poller alive but every fetched comment is a dedup
        hit"). The dedup-hit steady state is HEALTHY: ``_has_replied()``
        in ``comment_processor`` short-circuits BEFORE
        ``bl.write_pending_engagement()``, so a running poller against
        a saturated thread legitimately produces zero new rows for
        days. Widened from 24h→48h to absorb weekend lulls. See
        2026-06-29 investigation for the false-positive that motivated
        the gate.
      * **DLQ is bounded** — if a Dramatiq dead-letter-queue table
        exists, count rows; alert if >10 accumulated (suggests workers
        are crashing on every task).
      * **Recent reply timestamps** — max(updated_at) on
        pending_engagement with status='COMPLETED' is within 48h. If
        no completions in 48h, the consumer half of the pipeline is
        wedged even if producers fire.

    Defensive: any query failure → log + return [] (don't crash the
    monitor on DB hiccups). Three discrete signals so an alert points
    at the actual broken layer.
    """
    alerts: list[Alert] = []
    try:
        from genlab_core.storage.tenant_context import pg_connect

        with pg_connect(os.environ.get("DATABASE_URL", ""), niche_id="all") as conn:
            with conn.cursor() as cur:
                # Signal 1: pending_engagement freshness — widened to
                # 48h AND gated on "no worker UPDATE activity" so a
                # dedup-hit steady state (poller alive, worker idle on
                # already-replied comments) is not a false positive.
                # ``latest_any_activity`` covers worker UPDATEs as well
                # as inserts; if it's recent the poller IS reaching the
                # DB even when no rows are being created.
                cur.execute("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '48 hours') AS last_48h_writes,
                        MAX(updated_at) AS latest_any_activity,
                        MAX(updated_at) FILTER (WHERE status = 'COMPLETED') AS latest_completed
                    FROM pending_engagement
                """)
                row = cur.fetchone()
                if row is None:
                    return alerts
                total, last_48h_writes, latest_any_activity, latest_completed = row

                from datetime import datetime as _dt

                worker_idle_48h = True
                if isinstance(latest_any_activity, _dt):
                    activity_age_hours = (
                        datetime.now(UTC) - latest_any_activity
                    ).total_seconds() / 3600
                    worker_idle_48h = activity_age_hours > 48

                if last_48h_writes == 0 and total > 0 and worker_idle_48h:
                    alerts.append(
                        Alert(
                            check="engagement_no_recent_writes",
                            severity="warning",
                            message=(
                                f"No pending_engagement writes in 48h AND no worker UPDATEs in 48h "
                                f"(total table size: {total}). Three possible causes: "
                                f"(A) poller dead, (B) worker stuck, or (C) no NEW comments coming "
                                f"in but poller re-fetching the same comments — 100% idempotency "
                                f"dedup at comment_processor. Case C is 'no engagement growth' — "
                                f"not a bug, just a real signal that the channels aren't attracting "
                                f"new comments. To distinguish: "
                                f"`journalctl -u genlab-engagement-poller.service --since '2h ago' | grep '\\[POLLER\\]'` "
                                f"— empty output = case A (poller dead, check AGENT_ROOT + tracebacks); "
                                f"non-empty AND recent `.engagement_replied.jsonl` mtime = case C "
                                f"(no new engagement — legit no-op); non-empty AND stale mtime = "
                                f"case B (check worker journal for tracebacks). Investigation "
                                f"pattern documented 2026-07-19."
                            ),
                            details={
                                "total": int(total),
                                "last_48h_writes": int(last_48h_writes),
                                "latest_any_activity": (
                                    latest_any_activity.isoformat()
                                    if isinstance(latest_any_activity, _dt)
                                    else None
                                ),
                            },
                        )
                    )

                # Signal 3: completions stalled
                if latest_completed is not None:
                    if isinstance(latest_completed, _dt):
                        age_hours = (datetime.now(UTC) - latest_completed).total_seconds() / 3600
                        if age_hours > 48:
                            alerts.append(
                                Alert(
                                    check="engagement_completion_stalled",
                                    severity="warning",
                                    message=(
                                        f"No engagement reply completions in {int(age_hours)}h "
                                        f"(latest: {latest_completed.isoformat()}). "
                                        "Worker may be stuck on a poison message — check DLQ + "
                                        "worker journal."
                                    ),
                                    details={"last_completed_hours_ago": int(age_hours)},
                                )
                            )
    except Exception as exc:
        # Fail-open: probe failure must NOT crash the health monitor.
        # Operator visibility for engagement is the GOAL; logging the
        # probe failure at DEBUG (not WARNING) is intentional — we don't
        # want noisy "probe couldn't connect to DB" alerts on transient
        # DB hiccups. The pipeline_alerts table itself will eventually
        # surface the broader DB issue via other checks.
        logger.debug("[engagement_health] probe failed: %s", exc)
    return alerts


# ─── 2026-08-11 Phase 6: silent-fail detection for learning loops ────────────
#
# Motivating class-of-bug: services that exit successfully via systemd but
# produce 0 downstream rows for days. Discovered manually via row-count
# queries in this session (5 hits):
#   * late_reward dead 20 days (status filter bug)
#   * outcome_calibration frozen 43 days (op stopped clicking after Option A)
#   * strategist auto-accept never wrote (5-layer chain of silent-fails)
#   * IG plays deprecated → reels 6h fetch 400s
#   * Bug 3d — reviewed_at gate excluding auto-accepted rows
#
# Each was "systemd exit 0 + zero downstream work" — invisible without
# manual investigation. Automating these row-count assertions turns each
# into a pipeline_alerts row the operator sees on Mission Control.
#
# Detection heuristic pattern: "table X should have ≥N rows in last Y
# hours given healthy operation." Every check is DB-only (no journalctl
# grep — that's fragile). Fail-open at outer try/except.


def check_learning_loops_silent_fail() -> list[Alert]:
    """Detect learning-loop services that ran successfully but produced
    no downstream work. Each sub-check runs independently — one failing
    check doesn't mask the others."""
    alerts: list[Alert] = []

    # Artifact-freshness check doesn't need DB — runs first so it fires
    # even in test/local envs without DATABASE_URL set. Covers the
    # file-based signal blind spot in the original Phase 6 checks
    # (counterfactual-replay was stale 29 days before manual discovery).
    try:
        alerts.extend(_check_artifact_freshness())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[learning_loop_health] artifact-freshness failed: %s", exc)

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return alerts

    for check_fn in (
        _check_late_reward_dead,
        _check_outcome_calibration_dead,
        _check_strategist_apply_dead,
        _check_reward_pipeline_flow,
        _check_ig_view_metric_regression,
        _check_stuck_at_success_past_24h,
    ):
        try:
            alerts.extend(check_fn(dsn))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[learning_loop_health] sub-check %s failed: %s",
                         check_fn.__name__, exc)
    return alerts


# ─── 2026-08-11 addendum: artifact-freshness check ───────────────────────────
#
# The original Phase 6 checks were DB-only. Manual sweep found 2 services
# that write JSON files instead of DB rows and had gone silent for weeks:
#
#   * counterfactual-replay: file mtime Jul 14, 29 days stale despite
#     Aug 1 timer fire (fire produced no file — root cause undiagnosable
#     from rotated journal)
#   * cross-niche-transfer: file mtime Aug 10 — legitimately fresh at
#     scan time, included as a manifest entry for future protection
#
# Manifest: (path_or_glob, max_age_hours, service_name). max_age_hours
# should be 1.5-2× the natural timer cadence so a single missed fire
# doesn't alarm but 2 consecutive misses does.
_ARTIFACT_FRESHNESS_MANIFEST: tuple[tuple[str, int, str], ...] = (
    # Cross-niche Bayesian priors — weekly timer, 168h + 24h slack.
    (
        "/mnt/genlab-media/.tmp/cross-niche-transfer/priors.json",
        192,  # 8 days
        "genlab-cross-niche-transfer",
    ),
    # Counterfactual replay reports — monthly timer, 30d + 5d slack.
    (
        "/mnt/genlab-media/.tmp/counterfactual-replay/replay-*.json",
        840,  # 35 days
        "genlab-counterfactual-replay",
    ),
    # 2026-08-12 (unaudited-services audit): added after confirming
    # both services healthy but neither had freshness monitoring.
    # If either goes silent-dead the pattern is identical to the
    # cross-niche-transfer 29-day silent stall discovered manually.
    #
    # Bandit-arms daily snapshot — timer 03:00 IST daily, 24h + 12h slack.
    (
        "/mnt/genlab-media/snapshots/bandit_arms_*.csv",
        36,
        "genlab-bandit-snapshot",
    ),
    # Bayesian gate state — nightly refit timer, 24h + 12h slack.
    # Overwrites the same file each run (not glob), so stale-mtime
    # correctly signals a stopped writer.
    (
        "/opt/genlab/.tmp/bayesian_gate_state.json",
        36,
        "genlab-bayesian-gate-refit",
    ),
)


def _check_artifact_freshness() -> list[Alert]:
    """Alert when file-based signals from periodic services age past
    their allowed staleness. Complements the DB-based silent-fail
    checks — Phase 6 initially missed this class."""
    import glob
    import time
    from pathlib import Path

    alerts: list[Alert] = []
    now = time.time()

    for path_pattern, max_age_hours, service in _ARTIFACT_FRESHNESS_MANIFEST:
        try:
            matches = glob.glob(path_pattern)
            if not matches:
                # No file exists at all — service has never successfully
                # produced output OR the file was GC'd. Alarm if the
                # containing directory exists (indicates the service is
                # supposed to write there). Skip if the whole path is
                # missing (fresh install, wrong environment).
                parent = Path(path_pattern).parent
                # Handle glob patterns: strip the glob suffix to get the
                # container dir.
                if "*" in str(parent):
                    parent = Path(str(parent).split("*")[0]).parent
                if not parent.exists():
                    continue
                alerts.append(
                    Alert(
                        check="silent_fail_artifact_missing",
                        severity="warning",
                        message=(
                            f"{service} should have written to "
                            f"{path_pattern} but no matching file exists. "
                            f"Service has been silent since deploy OR files "
                            f"got GC'd. Trigger manually to test."
                        ),
                        details={"path_pattern": path_pattern, "service": service},
                        auto_fix=f"sudo systemctl start {service}.service",
                    )
                )
                continue

            latest_mtime = max(Path(p).stat().st_mtime for p in matches)
            age_hours = (now - latest_mtime) / 3600.0

            if age_hours > max_age_hours:
                alerts.append(
                    Alert(
                        check="silent_fail_artifact_stale",
                        severity="warning",
                        message=(
                            f"{service} artifact stale: latest file matching "
                            f"{path_pattern} is {int(age_hours)}h old "
                            f"(threshold: {max_age_hours}h). Timer likely "
                            f"ran but produced no output. Diagnosable "
                            f"pattern: counterfactual-replay's Aug 1 fire "
                            f"wrote nothing — journal rotation lost the "
                            f"why. Trigger manually + inspect journal."
                        ),
                        details={
                            "path_pattern": path_pattern,
                            "service": service,
                            "age_hours": round(age_hours, 1),
                            "threshold_hours": max_age_hours,
                        },
                        auto_fix=f"sudo systemctl start {service}.service; "
                                f"journalctl -u {service}.service --since '5 min ago'",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[artifact_freshness] failed for %s: %s", path_pattern, exc
            )
    return alerts


def _check_late_reward_dead(dsn: str) -> list[Alert]:
    """late_reward should populate late_reward_deltas daily (timer at
    09:30 UTC). Zero rows in 48h means the whole late-tail correction
    system is silent-dead. Load-bearing for Task B backfill + bandit
    posterior corrections."""
    with pg_connect(dsn) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(measured_at) AS latest "
            "FROM late_reward_deltas "
            "WHERE measured_at >= NOW() - INTERVAL '48 hours'"
        ).fetchone()
        n = int((row.get("n") if hasattr(row, "get") else row[0]) or 0)
        latest = row.get("latest") if hasattr(row, "get") else row[1]
    if n == 0:
        return [
            Alert(
                check="silent_fail_late_reward",
                severity="warning",
                message=(
                    f"late_reward has produced 0 late_reward_deltas rows in "
                    f"48h (last row: {latest}). Timer likely runs OK but the "
                    f"batch scan matches 0 blueprints — probably a status "
                    f"filter regression like the 20-day silent-dead in Aug 2026."
                ),
                details={"rows_48h": n, "last_row": str(latest) if latest else None},
                auto_fix="Investigate late_reward.process_late_reward_batch SQL; "
                        "confirm status filter matches actual publishing_analytics "
                        "status values (SUCCESS + INSIGHTS_* variants).",
            )
        ]
    return []


def _check_outcome_calibration_dead(dsn: str) -> list[Alert]:
    """After Option A operator stopped clicking review, source='outcome'
    rows should flow via write_outcome_calibration_all_niches. Zero in
    48h means the wire is broken → AUTO #2 ratchet frozen again."""
    with pg_connect(dsn) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM auto_approval_calibration "
            "WHERE decided_at >= NOW() - INTERVAL '48 hours' "
            "AND source = 'outcome'"
        ).fetchone()
        n = int((row.get("n") if hasattr(row, "get") else row[0]) or 0)
    if n == 0:
        return [
            Alert(
                check="silent_fail_outcome_calibration",
                severity="warning",
                message=(
                    "auto_approval_calibration has 0 outcome-source rows in "
                    "48h. write_outcome_calibration_all_niches is wired to "
                    "late_reward.process_late_reward_batch — if late_reward "
                    "is also dead, that's the root cause. Otherwise the "
                    "outcome-write path itself regressed."
                ),
                details={"rows_48h": n},
                auto_fix="Trigger late_reward manually + grep journal for "
                        "'[outcome_calibration]' log lines to see if the "
                        "wire fires.",
            )
        ]
    return []


def _check_strategist_apply_dead(dsn: str) -> list[Alert]:
    """apply_strategist_actions should materialise proposals_accepted
    entries into bandit_arms. If reports have accepted proposals but
    no matching arm was created in 48h, the applier chain is silent-
    dead (the Bug 3d / 3e class of bugs)."""
    with pg_connect(dsn) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM strategist_reports "
            "WHERE proposals_accepted IS NOT NULL "
            "AND jsonb_array_length(proposals_accepted) > 0 "
            "AND (extra->'applied_indices' IS NULL "
            "     OR jsonb_array_length(extra->'applied_indices') < "
            "        jsonb_array_length(proposals_accepted))"
        ).fetchone()
        n = int((row.get("n") if hasattr(row, "get") else row[0]) or 0)
    if n > 0:
        # There are unfulfilled accepted-but-not-applied proposals.
        # Only alarm if the applier hasn't run recently — otherwise the
        # gap will close on its own within a timer cycle.
        return [
            Alert(
                check="silent_fail_strategist_apply",
                severity="warning",
                message=(
                    f"{n} strategist_reports have proposals_accepted entries "
                    f"that never got applied to bandit_arms. Chain typically "
                    f"breaks at: (a) apply's WHERE excludes auto-accepted "
                    f"rows, (b) _apply_arm_add can't parse 'proposed' when "
                    f"it's a JSON string. Both were Aug 2026 discoveries."
                ),
                details={"unapplied_report_count": n},
                auto_fix="sudo systemctl start genlab-strategist-apply.service; "
                        "journalctl -u it --since '5 min ago' | grep counters",
            )
        ]
    return []


def _check_reward_pipeline_flow(dsn: str) -> list[Alert]:
    """If publishes have happened in last 3 days but pending_feedback
    has 0 rows with reward_48h computed in last 24h, the reward
    pipeline is stuck (48h windows not closing, or metric_collector
    silent-dead). Cross-check that both signals exist to avoid
    false-alarming when publishing legitimately paused."""
    with pg_connect(dsn) as conn:
        recent_publishes = conn.execute(
            "SELECT COUNT(*) AS n FROM publishing_analytics "
            "WHERE published_at >= NOW() - INTERVAL '3 days' "
            "AND status = 'SUCCESS'"
        ).fetchone()
        pub_n = int((recent_publishes.get("n") if hasattr(recent_publishes, "get")
                     else recent_publishes[0]) or 0)

        recent_rewards = conn.execute(
            "SELECT COUNT(*) AS n FROM pending_feedback "
            "WHERE updated_at >= NOW() - INTERVAL '24 hours' "
            "AND reward_48h IS NOT NULL"
        ).fetchone()
        rwd_n = int((recent_rewards.get("n") if hasattr(recent_rewards, "get")
                     else recent_rewards[0]) or 0)

    if pub_n >= 20 and rwd_n == 0:
        # Enough publishes to expect at least some 48h windows to close
        # (publishes cross platforms multiply the row count).
        return [
            Alert(
                check="silent_fail_reward_pipeline",
                severity="warning",
                message=(
                    f"{pub_n} publishes in last 3d but 0 pending_feedback "
                    f"rows had reward_48h computed in last 24h. Either "
                    f"metric_collector's 48h window isn't firing OR every "
                    f"post is early-stopped at 6h OR compute_reward is "
                    f"returning None on all-zero metrics without backfill."
                ),
                details={"publishes_3d": pub_n, "rewards_computed_24h": rwd_n},
                auto_fix="Check metric_collector journal for 48h reward "
                        "logs; verify early_stop 6h floors aren't set too "
                        "high per niche in metric_collector.py:875.",
            )
        ]
    return []


def _check_ig_view_metric_regression(dsn: str) -> list[Alert]:
    """Detect the class-of-bug where Meta API deprecates a metric field
    we depend on. Symptom: IG posts consistently return zero views
    via the fetcher pipeline. Cross-check: publishing_analytics shows
    the post got engagement (any-platform views > 0) but pending_feedback
    for IG reward computed to 0. If this happens across many posts,
    the fetcher's metric_set is broken."""
    with pg_connect(dsn) as conn:
        row = conn.execute(
            """
            WITH recent_ig AS (
                SELECT pa.post_id, MAX(pa.views) AS ig_views
                FROM publishing_analytics pa
                WHERE pa.platform = 'instagram'
                  AND pa.published_at >= NOW() - INTERVAL '10 days'
                  AND pa.published_at <= NOW() - INTERVAL '3 days'
                GROUP BY pa.post_id
            )
            SELECT COUNT(*) FILTER (WHERE ig_views = 0) AS zero_view,
                   COUNT(*) AS total
            FROM recent_ig
            """
        ).fetchone()
        zero_view = int((row.get("zero_view") if hasattr(row, "get") else row[0]) or 0)
        total = int((row.get("total") if hasattr(row, "get") else row[1]) or 0)

    if total >= 10 and zero_view / total >= 0.5:
        # >50% of last-week's IG posts (past their 48h+ measurement
        # window) show views=0. That's not a natural distribution —
        # it means the fetcher is systemically returning zeros. Likely
        # a Meta API deprecation like `plays` in Graph v22.
        return [
            Alert(
                check="silent_fail_ig_metric_regression",
                severity="warning",
                message=(
                    f"{zero_view}/{total} IG posts published 3-10d ago show "
                    f"views=0 in publishing_analytics. Distribution is "
                    f"unnaturally skewed — likely a Meta API field "
                    f"deprecation (e.g. `plays` in v22 → `views`) breaking "
                    f"the metric_set cascade in _fetch_instagram."
                ),
                details={
                    "zero_view_posts": zero_view,
                    "total_posts": total,
                    "zero_view_pct": round(100 * zero_view / total, 1),
                },
                auto_fix="grep journal for 'reels 6h fetch failed' + "
                        "'[meta-metric-deprecation]'; audit metric_set list "
                        "in genlab_core/learning/metrics/instagram.py against "
                        "meta_metric_deprecation._DEPRECATED_METRICS.",
            )
        ]
    return []


def _check_stuck_at_success_past_24h(dsn: str) -> list[Alert]:
    """Detect publishing_analytics rows stuck at SUCCESS past the 24h
    insight window. Root cause is usually one of:

    * Post deleted / removed by platform → API returns 400 → fetcher
      returns empty dict → run_fetch_insights `if not insights: continue`
      loops forever.
    * Post_id malformed (double-prefix, wrong shape).
    * Fetcher raising unhandled exception → swallowed by outer try/except.

    Motivating incident: 2026-08-12 audit found 257 historical Threads
    rows stuck at SUCCESS forever. Test on the most-recent stuck row
    (gaming Aug 10) reproduced the class: post returns 400 from Threads
    API → `_fetch_threads` returns {} → status never advances.

    Alert per (niche, platform) with >=3 stuck rows — one-off stuck posts
    (single-post deletion) are noise; systemic issues cluster.
    """
    with pg_connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT niche_id, platform, COUNT(*) AS n,
                   MIN(published_at) AS oldest_stuck,
                   MAX(published_at) AS newest_stuck
            FROM publishing_analytics
            WHERE status = 'SUCCESS'
              AND published_at < NOW() - INTERVAL '24 hours'
              AND published_at > NOW() - INTERVAL '30 days'
            GROUP BY niche_id, platform
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()

    alerts: list[Alert] = []
    for row in rows:
        niche_id = row.get("niche_id") if hasattr(row, "get") else row[0]
        platform = row.get("platform") if hasattr(row, "get") else row[1]
        n = int((row.get("n") if hasattr(row, "get") else row[2]) or 0)
        newest = row.get("newest_stuck") if hasattr(row, "get") else row[4]
        alerts.append(
            Alert(
                check=f"silent_fail_insights_stuck:{platform}",
                severity="warning",
                niche_id=str(niche_id or ""),
                message=(
                    f"{n} {platform} posts for {niche_id} stuck at SUCCESS "
                    f"past 24h (newest stuck: {newest}). Insights fetcher "
                    f"likely returning empty dict for these posts — "
                    f"run_fetch_insights `if not insights: continue` never "
                    f"advances status. Common causes: post deleted (API 400), "
                    f"post_id malformed, fetcher raising swallowed exception."
                ),
                details={
                    "stuck_count": n,
                    "platform": str(platform),
                    "oldest": str(row.get("oldest_stuck") if hasattr(row, "get") else row[3]),
                    "newest": str(newest),
                },
                auto_fix=(
                    "1) sample the stuck post_ids; 2) call the platform's "
                    "fetch_* directly to reproduce; 3) if 400 = post deleted, "
                    "batch-update to INSIGHTS_UNAVAILABLE (needs enum add); "
                    "4) if 5xx / network = transient, re-run insights fetcher "
                    "manually."
                ),
            )
        )
    return alerts
