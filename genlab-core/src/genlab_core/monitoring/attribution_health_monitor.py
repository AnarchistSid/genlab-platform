"""Detect low attribution_present_pct and surface as a CRITICAL alert.

Post-Markanimation observability follow-up. The Layer 5 dashboard card
(PR #Layer5, 2026-07-11) surfaces the metric in Mission Control, but
an operator only sees it when they open the dashboard. This monitor
runs on a systemd timer and writes into ``pipeline_alerts`` so
Mission Control's CRITICAL banner fires without the operator having
to look.

How it works
------------
Every 30 min via systemd timer::

    scripts/attribution_health_monitor.py

Reads the same underlying data as
``server.core.attribution_health.compute_stats`` — but here directly
against Postgres (no HTTP hop), so the monitor doesn't depend on the
dashboard service being up. Fires when:

  * Any single niche has ``attribution_pct < 90`` (aka ``critical``
    status per the endpoint's threshold constants) AND
    ``total_published ≥ 3`` in the window (avoids paging on a single
    early-morning miss).
  * OR overall attribution_pct < 90 (across all niches summed).

Alert de-dup follows the same pattern as
``anthropic_credit_monitor``: don't write a duplicate CRITICAL alert
if one is already OPEN (resolved_at IS NULL) for the same
``check_name``. Auto-resolver sweeps stale ones after 24h.

Exit code
---------
Always 0. Same contract as anthropic_credit_monitor — informational,
must not cascade into systemd_unit_failed. A silent failure just
delays the next sweep 30 min.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Kept in sync with dashboard's Layer 5 module. If these drift, the
# alert threshold and the dashboard's "critical" pill would show
# different things and confuse the operator.
#
# Post-2026-07-11 audit tightening: threshold raised from 90 → 99.
# The 2026-07-11 gaming case demonstrated that even a single
# uncredited publish is a real audience-facing failure — we already
# retroactively fix them by hand. So the alert should page on
# ANY single miss (99% out of the minimum 3 = 3/3 required for
# healthy). The old 90% threshold allowed 1 in 10 uncredited before
# anyone was told, which defeats the alert's purpose.
CRITICAL_PCT = 99.0
MIN_PUBLISHES_FOR_ALERT = 3

CHECK_NAME = "attribution_health_below_threshold"


def compute_health(
    *,
    window_hours: int = 24,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-niche rows. Same shape as the endpoint's ``compute_stats``.

    Duplicates the endpoint's SQL because we don't want the monitor
    to depend on the dashboard package (systemd wiring installs are
    less brittle if the module is self-contained).
    """
    import psycopg

    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    # 2026-07-14 (class-of-bug scan): import the canonical markers +
    # match case-insensitively. Prior state hardcoded "\U0001f3ac
    # Original:" with case-sensitive LIKE — diverged from
    # platforms/caption_validation.py which lowercases input before
    # comparing. Writer emitting "🎬 ORIGINAL:" would satisfy the L4
    # publisher validator but be silently missed by this L5 monitor —
    # exact same-invariant-two-paths class-of-bug the writer-wire
    # session (2026-07-13) tightened. Same fix at
    # dashboard/server/core/attribution_health.py:70.
    from genlab_core.platforms.caption_validation import (
        _MARKER_FOOTAGE,
        _MARKER_ORIGINAL,
    )

    original_mark = _MARKER_ORIGINAL  # already lowercase
    footage_mark = _MARKER_FOOTAGE  # already lowercase
    niche_ids = ("ai_creators", "anime", "gaming", "movies", "sports")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Post-2026-07-13 audit follow-up: kept in lockstep with
            # dashboard/server/core/attribution_health.py. Previous
            # version only queried 2 fields (caption + facebook_content),
            # which made the monitor even more permissive than the
            # dashboard metric it mirrors — a niche publishing on
            # threads / youtube / twitter with credit in those fields
            # (but no main caption + no fb content) would show 0%
            # attribution and page the operator falsely. Union all 6
            # platform-content fields so the two metrics report the
            # same reality.
            cur.execute(
                """
                -- 2026-07-14 (class-of-bug scan): LOWER() the columns
                -- so the LIKE matches case-insensitively against the
                -- markers (which are already lowercased). Prior state
                -- was case-sensitive → diverged from L4 validator.
                SELECT
                    niche_id,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(caption, '')) LIKE %s
                           OR LOWER(COALESCE(caption, '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'facebook_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'facebook_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'threads_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'threads_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'youtube_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'youtube_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'twitter_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'twitter_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'tiktok_content', '')) LIKE %s
                           OR LOWER(COALESCE(extra->>'tiktok_content', '')) LIKE %s
                    ) AS with_attribution
                FROM blueprints
                WHERE status = 'PUBLISHED'
                  AND updated_at > NOW() - (%s || ' hours')::interval
                GROUP BY niche_id
                ORDER BY niche_id
                """,
                (
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    f"%{original_mark}%",
                    f"%{footage_mark}%",
                    str(window_hours),
                ),
            )
            rows = cur.fetchall()

    row_by_niche = {r[0]: (r[1], r[2]) for r in rows}
    out: list[dict[str, Any]] = []
    for niche in niche_ids:
        total, with_attr = row_by_niche.get(niche, (0, 0))
        pct = round(100.0 * with_attr / total, 1) if total else 0.0
        out.append(
            {
                "niche_id": niche,
                "total_published": int(total),
                "with_attribution": int(with_attr),
                "attribution_pct": pct,
            }
        )
    return out


def scan_and_alert(
    *,
    window_hours: int = 24,
    dsn: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fire the alert when any niche breaches the critical threshold.

    Returns a summary dict for the CLI wrapper. Fail-open on DB
    errors (same discipline as anthropic_credit_monitor).
    """
    summary: dict[str, Any] = {
        "window_hours": window_hours,
        "alert_written": False,
        "errors": 0,
    }

    try:
        rows = compute_health(window_hours=window_hours, dsn=dsn)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[attribution_health_monitor] compute failed: %s", exc)
        summary["errors"] = 1
        return summary

    # Which niches are in critical territory + have enough publishes to
    # justify paging?
    breached = [
        r
        for r in rows
        if r["total_published"] >= MIN_PUBLISHES_FOR_ALERT and r["attribution_pct"] < CRITICAL_PCT
    ]

    # Overall aggregate
    total_all = sum(r["total_published"] for r in rows)
    with_all = sum(r["with_attribution"] for r in rows)
    overall_pct = round(100.0 * with_all / total_all, 1) if total_all else 0.0
    overall_breached = total_all >= MIN_PUBLISHES_FOR_ALERT and overall_pct < CRITICAL_PCT

    summary["breached_niches"] = [r["niche_id"] for r in breached]
    summary["overall_pct"] = overall_pct
    summary["overall_breached"] = overall_breached

    if not breached and not overall_breached:
        # 2026-07-14: auto-resolve any lingering open attribution
        # alerts when the window is healthy again. Prior state: no
        # auto-resolve → after L4 flip + retro-credit backfill,
        # attribution recovers to 100% but the CRITICAL row lingers
        # until `resolve_stale_alerts` sweeps at 24h. Same class-of-
        # bug pattern as sibling monitors' current-state discipline.
        # Best-effort: any DB failure keeps the row (safe fallback).
        try:
            import psycopg

            dsn_ = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
            with psycopg.connect(dsn_) as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pipeline_alerts SET resolved_at = NOW()
                     WHERE check_name = %s AND resolved_at IS NULL
                    """,
                    (CHECK_NAME,),
                )
                summary["auto_resolved"] = cur.rowcount
        except Exception as exc:
            logger.warning(
                "[attribution_health_monitor] auto-resolve failed (leaving "
                "stale row in place; resolve_stale_alerts will sweep at 24h): %s",
                exc,
            )
        return summary

    # Build the alert message
    lines = [
        f"Attribution health critical — window={window_hours}h, overall={overall_pct}%",
    ]
    for r in breached:
        lines.append(
            f"  {r['niche_id']}: {r['with_attribution']}/"
            f"{r['total_published']} ({r['attribution_pct']}%)"
        )
    lines.append(
        "Investigate: check compliance_events for missing_source_attribution + "
        "Layer 5 endpoint /api/v1/attribution-health/stats?window_hours=" + str(window_hours)
    )
    message = "\n".join(lines)

    if dry_run:
        logger.info(
            "[attribution_health_monitor] DRY-RUN would write CRITICAL: %s",
            message,
        )
        summary["alert_written"] = True
        return summary

    # De-dup + write
    try:
        import psycopg

        dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM pipeline_alerts
                     WHERE check_name = %s
                       AND resolved_at IS NULL
                    """,
                    (CHECK_NAME,),
                )
                open_count = cur.fetchone()[0]
                if open_count > 0:
                    logger.info(
                        "[attribution_health_monitor] alert already open "
                        "(n=%d) — skipping duplicate write",
                        open_count,
                    )
                    summary["alert_deduplicated"] = True
                    return summary

                cur.execute(
                    """
                    INSERT INTO pipeline_alerts (
                        niche_id, check_name, severity, message,
                        created_at, resolved_at
                    ) VALUES (
                        %s, %s, %s, %s, NOW(), NULL
                    )
                    """,
                    ("all", CHECK_NAME, "critical", message),
                )
            conn.commit()
        logger.warning(
            "[attribution_health_monitor] wrote CRITICAL alert "
            "(breached_niches=%s, overall_pct=%s)",
            [r["niche_id"] for r in breached],
            overall_pct,
        )
        summary["alert_written"] = True
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[attribution_health_monitor] DB write path failed: %s", exc)
        summary["errors"] = 1

    return summary
