"""Learning system API endpoints for dashboard.

Routes:
    GET /api/v1/learning/bandit-state         -- bandit arm alpha/beta for all niches
    GET /api/v1/learning/hook-classifier-status -- which niches have trained hook classifiers
"""
import json
import logging
import time as _time
from pathlib import Path

from flask import Blueprint

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

# Bandit state cache (60s TTL — 5 SP roundtrips are expensive)
_bandit_cache: dict = {"data": None, "ts": 0.0}
_BANDIT_TTL = 60.0

# Learning status cache (60s TTL — multiple table queries are expensive)
_status_cache: dict = {"data": None, "ts": 0.0}
_STATUS_TTL = 60.0


@bp.route("/bandit-state")
def bandit_state():
    """Return bandit arm alpha/beta for all niches -- shown in dashboard learning panel."""
    now = _time.time()
    if _bandit_cache["data"] is not None and (now - _bandit_cache["ts"]) < _BANDIT_TTL:
        return api_success(data=_bandit_cache["data"])

    try:
        from genlab_core.learning.arm_loader import BANDIT_LIST_NAMES, load_all_arms

        from server.core.graph_sync import get_sync_client

        client = get_sync_client()
        state = {}
        for niche_id in BANDIT_LIST_NAMES:
            arms = load_all_arms(client.bandit_arms, niche_id)
            if arms:
                state[niche_id] = {
                    arm_id: {
                        "alpha": round(a, 3),
                        "beta": round(b, 3),
                        "expected_reward": round(a / (a + b), 4),
                    }
                    for arm_id, (a, b) in arms.items()
                }
        _bandit_cache["data"] = state
        _bandit_cache["ts"] = now
        return api_success(data=state)
    except Exception as e:
        logger.warning("bandit_state failed: %s", e)
        return api_error(error=str(e), code=500)


@bp.route("/status")
def learning_status():
    """Comprehensive learning loop status — all mechanisms in one response."""
    now = _time.time()
    if _status_cache["data"] is not None and (now - _status_cache["ts"]) < _STATUS_TTL:
        return api_success(data=_status_cache["data"])

    try:
        from server.core.graph_sync import get_sync_client
        client = get_sync_client()

        # Bandit arms
        arms = client.bandit_arms.all(max_records=100)
        arm_data = {}
        for a in arms:
            f = a.get("fields", a)
            niche = f.get("niche_id", "")
            arm_data.setdefault(niche, []).append({
                "arm_id": f.get("arm_id", ""),
                "alpha": round(float(f.get("alpha", 1)), 3),
                "beta": round(float(f.get("beta", 1)), 3),
                "n_plays": _linucb_obs(a),
                "mean": round(float(f.get("alpha", 1)) / (float(f.get("alpha", 1)) + float(f.get("beta", 1))), 4),
            })

        # Pending feedback pipeline
        feedback = client.pending_feedback.all(max_records=500)
        feedback_by_status = {}
        rewards = []
        for fb in feedback:
            ff = fb.get("fields", fb)
            status = ff.get("collection_status", "unknown")
            feedback_by_status[status] = feedback_by_status.get(status, 0) + 1
            r = ff.get("reward_48h")
            if r is not None:
                rewards.append(float(r))

        # Analytics summary
        analytics = client.analytics.all(max_records=50)
        analytics_count = len(analytics)

        result = {
            "bandit_arms": arm_data,
            "feedback_pipeline": feedback_by_status,
            "rewards_computed": len(rewards),
            "avg_reward": round(sum(rewards) / len(rewards), 4) if rewards else None,
            "max_reward": round(max(rewards), 4) if rewards else None,
            "analytics_records": analytics_count,
            "hook_classifier_threshold": 50,
            "hook_classifier_progress": len(rewards),
            "config_update_threshold": 50,
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
