"""Background thread that polls Prefect for pipeline progress and emits SocketIO events.

Usage:
    from genlab_core.orchestration.socketio_poller import start_prefect_poller

    socketio = SocketIO(app)
    start_prefect_poller(socketio, interval=5)

Emits:
    "pipeline_progress" — PipelineProgress-compatible dict per running flow
    "pipeline_complete" — When a flow transitions from RUNNING to COMPLETED/FAILED
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def start_prefect_poller(socketio: Any, *, interval: int = 5) -> threading.Thread:
    """Start a background thread that polls Prefect and emits SocketIO events.

    Args:
        socketio: Flask-SocketIO instance.
        interval: Seconds between polls.

    Returns:
        The daemon thread (already started).
    """
    thread = threading.Thread(
        target=_poll_loop,
        args=(socketio, interval),
        name="prefect-poller",
        daemon=True,
    )
    thread.start()
    logger.info("[Poller] Started Prefect poller (interval=%ds)", interval)
    return thread


def _poll_loop(socketio: Any, interval: int) -> None:
    """Main polling loop — runs forever in a daemon thread."""
    from genlab_core.orchestration.prefect_api import PrefectRESTClient

    client = PrefectRESTClient()
    previously_running: set[str] = set()

    while True:
        try:
            _poll_once(client, socketio, previously_running)
        except Exception as e:
            logger.debug("[Poller] Poll error: %s", e)

        time.sleep(interval)


def _poll_once(
    client: Any,
    socketio: Any,
    previously_running: set[str],
) -> None:
    """Single poll iteration."""
    if not client.healthy():
        return

    runs = client.list_flow_runs(limit=10)
    currently_running: set[str] = set()

    flow_cache: dict[str, str] = {}

    for run in runs:
        run_id = run.get("id", "")
        state = run.get("state", {}) or {}
        state_type = state.get("type", "")

        flow_id = run.get("flow_id", "")
        if flow_id not in flow_cache:
            flow_meta = client.get_flow_by_id(flow_id)
            flow_cache[flow_id] = flow_meta.get("name", "") if flow_meta else ""
        flow_name = flow_cache[flow_id]

        # Skip spike detector from progress events
        if "spike" in flow_name.lower():
            continue

        parameters = run.get("parameters", {}) or {}

        if state_type == "RUNNING":
            currently_running.add(run_id)

            # Get task runs for stage breakdown
            task_runs = client.get_task_runs_for_flow(run_id)
            current_stage = None
            completed_count = 0
            for tr in task_runs:
                ts = tr.get("state", {}) or {}
                if ts.get("type") == "COMPLETED":
                    completed_count += 1
                elif ts.get("type") == "RUNNING":
                    current_stage = tr.get("name")

            # Calculate elapsed
            started = run.get("start_time") or run.get("expected_start_time")
            elapsed = 0.0
            if started:
                from datetime import datetime, timezone
                try:
                    s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - s).total_seconds()
                except (ValueError, TypeError):
                    pass

            # Determine niche_id
            niche_id = "gaming" if "gaming" in flow_name else "ai_creators"

            progress = {
                "niche_id": niche_id,
                "run_id": run_id,
                "pipeline_status": "running",
                "current_stage": current_stage,
                "stage_index": completed_count,
                "total_stages": len(task_runs),
                "items_processed": 0,
                "items_total": 0,
                "elapsed_seconds": round(elapsed, 1),
            }

            socketio.emit("pipeline_progress", progress)

        elif state_type in ("COMPLETED", "FAILED") and run_id in previously_running:
            # Transition: was running, now done
            niche_id = "gaming" if "gaming" in flow_name else "ai_creators"

            started = run.get("start_time")
            finished = run.get("end_time")
            duration = 0.0
            if started and finished:
                from datetime import datetime
                try:
                    s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                    duration = (f - s).total_seconds()
                except (ValueError, TypeError):
                    pass

            socketio.emit("pipeline_complete", {
                "run_id": run_id,
                "niche_id": niche_id,
                "state": state_type.lower(),
                "duration": round(duration, 1),
                "summary": {},
            })

    previously_running.clear()
    previously_running.update(currently_running)
