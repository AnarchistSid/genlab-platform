"""Sponsorship-readiness API endpoint.

Routes:
    GET /api/v1/sponsorship/readiness  -- per-niche sponsorship-readiness tiers

## Why this endpoint

Existing ``/api/v1/monetisation/progress`` returns the raw per-niche /
per-platform / per-metric grid. Useful for detail views, awkward for
at-a-glance "which channels can I pitch sponsors on this week?"
The Mission Control card needs an actionable summary: a TIER per
niche, derived from the same underlying data.

Tier semantics (used by ``SponsorshipReadinessCard``):

  eligible_now      → at least one platform on this niche has every
                      monetisation metric met (formal monetisation
                      threshold crossed; sponsors will respect the
                      official audience number)
  within_2_months   → nearest unmet metric has ``days_to_threshold_est``
                      ≤ 60 with positive 7-day velocity
  within_6_months   → nearest unmet metric has ``days_to_threshold_est``
                      ≤ 180 with positive 7-day velocity
  tracking          → far from threshold, flat velocity, or no ETA
                      data yet (cold start)

## Architecture note — fail-safe to the existing reader

This endpoint deliberately reuses ``server.core.monetisation_progress_pg.fetch_progress``
so any future schema or RLS change to ``monetisationprogress`` is
inherited automatically. The tier computation lives purely in-process
— no new tables, no new migrations.

Audit-friendly: when the Postgres table is empty (cold start, fresh
prod box, or DATABASE_URL unset), each niche cleanly returns the
``tracking`` tier with empty per-platform data. Never 500s on missing
data; the card renders "tracking · 0/4 platforms reporting".
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

from flask import Blueprint, request

from server.core.monetisation_progress_pg import fetch_progress as _pg_fetch_progress
from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("sponsorship_api", __name__, url_prefix="/api/v1/sponsorship")

# 60-second cache. The card polls every 60s; this collapses any
# concurrent dashboard tabs into a single DB round-trip per minute.
_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 60.0

# Tier thresholds — defined here, not in YAML, because they are
# operator-facing labels rather than monetisation constants. A future
# audit might re-tune these if "within_2_months" turns out too
# permissive in practice.
_TIER_2MO_DAYS = 60
_TIER_6MO_DAYS = 180


def _fetch_progress_cached() -> list[dict]:
    entry = _cache.get("data")
    now = _time.time()
    if entry is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return entry
    rows = _pg_fetch_progress()
    _cache["data"] = rows
    _cache["ts"] = now
    return rows


def _compute_platform_summary(metrics: list[dict]) -> dict[str, Any]:
    """Per-platform summary: pick the most-informative single metric +
    flag whether ALL metrics on this platform are met (formal
    "monetised" status).

    "Most informative" = the unmet metric with the highest
    ``pct_complete`` (i.e., the closest miss). When ALL are met,
    surface any one — the platform is monetised and the operator
    just needs to see that fact.
    """
    if not metrics:
        return {
            "is_monetised": False,
            "primary_metric": None,
            "metric_count": 0,
        }

    # A platform is monetised when every metric with a non-null target
    # is_threshold_met. Mirrors the existing ``monetisation.progress``
    # endpoint's logic (lines 102-108 there).
    metrics_with_targets = [m for m in metrics if m.get("target_value") is not None]
    met_count = sum(1 for m in metrics_with_targets if m.get("is_threshold_met"))
    is_monetised = len(metrics_with_targets) > 0 and met_count == len(metrics_with_targets)

    # Primary metric — closest unmet (highest pct_complete). When all
    # are met, surface the one with the highest pct_complete (i.e., the
    # most over-the-line — operator-friendly "X% of target" headline).
    unmet = [m for m in metrics if not m.get("is_threshold_met")]
    pool = unmet if unmet else list(metrics)
    pool_with_pct = [m for m in pool if m.get("pct_complete") is not None]
    if pool_with_pct:
        primary = max(pool_with_pct, key=lambda m: float(m.get("pct_complete") or 0.0))
    else:
        primary = pool[0] if pool else None

    primary_summary = None
    if primary is not None:
        primary_summary = {
            "metric_name": primary.get("metric_name", ""),
            "pct_complete": primary.get("pct_complete"),
            "current_value": primary.get("current_value"),
            "target_value": primary.get("target_value"),
            "days_to_threshold_est": primary.get("days_to_threshold_est"),
            "delta_7d": primary.get("delta_7d"),
            "is_threshold_met": bool(primary.get("is_threshold_met", False)),
        }

    return {
        "is_monetised": is_monetised,
        "primary_metric": primary_summary,
        "metric_count": len(metrics),
    }


def _compute_tier(
    platforms_summary: dict[str, dict[str, Any]],
    all_metrics: list[dict],
) -> tuple[str, int | None]:
    """Decide the niche-level tier label + the nearest-threshold ETA.

    Returns (tier, nearest_threshold_days). nearest_threshold_days
    can be None when there's no ETA data on any unmet metric yet
    (cold-start niche or all metrics already met).
    """
    # eligible_now wins immediately if ANY platform has every metric met.
    if any(p["is_monetised"] for p in platforms_summary.values()):
        return ("eligible_now", 0)

    # Otherwise look at the nearest-to-threshold unmet metric across
    # the entire niche. Require positive 7-day velocity — a metric
    # that's "within 60 days" only because the ETA was computed
    # against a momentary uptick that's already reversed shouldn't
    # qualify as within_2_months.
    candidates = [
        m
        for m in all_metrics
        if not m.get("is_threshold_met")
        and m.get("days_to_threshold_est") is not None
        and (m.get("delta_7d") or 0) > 0
    ]
    if not candidates:
        # No projectable path — cold-start, no velocity yet, or
        # regressing. Default to ``tracking``.
        return ("tracking", None)

    nearest = min(candidates, key=lambda m: int(m.get("days_to_threshold_est") or 10**6))
    days = int(nearest.get("days_to_threshold_est") or 10**6)
    if days <= _TIER_2MO_DAYS:
        return ("within_2_months", days)
    if days <= _TIER_6MO_DAYS:
        return ("within_6_months", days)
    return ("tracking", days)


@bp.route("/readiness")
def sponsorship_readiness():
    """Return per-niche sponsorship-readiness summary.

    Response shape:

        {
          "<niche_id>": {
            "tier": "eligible_now" | "within_2_months" |
                    "within_6_months" | "tracking",
            "nearest_threshold_days": <int> | null,
            "platforms": {
              "<platform>": {
                "is_monetised": <bool>,
                "primary_metric": {
                   "metric_name": str,
                   "pct_complete": float,
                   "current_value": float,
                   "target_value": float,
                   "days_to_threshold_est": int | null,
                   "delta_7d": float | null,
                   "is_threshold_met": bool
                } | null,
                "metric_count": int
              }
            }
          },
          ...
        }

    Niches with zero rows in ``monetisationprogress`` (cold start) are
    NOT included in the response — the card's per-niche-row rendering
    falls back to a "no data yet" stub when the niche key is missing.
    Including them with empty platform data would create misleading
    `tracking` tiers from `compute_tier`'s default branch.
    """
    try:
        records = _fetch_progress_cached()
    except Exception as exc:
        logger.exception("[sponsorship_readiness] fetch_progress failed")
        return api_error(error=str(exc), code=500)

    # Group raw rows by (niche, platform)
    grouped: dict[str, dict[str, list[dict]]] = {}
    for raw in records:
        rec = raw.get("fields", raw)
        niche = rec.get("niche_id") or "unknown"
        platform = rec.get("platform") or "unknown"
        if niche == "unknown":
            continue
        grouped.setdefault(niche, {}).setdefault(platform, []).append(rec)

    # Compute per-niche tier + per-platform summaries
    out: dict[str, Any] = {}
    for niche, platforms in grouped.items():
        platforms_summary = {
            plat: _compute_platform_summary(metrics) for plat, metrics in platforms.items()
        }
        all_metrics: list[dict] = [m for plat_metrics in platforms.values() for m in plat_metrics]
        tier, nearest = _compute_tier(platforms_summary, all_metrics)
        out[niche] = {
            "tier": tier,
            "nearest_threshold_days": nearest,
            "platforms": platforms_summary,
        }

    # PR II (2026-06-23): tier-transition detection happens here as a
    # write-side side-effect — when the readiness endpoint runs (every
    # 60s from the dashboard), compute current tier per niche and
    # compare to the last-known tier from tier_history. New transitions
    # get appended; existing tiers are no-ops. Fully fail-OPEN: any
    # error in detection doesn't change the readiness response.
    #
    # The READ side is a separate endpoint
    # (``/api/v1/sponsorship/recent-transitions``) so this response's
    # back-compat shape is preserved. Frontend SponsorshipReadinessCard
    # gets its tier badges from this endpoint and its "recent activity"
    # surface from the sibling endpoint — separation of concerns.
    try:
        from server.core.tier_transition_detector import record_tier_transitions

        record_tier_transitions(
            {
                nid: {
                    "tier": data["tier"],
                    "nearest_threshold_days": data["nearest_threshold_days"],
                }
                for nid, data in out.items()
            }
        )
    except Exception as exc:
        logger.debug("[sponsorship_readiness] transition detection failed: %s", exc)

    return api_success(data=out)


@bp.route("/recent-transitions")
def recent_transitions():
    """Return recent tier transitions for the dashboard's surface.

    Query params:
        window_hours: int, default 24

    Response::

        {
          "window_hours": 24,
          "transitions": [
            {
              "niche_id": "ai_creators",
              "tier": "eligible_now",
              "prev_tier": "within_2_months",
              "nearest_threshold_days": 0,
              "observed_at": "2026-06-23T10:42:00+00:00"
            },
            ...  // ordered DESC by observed_at, max 100
          ]
        }

    Always returns 200 — empty transitions list when no recent
    activity OR when the underlying read fails (cold-start / DSN
    unset / pg error). Read failures debug-log; never surface as
    a 500 because this endpoint is a passive dashboard surface,
    not a critical write path.
    """
    try:
        window_hours = int(request.args.get("window_hours") or "24")
    except (TypeError, ValueError):
        window_hours = 24
    # Clamp to a sensible range
    window_hours = max(1, min(window_hours, 168))  # 1 hour .. 7 days

    try:
        from server.core.tier_transition_detector import fetch_recent_transitions

        transitions = fetch_recent_transitions(window_hours=window_hours)
    except Exception as exc:
        logger.debug("[recent_transitions] read failed: %s", exc)
        transitions = []

    return api_success(
        data={
            "window_hours": window_hours,
            "transitions": transitions,
        }
    )
