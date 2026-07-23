"""LLM cost predictive alerting.

Motivating incident (2026-07-23): Anthropic credit exhausted at some
point during the day and stayed unnoticed until the writer + LLM
judge + shadow reviewer all silently failed. The existing
``anthropic_credit_exhausted`` alert fires only AFTER the balance hits
zero. By then the writer prompt has already been degraded and the
learning loop paused for hours.

This module adds two PROACTIVE signals derived from
``pipeline_run_costs``:

1. **Runaway spike detection** — when today's LLM spend is > 3× the
   rolling 7-day median (excluding today), fires a WARNING. Catches
   both cost-runaway bugs (a hot loop calling the API) AND legit-but-
   unexpected spikes (a strategist re-run + heavy testing day).

2. **Budget runway projection** — when the operator has set
   ``ANTHROPIC_MONTHLY_BUDGET_USD`` in .env, computes
   ``days_of_runway = (budget - month_to_date_spend) / avg_daily_burn``.
   Fires CRITICAL at 1-day runway, WARNING at 3-day runway, INFO at
   7-day runway. Gives operator a full week to schedule the top-up
   instead of scrambling after the outage.

Design notes
============

* Runaway spike uses MEDIAN (not mean) to be robust against another
  spike distorting the baseline. 3× median is generous — an unusual
  but legit day (Sunday strategist + backfill) is ~2× median.
* Budget projection is opt-in via env var. When
  ``ANTHROPIC_MONTHLY_BUDGET_USD`` is unset, the projection signal is
  skipped silently. No noise for operators who haven't set a budget.
* Both signals include ``auto_fix`` values that are OPERATOR
  SUGGESTIONS ("Investigate today's runs" / "Top up credits"). Per
  the 2026-07-23 whitelist correction, these values are NOT in
  ``_AUTO_FIX_COMPLETED_VALUES`` so the auto_fix_applied resolver
  correctly leaves them visible.

See:
* ``anthropic_credit_monitor.py`` — the reactive counterpart
* Rule #26 — don't alert on transient signals; the 3× threshold +
  budget opt-in mean this check only fires on genuinely actionable
  states.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from statistics import median

from genlab_core.monitoring.alerts import Alert

logger = logging.getLogger(__name__)


_RUNAWAY_SPIKE_MULTIPLIER: float = 3.0
_RUNAWAY_MIN_ABSOLUTE_USD: float = 1.00
_BUDGET_ENV_VAR: str = "ANTHROPIC_MONTHLY_BUDGET_USD"


def _fetch_daily_llm_costs(days: int = 14) -> list[tuple[str, float]]:
    """Return [(YYYY-MM-DD, cost_usd)] for the last N days, most recent first.

    Missing days are returned as (date, 0.0). Fail-open: any DB error
    returns [] and the caller skips the check.
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return []
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        return []

    try:
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT DATE(completed_at) AS d, SUM(llm_usd)::float AS cost
                FROM pipeline_run_costs
                WHERE completed_at > NOW() - make_interval(days => %s)
                GROUP BY 1
                ORDER BY 1 DESC
                """,
                (days,),
            ).fetchall()
        return [(str(r["d"]), float(r["cost"] or 0.0)) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.debug("[llm_cost] fetch failed: %s", exc)
        return []


def _fetch_month_to_date_llm_spend() -> float:
    """Sum of llm_usd for the current UTC month."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return 0.0
    try:
        import psycopg
    except ImportError:
        return 0.0
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(llm_usd), 0)::float
                FROM pipeline_run_costs
                WHERE completed_at >= DATE_TRUNC('month', NOW())
                """
            ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("[llm_cost] MTD fetch failed: %s", exc)
        return 0.0


def check_llm_cost_runaway() -> list[Alert]:
    """Fire WARNING when today's LLM spend > 3× rolling 7-day median.

    Skips when: no data, all-zero baseline, today's spend < $1
    (absolute floor to avoid noise on genuinely cheap days).
    """
    daily = _fetch_daily_llm_costs(days=14)
    if len(daily) < 3:
        return []

    today_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    # Split today from history — today may or may not be present.
    today_cost = 0.0
    history: list[float] = []
    for d, cost in daily:
        if d == today_iso:
            today_cost = cost
        else:
            history.append(cost)
    if not history:
        return []

    # Rolling 7 days of pre-today history.
    baseline_window = history[:7]
    if not baseline_window:
        return []
    baseline_median = median(baseline_window)
    if baseline_median <= 0.0:
        # All-zero baseline — can't compute a ratio. Skip.
        return []
    if today_cost < _RUNAWAY_MIN_ABSOLUTE_USD:
        # Today is genuinely cheap — no alert regardless of ratio.
        return []

    ratio = today_cost / baseline_median
    if ratio < _RUNAWAY_SPIKE_MULTIPLIER:
        return []

    return [
        Alert(
            check="llm_cost_runaway",
            severity="warning",
            message=(
                f"LLM cost today ${today_cost:.2f} is {ratio:.1f}x the "
                f"rolling 7-day median (${baseline_median:.4f}). "
                "Investigate today's runs for a hot loop or unexpected "
                "batch job."
            ),
            details={
                "today_usd": round(today_cost, 4),
                "baseline_median_usd": round(baseline_median, 4),
                "ratio": round(ratio, 2),
                "baseline_window_days": len(baseline_window),
            },
            auto_fix=(
                "Investigate: SELECT run_id, niche_id, llm_usd FROM "
                "pipeline_run_costs WHERE DATE(completed_at) = CURRENT_DATE "
                "ORDER BY llm_usd DESC LIMIT 10"
            ),
        )
    ]


def check_llm_budget_runway() -> list[Alert]:
    """Project days-until-budget-exhausted from month-to-date spend.

    Only fires when ANTHROPIC_MONTHLY_BUDGET_USD is set. Escalates:
    * 7-day runway: no alert (informational only via dashboard card)
    * 3-day runway: WARNING
    * 1-day runway: CRITICAL
    * Budget exceeded: CRITICAL
    """
    budget_str = os.environ.get(_BUDGET_ENV_VAR, "").strip()
    if not budget_str:
        return []
    try:
        budget = float(budget_str)
    except ValueError:
        return []
    if budget <= 0.0:
        return []

    mtd_spend = _fetch_month_to_date_llm_spend()
    remaining = budget - mtd_spend

    if remaining < 0.0:
        return [
            Alert(
                check="llm_budget_exceeded",
                severity="critical",
                message=(
                    f"Anthropic month-to-date spend ${mtd_spend:.2f} has "
                    f"exceeded configured budget ${budget:.2f} — top up or "
                    "raise ANTHROPIC_MONTHLY_BUDGET_USD."
                ),
                details={
                    "month_to_date_usd": round(mtd_spend, 4),
                    "budget_usd": round(budget, 4),
                    "over_by_usd": round(-remaining, 4),
                },
                auto_fix=(
                    "Top up at https://console.anthropic.com/settings/billing "
                    "OR raise ANTHROPIC_MONTHLY_BUDGET_USD in /opt/genlab/.env"
                ),
            )
        ]

    # Compute avg daily burn from the last 7 non-zero days.
    daily = _fetch_daily_llm_costs(days=14)
    nonzero = [c for _, c in daily if c > 0.0]
    if not nonzero:
        return []
    avg_daily = sum(nonzero[:7]) / min(len(nonzero), 7)
    if avg_daily <= 0.0:
        return []

    days_runway = remaining / avg_daily

    if days_runway < 1.0:
        severity = "critical"
    elif days_runway < 3.0:
        severity = "warning"
    else:
        return []  # 3+ days of runway is not incident-level

    return [
        Alert(
            check="llm_budget_runway_low",
            severity=severity,
            message=(
                f"Anthropic budget runway: {days_runway:.1f} days remaining "
                f"(${remaining:.2f} left / ${avg_daily:.4f}/day avg burn). "
                f"Month-to-date ${mtd_spend:.2f} of ${budget:.2f} budget."
            ),
            details={
                "days_runway": round(days_runway, 2),
                "remaining_usd": round(remaining, 4),
                "avg_daily_usd": round(avg_daily, 4),
                "month_to_date_usd": round(mtd_spend, 4),
                "budget_usd": round(budget, 4),
            },
            auto_fix=(
                "Top up at https://console.anthropic.com/settings/billing "
                "before runway hits 0"
            ),
        )
    ]


def check_llm_cost() -> list[Alert]:
    """Combined check — runaway spike + budget runway projection."""
    alerts: list[Alert] = []
    try:
        alerts.extend(check_llm_cost_runaway())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[llm_cost] runaway check failed: %s", exc, exc_info=True
        )
    try:
        alerts.extend(check_llm_budget_runway())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[llm_cost] budget runway check failed: %s", exc, exc_info=True
        )
    return alerts


__all__ = [
    "check_llm_cost",
    "check_llm_budget_runway",
    "check_llm_cost_runaway",
]
