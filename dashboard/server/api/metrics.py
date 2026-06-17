"""Publishing metrics API — success rates, trends, error distribution.

Routes:
    GET /api/v1/metrics/publishing  -- publishing health metrics
"""

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("metrics_api", __name__, url_prefix="/api/v1/metrics")


@bp.route("/publishing")
def publishing_metrics():
    """Publishing health metrics for dashboard."""
    try:
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return api_error(error="DATABASE_URL not configured", code=503)

        with pg_connect(
            dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
        ) as conn:
            # Success rate by platform (last 7 days)
            rates = conn.execute("""
                SELECT platform,
                       COUNT(*) FILTER (WHERE status='SUCCESS') as ok,
                       COUNT(*) FILTER (WHERE status='FAILED') as fail
                FROM publishing_analytics
                WHERE created_at > NOW() - INTERVAL '7 days'
                  AND status IN ('SUCCESS', 'FAILED')
                GROUP BY platform ORDER BY platform
            """).fetchall()

            success_rates = {}
            for r in rates:
                total = (r["ok"] or 0) + (r["fail"] or 0)
                success_rates[r["platform"]] = int(r["ok"] / total * 100) if total else 0

            # Daily trend (last 7 days)
            trend = conn.execute("""
                SELECT DATE(created_at) as day,
                       COUNT(*) FILTER (WHERE status='SUCCESS') as ok,
                       COUNT(*) FILTER (WHERE status='FAILED') as fail
                FROM publishing_analytics
                WHERE created_at > NOW() - INTERVAL '7 days'
                  AND status IN ('SUCCESS', 'FAILED')
                GROUP BY DATE(created_at) ORDER BY day
            """).fetchall()

            daily_trend = [
                {"date": str(r["day"]), "ok": r["ok"] or 0, "fail": r["fail"] or 0} for r in trend
            ]

            # Error distribution (last 7 days) — from error_message column
            errors = conn.execute("""
                SELECT error_message as err, COUNT(*) as cnt
                FROM publishing_analytics
                WHERE status = 'FAILED'
                  AND created_at > NOW() - INTERVAL '7 days'
                  AND error_message IS NOT NULL AND error_message != ''
                GROUP BY error_message
                ORDER BY cnt DESC LIMIT 10
            """).fetchall()

            # Classify errors using our classifier
            error_dist = {"TRANSIENT": 0, "QUOTA": 0, "CREDENTIAL": 0, "CONTENT": 0, "PERMANENT": 0}
            try:
                from genlab_core.publishing.error_classifier import classify

                for r in errors:
                    cls = classify(r["err"] or "", "")
                    error_dist[cls] = error_dist.get(cls, 0) + (r["cnt"] or 0)
            except ImportError:
                for r in errors:
                    error_dist["TRANSIENT"] += r["cnt"] or 0

            # Platform status (green/yellow/red based on 24h success rate)
            status_24h = conn.execute("""
                SELECT platform,
                       COUNT(*) FILTER (WHERE status='SUCCESS') as ok,
                       COUNT(*) FILTER (WHERE status='FAILED') as fail
                FROM publishing_analytics
                WHERE created_at > NOW() - INTERVAL '24 hours'
                  AND status IN ('SUCCESS', 'FAILED')
                GROUP BY platform
            """).fetchall()

            platform_status = {}
            for r in status_24h:
                total = (r["ok"] or 0) + (r["fail"] or 0)
                if total == 0:
                    platform_status[r["platform"]] = "grey"  # no data
                else:
                    rate = r["ok"] / total
                    if rate >= 0.8:
                        platform_status[r["platform"]] = "green"
                    elif rate >= 0.5:
                        platform_status[r["platform"]] = "yellow"
                    else:
                        platform_status[r["platform"]] = "red"

            # Niche success rates (last 7 days)
            niche_rates = conn.execute("""
                SELECT niche_id,
                       COUNT(*) FILTER (WHERE status='SUCCESS') as ok,
                       COUNT(*) FILTER (WHERE status='FAILED') as fail
                FROM publishing_analytics
                WHERE created_at > NOW() - INTERVAL '7 days'
                  AND status IN ('SUCCESS', 'FAILED')
                GROUP BY niche_id ORDER BY niche_id
            """).fetchall()

            by_niche = {}
            for r in niche_rates:
                total = (r["ok"] or 0) + (r["fail"] or 0)
                by_niche[r["niche_id"]] = {
                    "success": r["ok"] or 0,
                    "failed": r["fail"] or 0,
                    "rate": int(r["ok"] / total * 100) if total else 0,
                }

        return api_success(
            data={
                "success_rate_7d": success_rates,
                "daily_trend": daily_trend,
                "error_distribution": error_dist,
                "platform_status": platform_status,
                "by_niche": by_niche,
            }
        )

    except Exception as e:
        logger.error("[Metrics] Publishing query failed: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)
