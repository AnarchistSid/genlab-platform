"""Prefect flow: Steam player-count spike detector.

Polls Steam's ISteamUserStats API every 5 minutes, detects spikes using
z-score + rank-velocity + percentage-change, and triggers a gaming pipeline
run when a spike is confirmed.

Usage (CLI):
    python -m niches.gaming.flows.spike_detector_flow          # single check
    python -m niches.gaming.flows.spike_detector_flow --once   # alias

Deployed via deployment_manager with a 5-minute interval.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

try:
    from prefect import flow, task
except ImportError:
    # Prefect eliminated in Sprint 68 — decorators are no-ops
    def flow(fn=None, **kwargs):
        return fn if fn else lambda f: f
    def task(fn=None, **kwargs):
        return fn if fn else lambda f: f

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATE_PATH = PROJECT_ROOT / ".tmp" / "spike_detector_state.json"

# Detection thresholds
Z_SCORE_THRESHOLD = 2.0         # Standard deviations above mean
PCT_CHANGE_THRESHOLD = 0.50     # 50% increase over rolling average
RANK_VELOCITY_THRESHOLD = 5     # Rank improvement of 5+ positions
MIN_OBSERVATIONS = 6            # Need at least 6 data points (30 min)
COOLDOWN_SECONDS = 3600         # Don't re-trigger for same game within 1h

# Steam API
STEAM_FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"
STEAM_PLAYERS_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"


def _load_state() -> dict[str, Any]:
    """Load persisted spike detector state."""
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"games": {}, "triggered": {}}


def _save_state(state: dict[str, Any]) -> None:
    """Save spike detector state to disk."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _z_score(value: float, history: list[float]) -> float:
    """Calculate z-score of value against history."""
    if len(history) < 2:
        return 0.0
    mean = sum(history) / len(history)
    variance = sum((x - mean) ** 2 for x in history) / len(history)
    std = math.sqrt(variance) if variance > 0 else 1.0
    return (value - mean) / std


@task(name="poll_steam_players", retries=1, retry_delay_seconds=30)
def poll_steam_players() -> dict[str, Any]:
    """Fetch current player counts for top Steam games.

    Returns dict of {app_id: {"name": str, "players": int, "rank": int}}.
    """
    result: dict[str, Any] = {}
    try:
        resp = requests.get(STEAM_FEATURED_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        top_sellers = data.get("top_sellers", {}).get("items", [])
        for rank, item in enumerate(top_sellers[:15], start=1):
            app_id = str(item.get("id", ""))
            name = item.get("name", "")
            if not app_id or not name:
                continue

            try:
                pr = requests.get(
                    STEAM_PLAYERS_URL,
                    params={"appid": app_id},
                    timeout=10,
                )
                pr.raise_for_status()
                players = pr.json().get("response", {}).get("player_count", 0)
            except Exception:
                time.sleep(0.3)
                continue

            result[app_id] = {"name": name, "players": players, "rank": rank}
            time.sleep(0.3)

    except Exception as e:
        logger.warning("[Spike] Steam poll failed: %s", e)

    logger.info("[Spike] Polled %d games", len(result))
    return result


@task(name="detect_spikes")
def detect_spikes(
    current_data: dict[str, Any],
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Analyze player counts for spikes using z-score + rank velocity + pct change.

    Returns (spikes_detected, updated_state).
    """
    now = time.time()
    games_state = state.get("games", {})
    triggered = state.get("triggered", {})
    spikes: list[dict[str, Any]] = []

    for app_id, info in current_data.items():
        name = info["name"]
        players = info["players"]
        rank = info["rank"]

        # Initialize or update game history
        if app_id not in games_state:
            games_state[app_id] = {
                "name": name,
                "history": [],
                "rank_history": [],
            }

        gs = games_state[app_id]
        history = gs["history"]
        rank_history = gs["rank_history"]

        # Append current observation (keep last 72 = 6 hours at 5-min intervals)
        history.append(players)
        if len(history) > 72:
            history[:] = history[-72:]

        rank_history.append(rank)
        if len(rank_history) > 72:
            rank_history[:] = rank_history[-72:]

        gs["history"] = history
        gs["rank_history"] = rank_history

        # Need minimum observations before detecting
        if len(history) < MIN_OBSERVATIONS:
            continue

        # Check cooldown
        last_triggered = triggered.get(app_id, 0)
        if now - last_triggered < COOLDOWN_SECONDS:
            continue

        # 1. Z-score check
        z = _z_score(players, history[:-1])

        # 2. Percentage change vs rolling average
        avg = sum(history[:-1]) / len(history[:-1])
        pct_change = (players - avg) / avg if avg > 0 else 0

        # 3. Rank velocity (improvement over last 3 readings)
        rank_vel = 0
        if len(rank_history) >= 4:
            old_avg_rank = sum(rank_history[-4:-1]) / 3
            rank_vel = old_avg_rank - rank  # positive = improving

        # Spike if ANY two of three signals fire
        signals = [
            z >= Z_SCORE_THRESHOLD,
            pct_change >= PCT_CHANGE_THRESHOLD,
            rank_vel >= RANK_VELOCITY_THRESHOLD,
        ]

        if sum(signals) >= 2:
            spike_info = {
                "app_id": app_id,
                "name": name,
                "players": players,
                "z_score": round(z, 2),
                "pct_change": round(pct_change * 100, 1),
                "rank_velocity": round(rank_vel, 1),
                "rank": rank,
                "signals": {
                    "z_score": signals[0],
                    "pct_change": signals[1],
                    "rank_velocity": signals[2],
                },
            }
            spikes.append(spike_info)
            triggered[app_id] = now
            logger.info(
                "[Spike] DETECTED: %s — %d players, z=%.1f, pct=%.1f%%, rank_vel=%.1f",
                name, players, z, pct_change * 100, rank_vel,
            )

    state["games"] = games_state
    state["triggered"] = triggered
    return spikes, state


@task(name="trigger_pipeline_for_spike")
def trigger_pipeline_for_spike(spikes: list[dict[str, Any]]) -> str | None:
    """Trigger a gaming pipeline run via Prefect deployment for detected spikes."""
    if not spikes:
        return None

    try:
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
    except ImportError:
        logger.warning("[Spike] genlab_core.orchestration not available — cannot trigger pipeline")
        return None

    client = PrefectRESTClient()
    if not client.healthy():
        logger.warning("[Spike] Prefect server not reachable — cannot trigger pipeline")
        return None

    spike_names = [s["name"] for s in spikes]
    run_id = client.trigger_deployment(
        "gaming-pipeline/gaming-manual",
        parameters={"trigger": "spike", "dry_run": False},
    )

    if run_id:
        logger.info(
            "[Spike] Triggered gaming-pipeline (run=%s) for spikes: %s",
            run_id, spike_names,
        )
    else:
        logger.error("[Spike] Failed to trigger gaming-pipeline for spikes: %s", spike_names)

    return run_id


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

@flow(
    name="steam-spike-detector",
    description="Poll Steam for player-count spikes every 5 minutes. "
                "Triggers a gaming pipeline run when spikes are confirmed.",
    retries=0,
    timeout_seconds=120,
)
def steam_spike_detector() -> dict[str, Any]:
    """Single spike-detection cycle.

    Returns summary dict with detected spikes and trigger status.
    """
    state = _load_state()

    current_data = poll_steam_players()

    if not current_data:
        return {"status": "no_data", "spikes": []}

    spikes, updated_state = detect_spikes(current_data, state)

    _save_state(updated_state)

    triggered_run = None
    if spikes:
        triggered_run = trigger_pipeline_for_spike(spikes)

    return {
        "status": "spikes_detected" if spikes else "no_spikes",
        "spikes": spikes,
        "games_tracked": len(updated_state.get("games", {})),
        "triggered_run_id": triggered_run,
        "timestamp": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = steam_spike_detector()
    print(json.dumps(result, indent=2, default=str))
