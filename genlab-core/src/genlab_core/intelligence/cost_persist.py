"""Persist CostAccumulator summaries to the pipeline_run_costs table.

Closes the visibility gap from the 2026-06-13 autonomy deep-dive: the
CostAccumulator tracks spend per pipeline run in contextvars and dumps
a summary to run_report.json, but **never persists to Postgres**. The
operator had to grep through 50 JSON files to answer "what did I spend
on gaming this week."

This module ships a single function ``persist_run_cost()`` that takes
the summary dict (the same shape CostAccumulator.summary() produces)
plus niche_id + run_id, and writes one row to ``pipeline_run_costs``
with per-category + per-model splits.

Design choices
==============

* **Idempotent UPSERT** — the unique index on (run_id, niche_id) means
  re-running RunReport (e.g. a retry pass) updates the row instead of
  inserting a duplicate. Operator metrics stay accurate.
* **Best-effort fail-open** — DB errors log at WARNING and return
  False. A persistence failure must NEVER block run_report.json from
  being written or the cron from completing.
* **Per-model breakdown via JSONB** — `by_model` is a {model: usd} dict
  so dashboard queries can answer "which model is the most expensive
  across all runs" without re-reading the JSON files.
* **Direct psycopg, no pool** — writes are 1/run/niche = max ~5/day
  per niche. Pool overhead would exceed its value at this volume.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def persist_run_cost(
    *,
    run_id: str,
    niche_id: str,
    summary: dict[str, Any],
    by_model: dict[str, float] | None = None,
    budget_usd: float | None = None,
) -> bool:
    """Write one cost row to pipeline_run_costs. Best-effort, never raises.

    Parameters
    ----------
    run_id:
        Pipeline run identifier (matches run_report.json.run_id).
    niche_id:
        Niche this run belonged to (matches run_report.json.niche_id).
    summary:
        Output of ``CostAccumulator.summary()`` — must carry at least
        ``total_usd`` and ``by_category``. Other keys are ignored.
    by_model:
        Optional per-model spend dict. None ⇒ empty JSONB.
    budget_usd:
        Optional per-run budget for budget-overrun analysis.

    Returns
    -------
    bool
        True if the row was written, False on any failure (missing
        DATABASE_URL, DB error, malformed summary). Caller does not
        need to handle failures — the run report is the source of
        truth, this table is an aggregated convenience.
    """
    if not run_id or not niche_id:
        logger.warning(
            "[cost_persist] skipping write — empty run_id=%r or niche_id=%r",
            run_id,
            niche_id,
        )
        return False
    if not isinstance(summary, dict):
        logger.warning("[cost_persist] skipping write — summary is not a dict")
        return False

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.debug("[cost_persist] DATABASE_URL unset, skipping persistence")
        return False

    try:
        import psycopg
    except ImportError:
        logger.warning("[cost_persist] psycopg not installed, skipping write")
        return False

    by_category = summary.get("by_category") or {}
    if not isinstance(by_category, dict):
        by_category = {}

    total_usd = float(summary.get("total_usd", 0.0) or 0.0)
    entry_count = int(summary.get("entry_count", 0) or 0)
    budget_remaining_pct = summary.get("budget_remaining_pct")

    # by_model fallback — caller might pass it in explicitly or we
    # synthesize from summary if it carries one.
    if by_model is None:
        by_model = summary.get("by_model") or {}
    if not isinstance(by_model, dict):
        by_model = {}

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pipeline_run_costs
                        (run_id, niche_id, total_usd,
                         llm_usd, image_usd, tts_usd,
                         compute_usd, media_usd, bandwidth_usd,
                         by_model, budget_usd, budget_remaining_pct,
                         entry_count)
                    VALUES (%s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s::jsonb, %s, %s,
                            %s)
                    ON CONFLICT (run_id, niche_id) DO UPDATE SET
                        completed_at = EXCLUDED.completed_at,
                        total_usd = EXCLUDED.total_usd,
                        llm_usd = EXCLUDED.llm_usd,
                        image_usd = EXCLUDED.image_usd,
                        tts_usd = EXCLUDED.tts_usd,
                        compute_usd = EXCLUDED.compute_usd,
                        media_usd = EXCLUDED.media_usd,
                        bandwidth_usd = EXCLUDED.bandwidth_usd,
                        by_model = EXCLUDED.by_model,
                        budget_usd = EXCLUDED.budget_usd,
                        budget_remaining_pct = EXCLUDED.budget_remaining_pct,
                        entry_count = EXCLUDED.entry_count
                    """,
                    (
                        str(run_id),
                        str(niche_id),
                        total_usd,
                        float(by_category.get("llm", 0.0) or 0.0),
                        float(by_category.get("image", 0.0) or 0.0),
                        float(by_category.get("tts", 0.0) or 0.0),
                        float(by_category.get("compute", 0.0) or 0.0),
                        float(by_category.get("media", 0.0) or 0.0),
                        float(by_category.get("bandwidth", 0.0) or 0.0),
                        json.dumps(by_model),
                        float(budget_usd) if budget_usd is not None else None,
                        float(budget_remaining_pct) if budget_remaining_pct is not None else None,
                        entry_count,
                    ),
                )
            conn.commit()
        logger.info(
            "[cost_persist] persisted run_id=%s niche=%s total_usd=%.4f",
            run_id,
            niche_id,
            total_usd,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — NEVER raise to caller
        logger.warning(
            "[cost_persist] write failed for run_id=%s niche=%s: %s",
            run_id,
            niche_id,
            exc,
        )
        return False
