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

from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("alerts_api", __name__, url_prefix="/api/v1/alerts")


@bp.route("/publishing")
def publishing_alerts():
    """Return categorized publishing alerts."""
    try:
        import psycopg
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return api_error(error="DATABASE_URL not configured", code=503)

        # TODO: migrate to shared connection pool when BacklogClient supports raw SQL
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
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
            partial = conn.execute(
                "SELECT COUNT(*) as cnt FROM blueprints "
                "WHERE status = 'PUBLISHED' "
                "AND platform_publish_status::text LIKE '%FAILED%'"
            ).fetchone()["cnt"]
            if partial > 0:
                warning.append({"type": "partial_publish", "count": partial})

            # WARNING: Stale VISUAL_READY > 7 days
            stale = conn.execute(
                "SELECT COUNT(*) as cnt FROM blueprints "
                "WHERE status = 'VISUAL_READY' "
                "AND created_at < NOW() - INTERVAL '7 days'"
            ).fetchone()["cnt"]
            if stale > 0:
                warning.append({"type": "stale_visual_ready", "count": stale, "age": ">7 days"})

            # INFO: YouTube quota
            yt_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM publishing_analytics "
                "WHERE platform = 'youtube' AND status = 'SUCCESS' "
                "AND created_at >= CURRENT_DATE AND created_at < CURRENT_DATE + INTERVAL '1 day'"
            ).fetchone()["cnt"]
            info.append(
                {"type": "youtube_quota", "uploads_today": yt_today, "daily_limit": "~6 uploads"}
            )

            # INFO: Today's publishing summary
            today = conn.execute(
                "SELECT "
                "  COUNT(*) FILTER (WHERE status='SUCCESS') as ok, "
                "  COUNT(*) FILTER (WHERE status='FAILED') as fail "
                "FROM publishing_analytics "
                "WHERE created_at >= CURRENT_DATE AND created_at < CURRENT_DATE + INTERVAL '1 day'"
            ).fetchone()
            info.append(
                {
                    "type": "today_summary",
                    "published": today["ok"] or 0,
                    "failed": today["fail"] or 0,
                }
            )

        # WARNING: Placeholder URLs (example.com) in affiliate catalog
        try:
            from pathlib import Path

            import yaml

            _catalog_path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "genlab-core"
                / "config"
                / "affiliate_catalog.yaml"
            )
            # Load alerting config to check if placeholder_url_alert is enabled
            _alerting_path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "genlab-core"
                / "config"
                / "alerting.yaml"
            )
            placeholder_alert_enabled = False
            if _alerting_path.exists():
                with open(_alerting_path, encoding="utf-8") as af:
                    alerting_cfg = yaml.safe_load(af) or {}
                placeholder_alert_enabled = alerting_cfg.get("affiliate", {}).get(
                    "placeholder_url_alert", False
                )

            if placeholder_alert_enabled and _catalog_path.exists():
                with open(_catalog_path, encoding="utf-8") as cf:
                    catalog = yaml.safe_load(cf) or {}
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
        import psycopg
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if dsn:
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
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
        import psycopg
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            # Dev environments without a DB connection: degrade
            # gracefully rather than 500. The banner reads ``count`` and
            # hides itself when it's 0.
            return api_success(data={"unresolved_critical": [], "count": 0})

        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
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
