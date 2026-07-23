"""Auto-experiments API — verdicts + queue depth for the operator.

The auto-experiment lifecycle (2026-07-23):

    strategist (Sun 02:00 UTC)  →  writes causal_hypotheses[].testable_prediction
    parser (daily 03:45 UTC)    →  parses predictions into pending experiments
    lifecycle (every 6h :20)    →  pending → running → completed with per-arm
                                    reward measurement from pending_feedback

This endpoint surfaces the OUTPUT of the lifecycle so the operator can
see whether the system's autonomously-run experiments confirmed or
refuted the strategist's predictions.

Routes:
    GET /api/v1/auto-experiments/summary?niche_id=<id>&limit=<N>
        Recent experiments across all statuses. Response includes
        active_state so the frontend can render the "active vs
        observation only" badge consistent with the other Mission
        Control cards. Empty on cold start; NEVER 500s.

Fail-open: DB errors return {"data": null} rather than 500. Consistent
with the other lifecycle-adjacent cards (StrategistReportCard,
CounterfactualReplayCard).
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint(
    "auto_experiments_api", __name__, url_prefix="/api/v1/auto-experiments"
)

_VALID_NICHES = frozenset(
    {"all", "ai_creators", "gaming", "sports", "movies", "anime"}
)


def _flag_active() -> bool:
    """The lifecycle is 'active' only when the strict-true flag is set.
    Mirrors ``auto_experiment.is_enabled`` — repeated here so the
    endpoint doesn't need to import the module just to check env."""
    return os.environ.get("GENLAB_AUTO_EXPERIMENT_ENABLED", "") in (
        "true",
        "TRUE",
        "True",
    )


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Return recent auto_experiments rows with per-status counts.

    Query params:
        niche_id — one of {ai_creators, gaming, sports, movies, anime,
                   all}. Default 'all'.
        limit    — max rows (default 20, cap 100)

    Response shape::

        {
          "data": {
            "active_state": "active" | "observation_only",
            "flag_env_var": "GENLAB_AUTO_EXPERIMENT_ENABLED",
            "counts": {"pending": N, "running": N, "completed": N},
            "recent": [
              {
                "id": "...", "niche_id": "gaming",
                "status": "completed",
                "spec": {...}, "result": {...},
                "created_at": "...", "started_at": "...",
                "completed_at": "..."
              }, ...
            ],
            "verdicts_last_30d": {
              "met_threshold": N,
              "unmet_threshold": N,
              "insufficient_samples": N
            }
          }
        }
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)

    niche_id = (request.args.get("niche_id") or "all").strip()
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"Invalid niche_id (allowed: {sorted(_VALID_NICHES)})",
            code=400,
        )

    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (ValueError, TypeError):
        limit = 20

    try:
        from psycopg.rows import dict_row

        from genlab_core.scheduling.auto_experiment import list_experiments

        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            filter_niche = None if niche_id == "all" else niche_id
            recent = list_experiments(
                conn, status=None, niche_id=filter_niche, limit=limit
            )

            # Per-status counts across the FULL table (not just the
            # limited window). Operator wants "how many experiments
            # are running right now" without paging.
            count_where = ""
            count_params: list = []
            if filter_niche is not None:
                count_where = " WHERE niche_id = %s"
                count_params.append(filter_niche)
            count_rows = conn.execute(
                f"""
                SELECT status, COUNT(*)::int AS n
                FROM auto_experiments
                {count_where}
                GROUP BY status
                """,
                tuple(count_params),
            ).fetchall()
            counts = {"pending": 0, "running": 0, "completed": 0}
            for r in count_rows:
                if hasattr(r, "get"):
                    s = r.get("status")
                    n = int(r.get("n") or 0)
                else:
                    s, n = r[0], int(r[1] or 0)
                if s in counts:
                    counts[s] = n

            # Verdict tally for last 30 days of completed rows —
            # summary badge on the card.
            verdicts = _tally_verdicts(conn, filter_niche)

    except Exception as exc:  # noqa: BLE001
        logger.warning("[auto_experiments] summary query failed: %s", exc)
        return api_success(data=None)

    return api_success(
        data={
            "active_state": "active" if _flag_active() else "observation_only",
            "flag_env_var": "GENLAB_AUTO_EXPERIMENT_ENABLED",
            "niche_id": niche_id,
            "counts": counts,
            "verdicts_last_30d": verdicts,
            "recent": recent,
        }
    )


def _tally_verdicts(conn, niche_id: str | None) -> dict[str, int]:
    """Count met/unmet/insufficient across last 30d of completed rows.

    Result JSON is JSONB in Postgres; we use ``->>`` and
    ``::bool`` casts. Rows with malformed result JSON count as
    ``insufficient_samples`` (safe fallback — an empty verdict is
    NOT a false positive)."""
    where_extra = ""
    params: list = []
    if niche_id:
        where_extra = " AND niche_id = %s"
        params.append(niche_id)
    try:
        rows = conn.execute(
            f"""
            SELECT
              COALESCE((result->>'met_threshold')::bool, false) AS met,
              COALESCE((result->>'sufficient_samples')::bool, false) AS suff
            FROM auto_experiments
            WHERE status = 'completed'
              AND completed_at >= NOW() - INTERVAL '30 days'
              {where_extra}
            """,
            tuple(params),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[auto_experiments] verdict tally failed: %s", exc)
        return {"met_threshold": 0, "unmet_threshold": 0, "insufficient_samples": 0}

    met_c = unmet_c = insuff_c = 0
    for r in rows:
        if hasattr(r, "get"):
            met = bool(r.get("met"))
            suff = bool(r.get("suff"))
        else:
            met, suff = bool(r[0]), bool(r[1])
        if not suff:
            insuff_c += 1
        elif met:
            met_c += 1
        else:
            unmet_c += 1
    return {
        "met_threshold": met_c,
        "unmet_threshold": unmet_c,
        "insufficient_samples": insuff_c,
    }
