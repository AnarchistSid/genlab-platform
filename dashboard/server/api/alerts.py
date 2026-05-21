"""Publishing & system alerts API.

Routes:
    GET /api/v1/alerts/publishing  -- categorized publishing alerts
    GET /api/v1/alerts/system      -- system-wide health alerts
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
