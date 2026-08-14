"""Learning system API endpoints for dashboard.

Routes:
    GET /api/v1/learning/bandit-state         -- bandit arm alpha/beta for all niches
    GET /api/v1/learning/hook-classifier-status -- which niches have trained hook classifiers
"""

import json
import logging
import time as _time
from pathlib import Path

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("learning_api", __name__, url_prefix="/api/v1/learning")

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "genlab-core" / "models"


def _linucb_obs(arm_record: dict) -> int:
    """Extract observation count from a bandit arm record.

    Prefers top-level n_plays, falls back to n_obs inside LinUCB_State JSON.
    """
    f = arm_record.get("fields", arm_record)
    n = int(f.get("n_plays", 0) or 0)
    if n > 0:
        return n
    # Fallback: read n_obs from persisted LinUCB state
    raw = f.get("linucb_state") or f.get("LinUCB_State") or ""
    if raw:
        try:
            state = json.loads(raw) if isinstance(raw, str) else raw
            return int(state.get("n_obs", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return 0


_HOOK_NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]

# Learning status cache (300s TTL — multiple DB aggregates are expensive
# and the data only changes meaningfully when a pipeline / metric_collector
# fires. 60s was tight enough to re-hit the DB on every dashboard nav.)
_status_cache: dict = {"data": None, "ts": 0.0}
_STATUS_TTL = 300.0


# Backend gate parameters used to compute the real config_update_threshold
# instead of the previous hardcoded 50 (which had no relation to the
# actual ConfigUpdater.run gate). Must stay in sync with
# genlab_core.learning.config_updater.MIN_DATA_POINTS.
_CONFIG_UPDATER_MIN_RECORDS_PER_NICHE = 20


def _learning_aggregates() -> dict:
    """Aggregate learning-loop state via direct SQL.

    Replaces the previous client.<table>.all(max_records=...) truncation
    pattern (PF capped at 500, analytics capped at 50, bandit arms at
    100) which silently underreported once any table grew past those
    caps. Uses one connection + four aggregates.
    """
    import os

    from psycopg.rows import dict_row

    out: dict = {
        "feedback_by_status": {},
        "rewards_count": 0,
        "avg_reward": None,
        "max_reward": None,
        "analytics_count": 0,
        "config_update_progress": 0,
        "config_update_threshold": _CONFIG_UPDATER_MIN_RECORDS_PER_NICHE * 5,
        "niches_at_config_quota": 0,
    }
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        return out

    try:
        with pg_connect(db_url, connect_timeout=5, row_factory=dict_row, niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT collection_status, COUNT(*) AS n
                    FROM pending_feedback
                    GROUP BY collection_status
                    """
                )
                out["feedback_by_status"] = {
                    r["collection_status"]: int(r["n"]) for r in cur.fetchall()
                }

                cur.execute(
                    """
                    SELECT COUNT(*) AS n,
                           AVG(reward_48h) AS avg_r,
                           MAX(reward_48h) AS max_r
                    FROM pending_feedback
                    WHERE reward_48h IS NOT NULL
                    """
                )
                row = cur.fetchone() or {}
                out["rewards_count"] = int(row.get("n") or 0)
                if row.get("avg_r") is not None:
                    out["avg_reward"] = round(float(row["avg_r"]), 4)
                if row.get("max_r") is not None:
                    out["max_reward"] = round(float(row["max_r"]), 4)

                cur.execute("SELECT COUNT(*) AS n FROM analytics")
                out["analytics_count"] = int((cur.fetchone() or {}).get("n") or 0)

                # Real config-update gate: niches with at least
                # MIN_DATA_POINTS reward-bearing PF rows in the last 30
                # days (matches ConfigUpdater.run's per-niche cutoff).
                cur.execute(
                    """
                    SELECT niche_id, COUNT(*) AS n
                    FROM pending_feedback
                    WHERE reward_48h IS NOT NULL
                      AND publish_time > NOW() - INTERVAL '30 days'
                    GROUP BY niche_id
                    """
                )
                niche_counts = {r["niche_id"]: int(r["n"]) for r in cur.fetchall()}
                out["niches_at_config_quota"] = sum(
                    1 for n in niche_counts.values() if n >= _CONFIG_UPDATER_MIN_RECORDS_PER_NICHE
                )
                # Progress = total reward-bearing PF rows in last 30 days
                # across all niches. The "threshold" UI compares against
                # is 5 niches * MIN_DATA_POINTS = 100 (i.e. every niche
                # has enough records to update independently).
                out["config_update_progress"] = sum(niche_counts.values())
    except Exception as exc:
        logger.warning("[learning] aggregates query failed: %s", exc)
    return out


@bp.route("/status")
def learning_status():
    """Comprehensive learning loop status — all mechanisms in one response."""
    now = _time.time()
    if _status_cache["data"] is not None and (now - _status_cache["ts"]) < _STATUS_TTL:
        return api_success(data=_status_cache["data"])

    try:
        from server.core.graph_sync import get_sync_client

        client = get_sync_client()

        # Bandit arms — cap raised from 100 to 500. With 5 niches and
        # ~12 arms each (content_type + 5 style:* + future expansion)
        # ~60 arms is realistic; 100 was uncomfortably close.
        arms = client.bandit_arms.all(max_records=500)
        arm_data: dict[str, list[dict]] = {}
        for a in arms:
            f = a.get("fields", a)
            niche = f.get("niche_id", "")
            alpha = float(f.get("alpha", 1) or 1)
            beta = float(f.get("beta", 1) or 1)
            arm_data.setdefault(niche, []).append(
                {
                    "arm_id": f.get("arm_id", ""),
                    "alpha": round(alpha, 3),
                    "beta": round(beta, 3),
                    "n_plays": _linucb_obs(a),
                    "mean": round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.0,
                }
            )

        # Everything else uses direct SQL aggregates so we don't
        # silently truncate when PF > 500 or analytics > 50 (the
        # previous caps both made the dashboard lie once data grew).
        agg = _learning_aggregates()

        # hook_classifier threshold mirrors MIN_EXAMPLES in
        # genlab_core.learning.hook_classifier (Sprint 68 lowered to 50).
        # Progress: the count actually usable for training (rows where
        # we have hook_text + reward_48h), surfaced separately so the
        # UI can distinguish "data exists" from "trained".
        result = {
            "bandit_arms": arm_data,
            "feedback_pipeline": agg["feedback_by_status"],
            "rewards_computed": agg["rewards_count"],
            "avg_reward": agg["avg_reward"],
            "max_reward": agg["max_reward"],
            "analytics_records": agg["analytics_count"],
            "hook_classifier_threshold": 50,
            "hook_classifier_progress": agg["rewards_count"],
            # Real backend gate: each niche needs 20 reward-bearing PF
            # rows in the last 30 days for ConfigUpdater.run to do
            # anything. Threshold == 5 niches * 20 = 100 (all niches
            # at quota). Progress is sum across niches.
            "config_update_threshold": agg["config_update_threshold"],
            "config_update_progress": agg["config_update_progress"],
            "niches_at_config_quota": agg["niches_at_config_quota"],
            "linucb_threshold": 50,
            "linucb_max_plays": max((_linucb_obs(a) for a in arms), default=0),
        }
        _status_cache["data"] = result
        _status_cache["ts"] = _time.time()
        return api_success(data=result)
    except Exception as e:
        logger.warning("learning_status failed: %s", e)
        return api_error(error=str(e), code=500)


@bp.route("/hook-classifier-status")
def hook_classifier_status():
    """Return which niches have trained hook classifiers."""
    model_dir = _MODEL_DIR
    status = {}
    for niche_id in _HOOK_NICHES:
        meta_path = model_dir / f"hook_classifier_{niche_id}.meta.json"
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                status[niche_id] = {
                    "trained": True,
                    "n_examples": meta.get("n_examples", 0),
                    "pos_rate": meta.get("pos_rate", 0),
                    "n_features": len(meta.get("feature_names", [])),
                }
            except Exception:
                status[niche_id] = {"trained": False}
        else:
            status[niche_id] = {"trained": False}

    return api_success(data=status)


@bp.route("/config-updates")
def config_updates():
    """Recent config-updates audit history.

    Reads the ``config_updates`` table populated by
    ``genlab_core.scripts.run_config_update`` and surfaces it for the
    Learning > Config Updates tab. Returns dry-run and applied rows
    separately so the UI can label preview rows.
    """
    import os

    from psycopg.rows import dict_row

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        return api_error(error="DATABASE_URL unset", code=500)

    try:
        with pg_connect(
            db_url,
            connect_timeout=5,
            row_factory=dict_row,
            niche_id=request.args.get("niche_id", "all") or "all",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, niche_id, file_path, field,
                           old_value, new_value, reason, n_records,
                           applied_at, dry_run
                    FROM config_updates
                    ORDER BY applied_at DESC
                    LIMIT 100
                    """
                )
                rows = cur.fetchall()
        # ISO-stringify timestamps for the JSON response.
        for r in rows:
            if r.get("applied_at") is not None:
                r["applied_at"] = r["applied_at"].isoformat()
        return api_success(data={"updates": rows})
    except Exception as exc:
        logger.warning("[learning] config_updates query failed: %s", exc)
        return api_error(error=str(exc), code=500)


@bp.route("/source-performance", methods=["GET"])
def source_performance():
    """PR #578 (2026-06-25): per-source Beta posteriors from the
    bandit_arms-via-arm_id-prefix convention (PR #571).

    Query params:
      niche_id — required; per-niche scope. Returns 400 when missing.
                 SR-F: restricted operator's explicit cross-tenant
                 niche → 403.

    Response shape:
      {data: [
        {niche_id, source, alpha, beta, n_plays, reward_mean}, ...
      ]}
      DESC by reward_mean (the per-source ordering the future
      'best/worst sources' dashboard card consumes directly).

    Empty list when no source-arm data exists yet (cold-start —
    PR #572's reward-loop wire populates this over 7-14 days as
    48h reward windows close).
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query parameter required")

    # SR-F: validate against operator allowlist
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id not in _allowed:
        return api_error(
            error=(
                f"Source performance scoped to niche '{niche_id}' which is "
                f"not in your allowlist (allowed: {sorted(_allowed)}). "
                f"See SR-F (PR #578)."
            ),
            code=403,
        )

    try:
        from genlab_core.learning.source_performance import list_source_performance

        records = list_source_performance(niche_id)
    except ImportError:
        # Graceful degrade for pre-PR-571 core
        return api_success(data=[])
    except Exception as exc:
        logger.error("[learning] source_performance list failed: %s", exc, exc_info=True)
        return api_error(error="Internal server error", code=500)

    data = [
        {
            "niche_id": r.niche_id,
            "source": r.source,
            "alpha": r.alpha,
            "beta": r.beta,
            "n_plays": r.n_plays,
            "reward_mean": r.reward_mean,
        }
        for r in records
    ]
    return api_success(data=data)


# 2026-08-14: Phase 0.C — reward signal audit endpoint.
#
# Surfaces the reward distribution per (niche, platform) so operator
# can tell at a glance whether the bandit is actually learning. The
# Goodhart-broken pre-Phase-0.A state showed rewards clustered near 0
# on YT/IG/Threads (avg 0.001-0.008); the Phase-0.A fix should
# gradually spread these distributions over the next 7 days as new
# reward_48h values land.
#
# Also computes a health verdict per niche:
#   * "healthy" — ≥3 platforms with stddev >= 0.05 (real signal spread)
#   * "partial" — 1-2 platforms healthy, others near-zero
#   * "broken" — 0 platforms with meaningful spread (Goodhart-mode)
#
# Staleness signal: max(created_at) per (niche, platform). If >48h
# stale, the platform's reward computation may be broken.


@bp.route("/reward-audit", methods=["GET"])
def get_reward_audit():
    """Return reward-signal health snapshot per niche × platform.

    Response shape:

        {
          "status": "ok",
          "data": [
            {
              "niche_id": "anime",
              "verdict": "healthy" | "partial" | "broken",
              "platforms": [
                {
                  "platform": "facebook",
                  "n_rewards_7d": 11,
                  "min": 0.006, "max": 0.804, "avg": 0.301,
                  "stddev": 0.271,
                  "p25": 0.071, "p50": 0.240, "p75": 0.436,
                  "hours_since_latest": 12.3,
                  "signal_status": "healthy" | "weak" | "stale" | "cold"
                },
                ...
              ]
            },
            ...
          ]
        }

    Query params: none (returns all 5 niches).
    """
    import os

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return api_error(error="DATABASE_URL unset", code=503)

    try:
        with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      niche_id,
                      platform,
                      COUNT(*) AS n_rewards_7d,
                      MIN(reward_48h)::float AS min_r,
                      MAX(reward_48h)::float AS max_r,
                      AVG(reward_48h)::float AS avg_r,
                      STDDEV(reward_48h)::float AS stddev_r,
                      percentile_cont(0.25) WITHIN GROUP (ORDER BY reward_48h)::float AS p25,
                      percentile_cont(0.50) WITHIN GROUP (ORDER BY reward_48h)::float AS p50,
                      percentile_cont(0.75) WITHIN GROUP (ORDER BY reward_48h)::float AS p75,
                      EXTRACT(EPOCH FROM (NOW() - MAX(created_at))) / 3600.0 AS hours_since_latest
                    FROM pending_feedback
                    WHERE reward_48h IS NOT NULL
                      AND created_at >= NOW() - INTERVAL '7 days'
                    GROUP BY niche_id, platform
                    ORDER BY niche_id, platform
                    """,
                )
                rows = cur.fetchall() or []
    except Exception as exc:
        logger.warning("[reward_audit] query failed: %s", exc, exc_info=True)
        return api_error(error="Reward audit query failed", code=500)

    # Group by niche + apply health verdicts
    by_niche: dict[str, list] = {}
    for row in rows:
        # Row is a dict when tenant_context connects with dict_row,
        # tuple otherwise. Handle both.
        if hasattr(row, "get"):
            r = dict(row)
        else:
            r = dict(zip(
                ["niche_id", "platform", "n_rewards_7d", "min_r", "max_r",
                 "avg_r", "stddev_r", "p25", "p50", "p75", "hours_since_latest"],
                row,
            ))
        stddev = float(r.get("stddev_r") or 0)
        hours = float(r.get("hours_since_latest") or 999)
        n = int(r.get("n_rewards_7d") or 0)

        # Signal-status heuristic:
        #   cold: <3 samples (not enough to grade)
        #   stale: latest reward >48h old (metric collector may be broken)
        #   weak: samples exist but stddev < 0.05 (Goodhart-mode)
        #   healthy: real spread
        if n < 3:
            signal_status = "cold"
        elif hours > 48:
            signal_status = "stale"
        elif stddev < 0.05:
            signal_status = "weak"
        else:
            signal_status = "healthy"

        platform_row = {
            "platform": r.get("platform"),
            "n_rewards_7d": n,
            "min": round(float(r.get("min_r") or 0), 4),
            "max": round(float(r.get("max_r") or 0), 4),
            "avg": round(float(r.get("avg_r") or 0), 4),
            "stddev": round(stddev, 4),
            "p25": round(float(r.get("p25") or 0), 4),
            "p50": round(float(r.get("p50") or 0), 4),
            "p75": round(float(r.get("p75") or 0), 4),
            "hours_since_latest": round(hours, 1),
            "signal_status": signal_status,
        }
        by_niche.setdefault(r.get("niche_id"), []).append(platform_row)

    # Per-niche verdict: count healthy platforms
    result = []
    for niche_id in ("ai_creators", "anime", "gaming", "movies", "sports"):
        platforms = by_niche.get(niche_id, [])
        healthy_count = sum(1 for p in platforms if p["signal_status"] == "healthy")
        if healthy_count >= 3:
            verdict = "healthy"
        elif healthy_count >= 1:
            verdict = "partial"
        else:
            verdict = "broken"
        result.append({
            "niche_id": niche_id,
            "verdict": verdict,
            "platforms": platforms,
        })

    return api_success(data=result)
