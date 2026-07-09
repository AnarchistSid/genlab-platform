"""Publishing & system alerts API.

Routes:
    GET /api/v1/alerts/publishing  -- categorized publishing alerts
    GET /api/v1/alerts/system      -- system-wide health alerts
    GET /api/v1/alerts/critical    -- unresolved CRITICAL pipeline_alerts
                                      rows (the visibility surface that
                                      let the 2026-05-20+ WARP outage go
                                      unnoticed for 20+ days — the system
                                      had been screaming, the dashboard
                                      wasn't relaying)
"""

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("alerts_api", __name__, url_prefix="/api/v1/alerts")


@bp.route("/publishing")
def publishing_alerts():
    """Return categorized publishing alerts."""
    try:
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return api_error(error="DATABASE_URL not configured", code=503)

        # TODO: migrate to shared connection pool when BacklogClient supports raw SQL
        with pg_connect(
            dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
        ) as conn:
            critical = []
            warning = []
            info = []

            # CRITICAL: Blueprints approved but failed to publish (have error_message)
            pf = conn.execute(
                "SELECT niche_id, COUNT(*) as cnt FROM blueprints "
                "WHERE status = 'VISUAL_READY' AND action_taken = 'approved' "
                "AND error_message IS NOT NULL AND error_message != '' "
                "GROUP BY niche_id"
            ).fetchall()
            if pf:
                critical.append(
                    {
                        "type": "publish_failed",
                        "count": sum(r["cnt"] for r in pf),
                        "niches": [r["niche_id"] for r in pf],
                    }
                )

            # WARNING: High failure rate (24h)
            rates = conn.execute(
                "SELECT "
                "  COUNT(*) FILTER (WHERE status='SUCCESS') as ok, "
                "  COUNT(*) FILTER (WHERE status='FAILED') as fail "
                "FROM publishing_analytics "
                "WHERE created_at > NOW() - INTERVAL '24 hours'"
            ).fetchone()
            total = (rates["ok"] or 0) + (rates["fail"] or 0)
            if total > 0:
                fail_rate = int((rates["fail"] or 0) / total * 100)
                if fail_rate > 20:
                    warning.append(
                        {"type": "high_failure_rate", "rate": fail_rate, "period": "24h"}
                    )

            # WARNING: Partial publishes pending retry
            #
            # 2026-07-09 (task #618): added ``updated_at >= NOW() - 7d``
            # filter. Before this, the query counted EVERY historical
            # partial publish since inception — 373 rows spanning
            # 2026-03-21 → 2026-07-09 on the day the fix landed. 99% of
            # those were archaeological failures whose platforms have
            # long-since either succeeded on retry or been abandoned as
            # unpublishable. Alerting on them each morning was pure
            # noise — the operator couldn't retry a 4-month-old failure
            # productively.
            #
            # The 7-day window matches operational actionability:
            # anything failed within the last week is a candidate for
            # manual retry or investigation; older is archaeology and
            # belongs in a separate historical view (not on Mission
            # Control's front page).
            partial = conn.execute(
                "SELECT COUNT(*) as cnt FROM blueprints "
                "WHERE status = 'PUBLISHED' "
                "AND platform_publish_status::text LIKE '%FAILED%' "
                "AND updated_at >= NOW() - INTERVAL '7 days'"
            ).fetchone()["cnt"]
            if partial > 0:
                warning.append({"type": "partial_publish", "count": partial})

            # WARNING: Stale VISUAL_READY > 7 days
            #
            # 2026-07-09 (task #618): added the ``scheduled_for IS NULL
            # OR scheduled_for < NOW()`` clause. Before this, the query
            # counted EVERY blueprint at status VISUAL_READY older than
            # 7 days — which included blueprints legitimately queued
            # for FUTURE publish dates (the nightly scheduler reaches
            # up to 14 days ahead per niche). Screenshot on the day of
            # this fix showed 32 alerted; only 4 were actually stuck.
            # The other 28 were healthy scheduled posts that just
            # happened to have been created >7 days before their
            # scheduled_for date.
            #
            # "Stuck" is either:
            #   * scheduled_for IS NULL   (never scheduled — truly orphan)
            #   * scheduled_for < NOW()   (scheduled in the past —
            #                              publisher missed the slot)
            #
            # Future-scheduled blueprints don't need operator attention;
            # they're the intended state of the queue.
            stale = conn.execute(
                "SELECT COUNT(*) as cnt FROM blueprints "
                "WHERE status = 'VISUAL_READY' "
                "AND created_at < NOW() - INTERVAL '7 days' "
                "AND (scheduled_for IS NULL OR scheduled_for < NOW())"
            ).fetchone()["cnt"]
            if stale > 0:
                warning.append({"type": "stale_visual_ready", "count": stale, "age": ">7 days"})

            # INFO: YouTube quota + today's publishing summary — collapsed
            # from 2 separate queries on publishing_analytics into 1 with
            # FILTER clauses. Both shared the same CURRENT_DATE window, so
            # the outer WHERE constrains the index scan to today's rows
            # and the 3 FILTER clauses bucket them into the metrics we
            # surface. See PR #398.
            today_metrics = conn.execute(
                "SELECT "
                "  COUNT(*) FILTER (WHERE status='SUCCESS') as ok, "
                "  COUNT(*) FILTER (WHERE status='FAILED') as fail, "
                "  COUNT(*) FILTER (WHERE platform='youtube' AND status='SUCCESS') as yt_today "
                "FROM publishing_analytics "
                "WHERE created_at >= CURRENT_DATE AND created_at < CURRENT_DATE + INTERVAL '1 day'"
            ).fetchone()
            yt_today = today_metrics["yt_today"] or 0
            info.append(
                {"type": "youtube_quota", "uploads_today": yt_today, "daily_limit": "~6 uploads"}
            )
            info.append(
                {
                    "type": "today_summary",
                    "published": today_metrics["ok"] or 0,
                    "failed": today_metrics["fail"] or 0,
                }
            )

        # WARNING: Placeholder URLs (example.com) in affiliate catalog.
        # 2026-06-21 (perf): both YAMLs go through the mtime-keyed cache
        # in ``server.core.yaml_cache`` — disk I/O eliminated on the
        # 99% steady-state case (alerting.yaml + affiliate_catalog.yaml
        # rarely change). Was a guaranteed 1-2 disk reads + YAML parses
        # per alerts endpoint call; this endpoint is polled by Mission
        # Control on the same 60s cadence as the overview endpoint.
        try:
            from pathlib import Path

            from server.core.yaml_cache import load_yaml_cached

            _catalog_path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "genlab-core"
                / "config"
                / "affiliate_catalog.yaml"
            )
            _alerting_path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "genlab-core"
                / "config"
                / "alerting.yaml"
            )
            alerting_cfg = load_yaml_cached(_alerting_path) or {}
            placeholder_alert_enabled = alerting_cfg.get("affiliate", {}).get(
                "placeholder_url_alert", False
            )

            if placeholder_alert_enabled:
                catalog = load_yaml_cached(_catalog_path) or {}
                placeholder_count = 0
                placeholder_products = []
                for _niche_id, niche_data in catalog.get("niches", {}).items():
                    for product in niche_data.get("products", []):
                        for _net_name, net_data in product.get("networks", {}).items():
                            url = net_data.get("url", "")
                            if "example.com" in url:
                                placeholder_count += 1
                                placeholder_products.append(product.get("name", "unknown"))
                if placeholder_count > 0:
                    warning.append(
                        {
                            "type": "placeholder_affiliate_urls",
                            "count": placeholder_count,
                            "products": placeholder_products[:10],  # cap at 10 for readability
                        }
                    )
        except Exception as e:
            logger.debug("[Alerts] Placeholder URL check failed: %s", e)

        total_unresolved = len(critical) + len(warning)
        return api_success(
            data={
                "critical": critical,
                "warning": warning,
                "info": info,
                "total_unresolved": total_unresolved,
            }
        )

    except Exception as e:
        logger.error("[Alerts] Publishing query failed: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)


@bp.route("/system")
def system_alerts():
    """Return system-wide health alerts."""
    try:
        alerts = {}

        # Pipeline: check if all niches ran today
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if dsn:
            with pg_connect(
                dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
            ) as conn:
                niches_today = conn.execute(
                    "SELECT DISTINCT niche_id FROM blueprints "
                    "WHERE created_at >= CURRENT_DATE AND created_at < CURRENT_DATE + INTERVAL '1 day'"
                ).fetchall()
                ran = {r["niche_id"] for r in niches_today}
                expected = {"gaming", "sports", "movies", "anime", "ai_creators"}
                missed = expected - ran
                alerts["pipeline"] = {
                    "ran_today": list(ran),
                    "missed_today": list(missed),
                    "all_ran": len(missed) == 0,
                }

                # Database pool
                pool_info = conn.execute(
                    "SELECT count(*) as active FROM pg_stat_activity WHERE datname = 'genlab'"
                ).fetchone()
                alerts["database"] = {
                    "active_connections": pool_info["active"],
                    "max_connections": 100,
                }

        # Disk usage
        import shutil

        genlab_path = os.environ.get("GENLAB_PROJECT_ROOT", "/Users/anarchistsid/GenLab")
        try:
            usage = shutil.disk_usage(genlab_path)
            alerts["disk"] = {
                "usage_pct": int((usage.used / usage.total) * 100),
                "free_gb": round(usage.free / (1024**3), 1),
            }
        except Exception:
            alerts["disk"] = {"usage_pct": -1, "free_gb": -1}

        return api_success(data=alerts)

    except Exception as e:
        logger.error("[Alerts] System query failed: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)


@bp.route("/critical")
def critical_alerts():
    """Return unresolved CRITICAL ``pipeline_alerts`` for the Mission
    Control banner.

    Why this exists
    ---------------

    The ``health_monitor.check_*`` functions (`monitoring/health_monitor.py`)
    write rich diagnostic rows to ``pipeline_alerts``: ``warp_down``,
    ``download_failure``, ``qc_collapse``, ``zero_blueprints``,
    ``missing_media_mass``, ``stuck_publishing`` … with severity,
    message, details, and (for a few categories) ``auto_fix_applied``
    outcomes. Several of those carry an exact remediation in the
    message body — e.g. ``warp_down`` says ``"Run: systemctl restart
    warp-svc"``.

    Before this route, the dashboard had ``/alerts/publishing`` (a
    derived-from-blueprints view) and ``/alerts/system`` (just
    ran-today + DB pool + disk), but NOTHING surfaced the
    ``pipeline_alerts`` rows. The 2026-05-20 WARP outage shows what
    that gap costs: 9 separate ``warp_down`` CRITICAL rows fired over
    20 days, each carrying the exact one-line fix in its ``message``,
    and the operator never saw any of them because the dashboard
    wasn't reading the table.

    Dedup model
    -----------

    The downstream banner deliberately groups by ``(niche_id,
    check_name)`` to avoid wall-of-alerts noise — daily-firing
    download_failure rows would otherwise stack to 24+ identical
    entries. The endpoint returns the rows raw and lets the frontend
    dedup; that keeps server logic dumb and lets the UI surface
    "occurrences=N since first_seen=X" naturally.

    Filtering
    ---------

    Only ``severity='critical'`` and ``resolved_at IS NULL`` rows are
    returned, capped at 50 most recent. Warning-level rows are
    intentionally excluded — the banner is for "you MUST act on this"
    not "FYI". A future ``/alerts/warning`` endpoint can mirror this
    shape if warnings need their own surface.

    Response shape
    --------------

    ::

        {
            "data": {
                "unresolved_critical": [
                    {
                        "id": "uuid-string",
                        "niche_id": "ai_creators",
                        "check_name": "warp_down",
                        "message": "warp-svc not active...",
                        "details": {...},
                        "auto_fix_applied": "not attempted (...)",
                        "created_at": "2026-06-12T07:00:35Z"
                    },
                    ...
                ],
                "count": 11
            }
        }
    """
    try:
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            # Dev environments without a DB connection: degrade
            # gracefully rather than 500. The banner reads ``count`` and
            # hides itself when it's 0.
            return api_success(data={"unresolved_critical": [], "count": 0})

        with pg_connect(
            dsn,
            row_factory=dict_row,
            connect_timeout=5,
            niche_id=request.args.get("niche_id", "all") or "all",
        ) as conn:
            rows = conn.execute(
                """
                SELECT id::text AS id,
                       niche_id,
                       check_name,
                       message,
                       details,
                       auto_fix_applied,
                       created_at
                FROM pipeline_alerts
                WHERE severity = 'critical'
                  AND resolved_at IS NULL
                ORDER BY created_at DESC
                LIMIT 50;
                """
            ).fetchall()

        # Normalise timestamps to ISO strings so the frontend doesn't
        # have to reason about Python datetime serialisation specifics
        # (Flask's default isoformat is fine but explicit is better).
        for r in rows:
            ts = r.get("created_at")
            if ts is not None:
                r["created_at"] = ts.isoformat()

        return api_success(
            data={
                "unresolved_critical": rows,
                "count": len(rows),
            }
        )

    except Exception as e:
        logger.error("[Alerts] Critical query failed: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)
