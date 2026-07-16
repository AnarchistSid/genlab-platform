"""Ensemble vote summary endpoint (L5, 2026-07-08 audit).

Read-only observability over the `ensemble_votes` table populated by
`genlab_core.scheduling.ensemble_persist`. Surfaces:

  * Per-component participation rate (how often did each component
    actually vote vs. abstain?)
  * Per-component agreement with the final ensemble recommendation
    (does the bandit tend to agree with the ensemble more than the
    LLM judge does? — helps calibrate weights)
  * Per-recommendation distribution (auto_approve vs. worth_your_look
    vs. route_to_operator vs. auto_reject vs. disabled vs.
    insufficient_data over the window)

Explicitly does NOT expose individual blueprint rows — the summary
card is the audit surface; a drill-down endpoint is a follow-up.

## Fail-open

Table missing (migration not applied yet), Postgres down, or empty
result set → returns `{"data": null}` with a message. Never 500.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "ensemble_votes_api",
    __name__,
    url_prefix="/api/v1/ensemble-votes",
)

# Whitelist matching the niche registry — prevents SQL-string
# construction on operator-provided input from leaking beyond the
# expected shape.
_VALID_NICHES = frozenset({"gaming", "movies", "sports", "anime", "ai_creators"})

# Clamp window_days to a reasonable range. 1..90 matches the
# calibration-stats endpoint's window contract for consistency.
_MIN_WINDOW = 1
_MAX_WINDOW = 90
_DEFAULT_WINDOW = 7


def _flag_enabled() -> bool:
    """Whether the write path is currently persisting new rows.

    Independent of read access — the endpoint always tries to read
    even when writes are off (there may be historical data to
    inspect). Surfacing the flag lets the frontend badge the card
    as "read-only historical" vs "actively collecting".
    """
    return os.environ.get("GENLAB_ENSEMBLE_PERSIST_ENABLED", "0").strip() == "1"


def _parse_niche(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    if normalized not in _VALID_NICHES:
        return None
    return normalized


def _parse_window(raw: str | None) -> int:
    try:
        n = int(raw) if raw is not None else _DEFAULT_WINDOW
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW
    return max(_MIN_WINDOW, min(_MAX_WINDOW, n))


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Return the per-component + per-recommendation summary.

    Query params:
      * ``niche_id`` — one of the 5 valid niches. Optional; when
        absent, aggregates across all niches.
      * ``window_days`` — 1..90, default 7.

    Response::

        {
          "status": "success",
          "data": {
            "flag_enabled": true,
            "window_days": 7,
            "niche_id": "gaming" | null,
            "total_decisions": 143,
            "components": [
              {
                "component": "bandit",
                "voted": 130,        // rows with non-NULL score
                "abstained": 13,
                "vote_rate": 0.91,
                "mean_score": 0.52,
                "mean_disagreement_when_voting": 0.024
              },
              ...
            ],
            "recommendations": {
              "auto_approve": 45,
              "worth_your_look": 32,
              "route_to_operator": 40,
              "auto_reject": 20,
              "disabled": 4,
              "insufficient_data": 2
            }
          }
        }

    Never 500s. When the table is missing (migration not applied)
    the endpoint returns ``data: null`` with a specific message so
    the frontend can distinguish "cold start" from "system down".
    """
    niche = _parse_niche(request.args.get("niche_id"))
    window = _parse_window(request.args.get("window_days"))
    flag = _flag_enabled()

    # Build the SQL. All values are bound via psycopg parameters —
    # niche is already allowlisted above so no interpolation risk,
    # but staying with parameters is defense in depth.
    where_parts = ["decided_at > NOW() - (%s || ' days')::interval"]
    params: list[Any] = [str(window)]
    if niche is not None:
        where_parts.append("niche_id = %s")
        params.append(niche)
    where_sql = " AND ".join(where_parts)

    component_sql = f"""
        SELECT
            component,
            COUNT(*) FILTER (WHERE component_score IS NOT NULL) AS voted,
            COUNT(*) FILTER (WHERE component_score IS NULL) AS abstained,
            AVG(component_score) FILTER (WHERE component_score IS NOT NULL)
                AS mean_score,
            AVG(ensemble_disagreement) FILTER (WHERE component_score IS NOT NULL)
                AS mean_disagreement_when_voting
        FROM ensemble_votes
        WHERE {where_sql}
        GROUP BY component
        ORDER BY component
    """
    # Distinct decisions and per-recommendation distribution.
    # blueprint_id can repeat across time; the decided_at bucket
    # separates them. Use decided_at + blueprint_id for uniqueness.
    total_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT blueprint_id, decided_at
            FROM ensemble_votes
            WHERE {where_sql}
        ) t
    """
    reco_sql = f"""
        SELECT ensemble_recommendation, COUNT(DISTINCT (blueprint_id, decided_at))
        FROM ensemble_votes
        WHERE {where_sql}
        GROUP BY ensemble_recommendation
    """

    try:
        # Lazy import — dashboard cold-boot should NOT pull psycopg
        # into memory unless the endpoint is actually hit.
        from genlab_core.storage import get_pool
    except Exception as exc:
        logger.warning(
            "ensemble_votes.storage_import_failed",
            extra={"error": str(exc)},
        )
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": "storage module unavailable",
            }
        )

    try:
        pool = get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(component_sql, params)
                comp_rows = cur.fetchall()
                cur.execute(total_sql, params)
                total_row = cur.fetchone()
                cur.execute(reco_sql, params)
                reco_rows = cur.fetchall()
    except Exception as exc:
        # `relation "ensemble_votes" does not exist` (migration not
        # applied yet) also lands here — that's fine, we degrade
        # to the same cold-start message.
        logger.warning(
            "ensemble_votes.query_failed",
            extra={"error": str(exc)},
        )
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": ("ensemble_votes table not yet available (migration pending?)"),
            }
        )

    components: list[dict[str, Any]] = []
    for row in comp_rows or []:
        # psycopg returns tuple-shaped rows unless a row_factory is
        # configured. Handle both defensively.
        if isinstance(row, dict):
            component = row["component"]
            voted = row["voted"]
            abstained = row["abstained"]
            mean_score = row["mean_score"]
            mean_disagreement = row["mean_disagreement_when_voting"]
        else:
            component, voted, abstained, mean_score, mean_disagreement = row

        total = (voted or 0) + (abstained or 0)
        vote_rate = float(voted) / total if total > 0 else 0.0
        components.append(
            {
                "component": str(component),
                "voted": int(voted or 0),
                "abstained": int(abstained or 0),
                "vote_rate": vote_rate,
                "mean_score": (float(mean_score) if mean_score is not None else None),
                "mean_disagreement_when_voting": (
                    float(mean_disagreement) if mean_disagreement is not None else None
                ),
            }
        )

    if total_row is None:
        total_decisions = 0
    elif isinstance(total_row, dict):
        total_decisions = int(list(total_row.values())[0] or 0)
    else:
        total_decisions = int(total_row[0] or 0)

    recommendations: dict[str, int] = {}
    for row in reco_rows or []:
        if isinstance(row, dict):
            reco_name = list(row.values())[0]
            reco_count = list(row.values())[1]
        else:
            reco_name, reco_count = row
        recommendations[str(reco_name)] = int(reco_count or 0)

    return jsonify(
        {
            "status": "success",
            "data": {
                "flag_enabled": flag,
                "window_days": window,
                "niche_id": niche,
                "total_decisions": total_decisions,
                "components": components,
                "recommendations": recommendations,
            },
        }
    )
