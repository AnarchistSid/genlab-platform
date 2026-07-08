"""Drift signal summary endpoint (L7, 2026-07-08 audit).

Read-only observability over the `drift_signals` table populated by
`genlab_core.learning.drift_persist`. Surfaces:

  * Recent regressions (negative z-score, most concerning) sorted
    by |z_score| descending
  * Recent improvements (positive z-score, informational) — smaller
    list, capped
  * Per-niche counts so the operator can see "gaming has 3 arms
    regressing" at a glance

Sibling endpoint to `ensemble_votes.py` (L5, PR #728) — same
fail-open + flag-badge pattern.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "drift_signals_api",
    __name__,
    url_prefix="/api/v1/drift-signals",
)

_VALID_NICHES = frozenset(
    {"gaming", "movies", "sports", "anime", "ai_creators"}
)

_MIN_WINDOW = 1
_MAX_WINDOW = 90
_DEFAULT_WINDOW = 7
# Cap the returned lists — the endpoint is polled every 60s and
# some detector runs surface hundreds of signals; the card only
# needs top-N for at-a-glance triage.
_MAX_REGRESSIONS = 20
_MAX_IMPROVEMENTS = 10
# Below this |z|, the module considers the signal not-worth-alerting.
# Mirrors `drift_detector._DEFAULT_THRESHOLD_Z=2.0` but exposed as a
# constant so a future tighter alert threshold doesn't need code
# review across both files.
_DEFAULT_MIN_ABS_Z = 2.0


def _flag_enabled() -> bool:
    """Whether the write path is currently persisting new rows.

    Independent of read access — historical rows may exist even
    when the flag is off. Surfaces to the frontend so the card can
    badge "collecting" vs "historical-only".
    """
    return (
        os.environ.get("GENLAB_DRIFT_PERSIST_ENABLED", "0").strip() == "1"
    )


def _parse_niche(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized or normalized not in _VALID_NICHES:
        return None
    return normalized


def _parse_window(raw: str | None) -> int:
    try:
        n = int(raw) if raw is not None else _DEFAULT_WINDOW
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW
    return max(_MIN_WINDOW, min(_MAX_WINDOW, n))


def _parse_min_abs_z(raw: str | None) -> float:
    """Clamp to a sensible range. 0.5 lower bound so operator can
    audit near-threshold cases; 10.0 upper bound so no one accidentally
    filters out every signal."""
    try:
        z = float(raw) if raw is not None else _DEFAULT_MIN_ABS_Z
    except (TypeError, ValueError):
        return _DEFAULT_MIN_ABS_Z
    return max(0.5, min(10.0, z))


def _row_to_signal(row: Any) -> dict[str, Any]:
    """Convert a tuple/dict row into the JSON shape."""
    if isinstance(row, dict):
        return {
            "arm_id": str(row["arm_id"]),
            "niche_id": str(row["niche_id"]),
            "recent_rate": float(row["recent_rate"]),
            "baseline_rate": float(row["baseline_rate"]),
            "z_score": float(row["z_score"]),
            "recent_obs": int(row["recent_obs"]),
            "baseline_obs": int(row["baseline_obs"]),
            "is_regression": bool(row["is_regression"]),
            "computed_at": (
                row["computed_at"].isoformat()
                if hasattr(row["computed_at"], "isoformat")
                else str(row["computed_at"])
            ),
        }
    return {
        "arm_id": str(row[0]),
        "niche_id": str(row[1]),
        "recent_rate": float(row[2]),
        "baseline_rate": float(row[3]),
        "z_score": float(row[4]),
        "recent_obs": int(row[5]),
        "baseline_obs": int(row[6]),
        "is_regression": bool(row[7]),
        "computed_at": (
            row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8])
        ),
    }


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Return recent drift signals grouped by regression vs improvement.

    Query params:
      * ``niche_id`` — one of the 5; optional. When absent, aggregates
        across all niches.
      * ``window_days`` — 1..90, default 7.
      * ``min_abs_z`` — 0.5..10.0, default 2.0. Signals with
        ``|z_score| < min_abs_z`` are excluded.

    Response::

        {
          "status": "success",
          "data": {
            "flag_enabled": bool,
            "window_days": 7,
            "niche_id": "gaming" | null,
            "min_abs_z": 2.0,
            "regressions": [ <=20 signals, sorted z ASC ],
            "improvements": [ <=10 signals, sorted z DESC ],
            "counts_by_niche": [
              {"niche_id": "gaming", "regressions": 3, "improvements": 1}
            ]
          }
        }

    Never 500s. Table missing → data=null + migration-pending message.
    """
    niche = _parse_niche(request.args.get("niche_id"))
    window = _parse_window(request.args.get("window_days"))
    min_abs_z = _parse_min_abs_z(request.args.get("min_abs_z"))
    flag = _flag_enabled()

    # Parameterized SQL — niche is allowlisted above; window + z are
    # numeric. Staying with parameters is defense in depth.
    where_parts = [
        "computed_at > NOW() - (%s || ' days')::interval",
        "abs(z_score) >= %s",
    ]
    params: list[Any] = [str(window), min_abs_z]
    if niche is not None:
        where_parts.append("niche_id = %s")
        params.append(niche)
    where_sql = " AND ".join(where_parts)

    regressions_sql = f"""
        SELECT
            arm_id, niche_id, recent_rate, baseline_rate,
            z_score, recent_obs, baseline_obs,
            is_regression, computed_at
        FROM drift_signals
        WHERE {where_sql} AND is_regression = true
        ORDER BY z_score ASC
        LIMIT %s
    """
    improvements_sql = f"""
        SELECT
            arm_id, niche_id, recent_rate, baseline_rate,
            z_score, recent_obs, baseline_obs,
            is_regression, computed_at
        FROM drift_signals
        WHERE {where_sql} AND is_regression = false
        ORDER BY z_score DESC
        LIMIT %s
    """
    counts_sql = f"""
        SELECT
            niche_id,
            SUM(CASE WHEN is_regression THEN 1 ELSE 0 END) AS regressions,
            SUM(CASE WHEN is_regression THEN 0 ELSE 1 END) AS improvements
        FROM drift_signals
        WHERE {where_sql}
        GROUP BY niche_id
        ORDER BY niche_id
    """

    try:
        from genlab_core.storage import get_pool
    except Exception as exc:
        logger.warning(
            "drift_signals.storage_import_failed",
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
                cur.execute(regressions_sql, [*params, _MAX_REGRESSIONS])
                regression_rows = cur.fetchall()
                cur.execute(improvements_sql, [*params, _MAX_IMPROVEMENTS])
                improvement_rows = cur.fetchall()
                cur.execute(counts_sql, params)
                count_rows = cur.fetchall()
    except Exception as exc:
        logger.warning(
            "drift_signals.query_failed",
            extra={"error": str(exc)},
        )
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": (
                    "drift_signals table not yet available "
                    "(migration pending?)"
                ),
            }
        )

    regressions = [_row_to_signal(r) for r in regression_rows or []]
    improvements = [_row_to_signal(r) for r in improvement_rows or []]

    counts_by_niche = []
    for row in count_rows or []:
        if isinstance(row, dict):
            counts_by_niche.append(
                {
                    "niche_id": str(row["niche_id"]),
                    "regressions": int(row["regressions"] or 0),
                    "improvements": int(row["improvements"] or 0),
                }
            )
        else:
            counts_by_niche.append(
                {
                    "niche_id": str(row[0]),
                    "regressions": int(row[1] or 0),
                    "improvements": int(row[2] or 0),
                }
            )

    return jsonify(
        {
            "status": "success",
            "data": {
                "flag_enabled": flag,
                "window_days": window,
                "niche_id": niche,
                "min_abs_z": min_abs_z,
                "regressions": regressions,
                "improvements": improvements,
                "counts_by_niche": counts_by_niche,
            },
        }
    )
