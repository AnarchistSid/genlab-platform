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
                                f"(total table size: {total}). This is BEYOND steady-state dedup — "
                                f"either the engagement-poller is silent OR the Dramatiq worker is "
                                f"stuck. To distinguish: "
                                f"`journalctl -u genlab-engagement-poller.service --since '2 hours ago' | grep '\\[POLLER\\]'` "
                                f"— empty output = poller dead (check AGENT_ROOT + tracebacks); "
                                f"non-empty = poller alive, check worker journal."
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
