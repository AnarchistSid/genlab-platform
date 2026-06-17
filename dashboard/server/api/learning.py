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
