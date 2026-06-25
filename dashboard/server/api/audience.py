"""Audience growth API — follower/subscriber tracking.

Routes:
    GET /api/v1/audience/current   -- latest follower counts per niche × platform
    GET /api/v1/audience/history   -- daily snapshots for growth charts (last 30 days)
"""

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("audience_api", __name__, url_prefix="/api/v1/audience")


@bp.route("/current")
def current_audience():
    """Latest follower/subscriber counts per niche × platform."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)
    try:
        from psycopg.rows import dict_row

        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            conn.execute("SELECT set_config('app.niche_id', 'all', true)")
            rows = conn.execute("""
                SELECT DISTINCT ON (niche_id, platform, metric_name)
                    niche_id, platform, metric_name, metric_value, snapshot_date
                FROM audience_snapshots
                ORDER BY niche_id, platform, metric_name, snapshot_date DESC
            """).fetchall()

        # Group by niche → platform → metrics
        data: dict = {}
        for r in rows:
            niche = r["niche_id"]
            plat = r["platform"]
            if niche not in data:
                data[niche] = {}
            if plat not in data[niche]:
                data[niche][plat] = {}
            data[niche][plat][r["metric_name"]] = {
                "value": float(r["metric_value"]),
                "as_of": str(r["snapshot_date"]),
            }

        # PR #551 (2026-06-25, SR-F wire pass 12): per-user allowlist
        # filter on the outer niche map. Restricted operator sees only
        # their niche's audience metrics; unrestricted admin sees all.
        from server.auth.niche_allowlist import get_allowed_niches

        _allowed = get_allowed_niches()
        if _allowed is not None:
            data = {k: v for k, v in data.items() if k in _allowed}
        return api_success(data=data)
    except Exception as e:
        logger.error("Audience current error: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)


@bp.route("/history")
def audience_history():
    """Daily follower snapshots for growth charts."""
    days = int(request.args.get("days", 30))
    niche_id = request.args.get("niche_id", "")
    # PR #551 (SR-F wire pass 12): per-user allowlist guard. Two cases:
    # explicit niche_id query → reject if outside allowlist (403);
    # no niche_id → fall through to the unfiltered query + post-filter
    # the result list below so the restricted operator sees only
    # their niches' history rows.
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id and niche_id not in _allowed:
        return api_error(
            error=(
                f"Audience history scoped to niche '{niche_id}' which is "
                f"not in your allowlist (allowed: {sorted(_allowed)}). "
                f"See SR-F (PR #551)."
            ),
            code=403,
        )
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)
    try:
        from psycopg.rows import dict_row

        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            conn.execute("SELECT set_config('app.niche_id', 'all', true)")
            if niche_id:
                rows = conn.execute(
                    """
                    SELECT niche_id, platform, metric_name, metric_value, snapshot_date
                    FROM audience_snapshots
                    WHERE snapshot_date >= CURRENT_DATE - %s::int
                      AND niche_id = %s
                    ORDER BY snapshot_date
                """,
                    (days, niche_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT niche_id, platform, metric_name, metric_value, snapshot_date
                    FROM audience_snapshots
                    WHERE snapshot_date >= CURRENT_DATE - %s::int
                    ORDER BY snapshot_date
                """,
                    (days,),
                ).fetchall()

        data = [
            {
                "niche_id": r["niche_id"],
                "platform": r["platform"],
                "metric": r["metric_name"],
                "value": float(r["metric_value"]),
                "date": str(r["snapshot_date"]),
            }
            for r in rows
        ]
        # PR #551: post-filter the result list when no explicit
        # niche_id was given. The query above pulled all niches in
        # the no-niche-id branch; allowlist trims to the operator's
        # scope. Same logic structure as the /current endpoint.
        if _allowed is not None and not niche_id:
            data = [row for row in data if row.get("niche_id") in _allowed]
        return api_success(data=data)
    except Exception as e:
        logger.error("Audience history error: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)
