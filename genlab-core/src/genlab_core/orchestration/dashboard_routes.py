"""Flask blueprint providing Prefect pipeline status endpoints.

Mount this into any Flask app to get pipeline monitoring REST endpoints:
    from genlab_core.orchestration.dashboard_routes import prefect_bp
    app.register_blueprint(prefect_bp, url_prefix="/api/prefect")

Endpoints:
    GET /status          — Current pipeline status per niche
    GET /runs            — Recent flow run history
    GET /runs/<id>       — Single run detail with task breakdown
    POST /trigger        — Trigger a pipeline deployment
    GET /health          — Prefect server health check
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

prefect_bp = Blueprint("prefect", __name__)


def _get_client():
    """Lazy-load PrefectRESTClient."""
    from genlab_core.orchestration.prefect_api import PrefectRESTClient
    return PrefectRESTClient()


def _state_type_to_status(state_type: str | None) -> str:
    """Map Prefect state types to simple status strings."""
    mapping = {
        "COMPLETED": "idle",
        "RUNNING": "running",
        "FAILED": "error",
        "CANCELLED": "idle",
        "PENDING": "running",
        "SCHEDULED": "idle",
    }
    return mapping.get(state_type or "", "idle")


def _extract_niche_id(flow_name: str, parameters: dict) -> str:
    """Extract niche_id from flow name or parameters."""
    if "gaming" in flow_name:
        return "gaming"
    if "ai_news" in flow_name or "ai-news" in flow_name:
        return "ai_news"
    return parameters.get("niche_id", "unknown")


def _format_run(run: dict, task_runs: list[dict] | None = None) -> dict:
    """Format a Prefect flow run into dashboard-friendly JSON."""
    state = run.get("state", {}) or {}
    state_type = state.get("type", "UNKNOWN")
    parameters = run.get("parameters", {}) or {}
    flow_name = run.get("name", "")

    started = run.get("start_time") or run.get("expected_start_time")
    finished = run.get("end_time")

    duration = 0.0
    if started and finished:
        try:
            s = datetime.fromisoformat(started.replace("Z", "+00:00"))
            f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            duration = (f - s).total_seconds()
        except (ValueError, TypeError):
            pass

    result = {
        "run_id": run.get("id", ""),
        "flow_name": flow_name,
        "niche_id": _extract_niche_id(flow_name, parameters),
        "state": state_type,
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": round(duration, 1),
        "trigger": parameters.get("trigger", "scheduled"),
        "parameters": parameters,
    }

    # Add task-level breakdown if available
    if task_runs is not None:
        stages = []
        for tr in task_runs:
            ts = tr.get("state", {}) or {}
            tr_started = tr.get("start_time")
            tr_finished = tr.get("end_time")
            tr_dur = 0.0
            if tr_started and tr_finished:
                try:
                    s = datetime.fromisoformat(tr_started.replace("Z", "+00:00"))
                    f = datetime.fromisoformat(tr_finished.replace("Z", "+00:00"))
                    tr_dur = (f - s).total_seconds()
                except (ValueError, TypeError):
                    pass

            stages.append({
                "name": tr.get("name", "?"),
                "status": ts.get("type", "PENDING").lower(),
                "started_at": tr_started,
                "finished_at": tr_finished,
                "duration_seconds": round(tr_dur, 1),
                "error": ts.get("message") if ts.get("type") == "FAILED" else None,
            })
        result["stages"] = stages

    return result


@prefect_bp.route("/health")
def health():
    """Check Prefect server connectivity."""
    client = _get_client()
    ok = client.healthy()
    return jsonify({"prefect_healthy": ok}), 200 if ok else 503


@prefect_bp.route("/status")
def pipeline_status():
    """Get current pipeline status per niche.

    Returns the most recent run per flow (gaming-pipeline, ai-news-pipeline)
    to determine if each niche is running/idle/error.
    """
    client = _get_client()
    runs = client.list_flow_runs(limit=10)

    # Group by flow and take most recent
    latest_by_flow: dict[str, dict] = {}
    for run in runs:
        name = run.get("name", "")
        # The flow_id needs to be resolved to flow name
        flow_id = run.get("flow_id", "")
        if flow_id and flow_id not in latest_by_flow:
            flow_meta = client.get_flow_by_id(flow_id)
            if flow_meta:
                name = flow_meta.get("name", name)

        if name not in latest_by_flow:
            latest_by_flow[name] = run

    niches = []
    for flow_name, run in latest_by_flow.items():
        state = run.get("state", {}) or {}
        state_type = state.get("type", "UNKNOWN")
        parameters = run.get("parameters", {}) or {}
        niche_id = _extract_niche_id(flow_name, parameters)

        # Skip spike detector from niche status
        if "spike" in flow_name.lower():
            continue

        niche_status = {
            "id": niche_id,
            "pipeline_status": _state_type_to_status(state_type),
            "current_stage": None,
            "last_run": _format_run(run),
        }

        # If running, get task runs to find current stage
        if state_type == "RUNNING":
            task_runs = client.get_task_runs_for_flow(run.get("id", ""))
            for tr in reversed(task_runs):
                ts = tr.get("state", {}) or {}
                if ts.get("type") == "RUNNING":
                    niche_status["current_stage"] = tr.get("name")
                    break

            niche_status["stage_progress"] = {
                "stage_index": sum(
                    1 for tr in task_runs
                    if (tr.get("state", {}) or {}).get("type") == "COMPLETED"
                ),
                "total_stages": len(task_runs),
                "elapsed_seconds": 0,
                "items_processed": 0,
                "items_total": 0,
            }

        niches.append(niche_status)

    return jsonify({"niches": niches})


@prefect_bp.route("/runs")
def list_runs():
    """List recent pipeline runs."""
    limit = request.args.get("limit", 20, type=int)
    client = _get_client()
    runs = client.list_flow_runs(limit=limit)

    # Resolve flow names
    flow_cache: dict[str, str] = {}
    formatted = []
    for run in runs:
        flow_id = run.get("flow_id", "")
        if flow_id not in flow_cache:
            flow_meta = client.get_flow_by_id(flow_id)
            flow_cache[flow_id] = flow_meta.get("name", "") if flow_meta else ""
        run["name"] = flow_cache[flow_id]
        formatted.append(_format_run(run))

    return jsonify({"data": formatted})


@prefect_bp.route("/runs/<run_id>")
def get_run(run_id: str):
    """Get a single run with task-level breakdown."""
    client = _get_client()
    run = client.get_flow_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404

    # Resolve flow name
    flow_id = run.get("flow_id", "")
    flow_meta = client.get_flow_by_id(flow_id)
    run["name"] = flow_meta.get("name", "") if flow_meta else ""

    task_runs = client.get_task_runs_for_flow(run_id)
    return jsonify(_format_run(run, task_runs))


@prefect_bp.route("/trigger", methods=["POST"])
def trigger_run():
    """Trigger a pipeline deployment.

    JSON body:
        niche_id: str (optional — defaults to all niches)
        mode: str (optional)
        parameters: dict (optional — passed to the flow)
    """
    data = request.get_json(silent=True) or {}
    niche_id = data.get("niche_id")
    parameters = data.get("parameters", {})
    parameters.setdefault("trigger", "manual")

    client = _get_client()

    deployment_map = {
        "gaming": "gaming-pipeline/gaming-manual",
        "ai_news": "ai-news-pipeline/ai-news-manual",
    }

    targets = (
        {niche_id: deployment_map[niche_id]}
        if niche_id and niche_id in deployment_map
        else deployment_map
    )

    results = {}
    for niche, dep_name in targets.items():
        run_id = client.trigger_deployment(dep_name, parameters=parameters)
        results[niche] = {
            "triggered": run_id is not None,
            "run_id": run_id,
        }

    return jsonify(results)
