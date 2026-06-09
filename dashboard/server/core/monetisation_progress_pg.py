"""Postgres reader for monetisation progress.

Repoints the dashboard's ``/api/v1/monetisation/progress`` endpoint at
the live Postgres ``monetisationprogress`` table (written daily by
``genlab-audience-collector.service``) instead of the SharePoint
``GenLab_MonetisationProgress`` list (writer has no phase-2 timer →
stale + likely unscheduled).

Audit ref: R-38.

The returned dict shape matches what the existing SP path returned —
the downstream grouping logic in ``server.api.monetisation`` unwraps
either ``{"fields": {...}}`` (legacy SP) or a flat dict, so flat dicts
from Postgres flow through unchanged.

Schema reference (from ``genlab-core/migrations/.../adopt_handcreated_tables.py``):

    monetisationprogress
        niche_id            TEXT NOT NULL
        platform            TEXT
        metric_name         TEXT
        current_value       DOUBLE PRECISION
        target_value        DOUBLE PRECISION
        pct_complete        DOUBLE PRECISION
        delta_7d            DOUBLE PRECISION
        days_to_threshold_est INTEGER
        is_threshold_met    BOOLEAN
        data_source         TEXT
        as_of_date          TEXT
        error_log           TEXT
        updated_at          TIMESTAMPTZ
        UNIQUE (niche_id, platform, metric_name)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_COLUMNS = [
    "niche_id",
    "platform",
    "metric_name",
    "current_value",
    "target_value",
    "pct_complete",
    "delta_7d",
    "days_to_threshold_est",
    "is_threshold_met",
    "data_source",
    "as_of_date",
    "error_log",
]


def fetch_progress(
    *,
    dsn: str | None = None,
    niche_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read the live ``monetisationprogress`` rows.

    Args:
        dsn: Postgres connection string. Defaults to ``DATABASE_URL`` env
            var, then ``dbname=genlab`` (matches the package's other
            local-dev fallbacks).
        niche_id: optional filter; when set, only that niche's rows
            come back. Used by the per-niche dashboard page if it ever
            wants to scope the read.

    Returns:
        List of flat dicts with the columns above. Empty list on
        connection / query failure (the dashboard must never 500
        because the monetisation table is unavailable).
    """
    import psycopg
    from psycopg.rows import dict_row

    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    cols = ", ".join(_COLUMNS)
    query = f"SELECT {cols} FROM monetisationprogress"
    params: tuple[Any, ...] = ()
    if niche_id:
        query += " WHERE niche_id = %s"
        params = (niche_id,)
    query += " ORDER BY niche_id, platform, metric_name"

    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                if niche_id:
                    # RLS context for niche-scoped reads (same convention
                    # as revenue_tracker.get_revenue_summary).
                    cur.execute(
                        "SELECT set_config('app.niche_id', %s, true)",
                        (niche_id,),
                    )
                cur.execute(query, params)
                rows = cur.fetchall()
        # psycopg's dict_row already returns plain dicts; just normalize
        # the data types for JSON-serialisation friendliness.
        return [_coerce_row(r) for r in rows]
    except Exception:
        logger.exception("[monetisation_progress_pg] failed to read monetisationprogress")
        return []


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce numeric/boolean column values for clean JSON output.

    Postgres ``DOUBLE PRECISION`` arrives as Python ``float`` already;
    we just round to a fixed precision so the dashboard isn't
    deceived by 16-digit IEEE artefacts (e.g. ``0.012700000000001``).
    """
    out: dict[str, Any] = dict(row)
    for k in ("current_value", "target_value", "pct_complete", "delta_7d"):
        v = out.get(k)
        if v is not None:
            try:
                out[k] = round(float(v), 6)
            except (TypeError, ValueError):
                # leave non-numeric values in place; the dashboard's
                # downstream code tolerates ``None``.
                pass
    # bool — Postgres returns Python bool already, but normalize Nones
    # so the dashboard's default-False fallback isn't deceived.
    if out.get("is_threshold_met") is None:
        out["is_threshold_met"] = False
    return out
