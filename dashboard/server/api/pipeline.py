"""Pipeline API endpoints — niche-aware status, run history, trigger, and logs."""

import json
import logging
import os
import time as _time
from datetime import datetime
from pathlib import Path

import yaml
from flask import Blueprint, request

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("pipeline_api", __name__, url_prefix="/api/v1/pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# GENLAB_ROOT is the parent GenLab directory — pipeline log files + runs live here
GENLAB_ROOT = Path(os.environ.get("GENLAB_ROOT", PROJECT_ROOT.parent))
LOGS_DIR = GENLAB_ROOT / ".tmp"
# Pipeline lock files and run reports live under the GenLab workspace root,
# NOT the dashboard subdir.  Run dirs follow the naming convention
# "{niche_id}_{YYYYMMDD_HHMMSS}" inside GENLAB_ROOT/.tmp/runs/.
TMP_DIR = GENLAB_ROOT / ".tmp"
RUNS_DIR = TMP_DIR / "runs"
REGISTRY_PATH = PROJECT_ROOT / "configs" / "niches_registry.yaml"

# Module-level cache (5s TTL for responsive pipeline monitoring)
_status_cache: dict = {"data": None, "ts": 0.0}
_STATUS_TTL = 5.0


def _load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("niches", [])


def _pipeline_status(niche_id: str) -> tuple[str, str | None, dict | None]:
    """Check lock + progress files. Returns (status, current_stage, stage_progress)."""
    lock_file = TMP_DIR / f"{niche_id}_pipeline.lock"
    if not lock_file.exists():
        return "idle", None, None

    age_seconds = _time.time() - lock_file.stat().st_mtime
    if age_seconds > 1800:  # 30 min stale
        return "error", None, None

    # Running — read progress
    progress_file = TMP_DIR / f"{niche_id}_progress.json"
    stage = None
    stage_progress = None
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text())
            stage = data.get("stage")
            stage_progress = {
                "stage": data.get("stage"),
                "stage_index": data.get("stage_index", 0),
                "total_stages": data.get("total_stages", 0),
                "items_processed": data.get("items_processed", 0),
                "items_total": data.get("items_total", 0),
                "elapsed_seconds": data.get("elapsed_seconds", 0),
            }
        except (json.JSONDecodeError, KeyError):
            pass
    return "running", stage, stage_progress


def _normalize_run(data: dict, run_id: str) -> dict:
    """Normalize a raw run_report.json into the PipelineRun shape.

    Handles both legacy format (metrics.speed.step_timings array) and
    current format (stage_timings flat dict at top level).
    """
    metrics = data.get("metrics", {})

    # Current format: stage_timings is a flat dict {stage_name: seconds}
    stage_timings_dict = data.get("stage_timings", {})
    # Legacy format: metrics.speed.step_timings is an array of objects
    step_timings = metrics.get("speed", {}).get("step_timings", [])

    if stage_timings_dict and not step_timings:
        # Convert current format to stages list
        stages = [
            {
                "name": name,
                "label": name,
                "status": "completed",
                "duration_seconds": round(secs, 2),
            }
            for name, secs in stage_timings_dict.items()
            if not name.startswith("_")
        ]
        total_steps = len(stages)
        ok_steps = total_steps
    else:
        # Legacy format
        skipped_steps = sum(1 for s in step_timings if s.get("status") == "skipped")
        total_steps = (len(step_timings) - skipped_steps) or data.get("total_steps", 0)
        ok_steps = sum(1 for s in step_timings if s.get("status") in ("ok", "success"))
        stages = [
            {
                "name": s.get("step", ""),
                "label": s.get("label", ""),
                "status": "completed"
                if s.get("status") in ("ok", "success")
                else (
                    "skipped"
                    if s.get("status") == "skipped"
                    else ("error" if s.get("status") == "failed" else s.get("status", "unknown"))
                ),
                "duration_seconds": s.get("elapsed_seconds", 0),
            }
            for s in step_timings
        ]

    raw_errors = data.get("errors", 0)
    error_count = len(raw_errors) if isinstance(raw_errors, list) else int(raw_errors or 0)
    slo_violations = data.get("slo_violations", [])

    return {
        "run_id": run_id,
        "niche_id": data.get("niche_id", run_id.split("_")[0]),
        "run_type": data.get("run_type", "pipeline"),
        "date": data.get("start_time") or data.get("timestamp") or run_id,
        "started_at": data.get("start_time") or data.get("timestamp"),
        "completed_at": data.get("end_time") or data.get("completed_at"),
        "duration_seconds": data.get("duration_seconds", 0),
        "steps_completed": ok_steps or data.get("steps_completed", total_steps),
        "total_steps": total_steps,
        "errors": error_count,
        "cost_estimate": metrics.get("estimated_cost_usd", data.get("estimated_cost_usd", 0)),
        "status": data.get("status", "unknown"),
        "slo_violations": slo_violations,
        "stages": stages,
        "blueprints_count": metrics.get("blueprints_count", 0),
        "stories_count": metrics.get("stories_count", 0),
        # RENDER #2 (2026-06-13): per-stage silent-failure attribution.
        # Empty {} means every tracked stage was clean. Non-empty means
        # the run.status was promoted from "success" to "partial" — the
        # dashboard frontend renders a badge per entry so operators see
        # what specifically failed (e.g. whisper_captions: 2) instead
        # of having to grep the journal.
        "stage_failures": data.get("stage_failures", {}),
        # PR #508 (2026-06-24): structured bottleneck attribution for
        # zero-blueprint runs. ``bottleneck_stage`` is one of
        # "fetch", "pre_download_dedup", "video_gate",
        # "video_validation", "push_to_backlog", or None when
        # blueprints were produced normally. ``bottleneck_reason``
        # is the human-readable explanation. Frontend renders these
        # as a "blocked at X" badge in the run-history row so the
        # operator sees the funnel killer without parsing
        # ``slo_violations`` strings.
        #
        # Fields were added by PR #504 (genlab-core run_report.py).
        # Default to None (not "") so the frontend can use
        # `if (run.bottleneck_stage)` as a truthy guard — empty string
        # vs None ambiguity would force less-readable explicit checks.
        "bottleneck_stage": data.get("bottleneck_stage"),
        "bottleneck_reason": data.get("bottleneck_reason"),
    }


def _find_runs(limit: int = 50, niche_id: str | None = None) -> list[dict]:
    """Find run reports, sorted by mtime descending.

    Args:
        limit: Max runs to return.
        niche_id: If set, only return runs whose directory name starts with
            this niche (e.g. "anime_20260316_163522" matches niche_id="anime").
    """
    if not RUNS_DIR.exists():
        return []
    # Collect entries first, then sort — avoids FileNotFoundError if a dir
    # is deleted between iterdir() and stat().
    entries = []
    try:
        for entry in RUNS_DIR.iterdir():
            try:
                # Filter by niche_id prefix when requested
                if niche_id and not entry.name.startswith(f"{niche_id}_"):
                    continue
                entries.append((entry, entry.stat().st_mtime))
            except (FileNotFoundError, OSError):
                continue
    except OSError:
        return []
    entries.sort(key=lambda e: e[1], reverse=True)

    runs = []
    for run_dir, _ in entries:
        if not run_dir.is_dir():
            continue
        report = run_dir / "run_report.json"
        if report.exists():
            try:
                data = json.loads(report.read_text())
                runs.append(_normalize_run(data, run_dir.name))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        if len(runs) >= limit:
            break
    return runs


def _last_run_for_niche(niche_id: str | None = None) -> dict | None:
    """Find the most recent run, optionally filtered by niche.

    Prefers runs with step_timings (full pipeline runs).
    """
    runs = _find_runs(limit=20, niche_id=niche_id)
    # Prefer runs with step timings (full runs, not publish-only)
    for r in runs:
        if r.get("stages"):
            return r
    # Fallback to any run
    return runs[0] if runs else None


def _prefect_healthy() -> bool:
    """Check if the Prefect server is reachable."""
    try:
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
    except ImportError:
        return False
    try:
        return PrefectRESTClient().healthy()
    except Exception:
        return False


def _merge_prefect_status(niches_data: list[dict]) -> list[dict]:
    """Overlay Prefect-sourced status for niches managed by Prefect.

    If the Prefect server is reachable, gaming (and other Prefect-managed
    niches) get their status from Prefect flow runs instead of lock files.
    Falls back gracefully if Prefect is unavailable.
    """
    try:
        from genlab_core.orchestration.prefect_api import PrefectRESTClient
    except ImportError:
        return niches_data

    client = PrefectRESTClient()
    if not client.healthy():
        return niches_data

    try:
        runs = client.list_flow_runs(limit=10)
    except Exception:
        return niches_data

    # Build latest-run-per-niche from Prefect
    flow_cache: dict[str, str] = {}
    prefect_latest: dict[str, dict] = {}

    for run in runs:
        flow_id = run.get("flow_id", "")
        if flow_id not in flow_cache:
            flow_meta = client.get_flow_by_id(flow_id)
            flow_cache[flow_id] = flow_meta.get("name", "") if flow_meta else ""
        flow_name = flow_cache[flow_id]

        if "spike" in flow_name.lower():
            continue

        niche_id = (
            "gaming"
            if "gaming" in flow_name
            else (
                "ai_creators" if "ai_creators" in flow_name or "ai-creators" in flow_name else None
            )
        )
        if niche_id and niche_id not in prefect_latest:
            prefect_latest[niche_id] = run

    # Overlay Prefect data onto matching niches
    state_map = {
        "COMPLETED": "idle",
        "RUNNING": "running",
        "FAILED": "error",
        "CANCELLED": "idle",
        "PENDING": "running",
        "SCHEDULED": "idle",
    }

    for niche in niches_data:
        niche_id = niche.get("id")
        run = prefect_latest.get(niche_id)
        if not run:
            continue

        state = run.get("state", {}) or {}
        state_type = state.get("type", "UNKNOWN")
        niche["pipeline_status"] = state_map.get(state_type, "idle")

        if state_type == "RUNNING":
            task_runs = client.get_task_runs_for_flow(run.get("id", ""))
            for tr in reversed(task_runs):
                ts = tr.get("state", {}) or {}
                if ts.get("type") == "RUNNING":
                    niche["current_stage"] = tr.get("name")
                    break

            completed = sum(
                1 for tr in task_runs if (tr.get("state", {}) or {}).get("type") == "COMPLETED"
            )
            niche["stage_progress"] = {
                "stage": niche.get("current_stage"),
                "stage_index": completed,
                "total_stages": len(task_runs),
                "items_processed": 0,
                "items_total": 0,
                "elapsed_seconds": 0,
            }

    return niches_data


# ── Endpoint 1: GET /api/v1/pipeline/status ──────────────────────


@bp.route("/status", methods=["GET"])
def status():
    now = _time.time()
    if _status_cache["data"] and (now - _status_cache["ts"]) < _STATUS_TTL:
        return api_success(data=_status_cache["data"])

    try:
        registry = _load_registry()
        active_niches = [n for n in registry if n.get("status") in ("active", "mvp")]

        all_runs = _find_runs(limit=20)

        niches_data = []
        for niche in active_niches:
            niche_id = niche.get("id")
            if not niche_id:
                continue
            pipe_status, current_stage, stage_progress = _pipeline_status(niche_id)

            # Per-niche run data — filter runs by niche_id prefix
            niche_runs = _find_runs(limit=10, niche_id=niche_id)
            niche_last_run = _last_run_for_niche(niche_id=niche_id)

            niches_data.append(
                {
                    "id": niche_id,
                    "display_name": niche.get("display_name", niche_id),
                    "accent_hex": niche.get("accent_hex", "#6366f1"),
                    "pipeline_status": pipe_status,
                    "current_stage": current_stage,
                    "stage_progress": stage_progress,
                    "last_run": niche_last_run,
                    "run_history": niche_runs,
                }
            )

        last_run_compat = all_runs[0] if all_runs else None
        health = "unknown"
        if last_run_compat:
            health = "healthy" if last_run_compat["errors"] == 0 else "degraded"

        # Merge Prefect-managed niche statuses when available (isolated)
        prefect_connected = False
        try:
            niches_data = _merge_prefect_status(niches_data)
            prefect_connected = _prefect_healthy()
        except Exception as exc:
            logger.warning("Prefect overlay failed (non-fatal): %s", exc)

        result = {
            "niches": niches_data,
            "prefect_connected": prefect_connected,
            "data": {
                "last_run": last_run_compat,
                "health": health,
            },
        }
        _status_cache["data"] = result
        _status_cache["ts"] = now
        return api_success(data=result)
    except Exception as exc:
        logger.error("Pipeline status error: %s", exc, exc_info=True)
        # Return degraded response instead of 500
        return api_success(
            data={
                "niches": [],
                "prefect_connected": False,
                "data": {"last_run": None, "health": "error"},
                "error": str(exc),
            }
        )


def _trigger_via_prefect(niche_id: str, mode: str) -> tuple:
    """Trigger a pipeline run via Prefect deployment."""
    deployment_map = {
        "gaming": "gaming-pipeline/gaming-manual",
    }
    dep_name = deployment_map.get(niche_id)
    if not dep_name:
        return api_error(error=f"No Prefect deployment for niche '{niche_id}'")

    try:
        from genlab_core.orchestration.prefect_api import PrefectRESTClient

        client = PrefectRESTClient()
        if not client.healthy():
            return api_error(error="Prefect server not reachable", code=503)

        run_id = client.trigger_deployment(dep_name, parameters={"trigger": "manual"})
        if run_id:
            _status_cache["data"] = None
            _status_cache["ts"] = 0.0
            return api_success(data={"status": "ok", "run_id": run_id, "niche_id": niche_id})
        return api_error(error="Failed to trigger Prefect deployment", code=500)
    except ImportError:
        return api_error(error="Prefect not installed", code=500)
    except Exception as exc:
        logger.error("Prefect trigger error: %s", exc)
        return api_error(error=str(exc), code=500)


def _run_unified_pipeline(run_id: str, niche_id: str, lock_fd=None):
    """Run pipeline via the unified CLI in a background thread.

    `lock_fd` is the flock-held file handle from the trigger endpoint; we
    keep it open for the run's lifetime so concurrent POSTs are blocked.
    """
    import subprocess

    from server.review_server import _express_lock, express_state, socketio

    with _express_lock:
        express_state["running"] = True
        express_state["current_step"] = "starting"

    lock_file = TMP_DIR / f"{niche_id}_pipeline.lock"
    try:
        if lock_fd is not None:
            lock_fd.write(run_id)
            lock_fd.flush()
        else:
            lock_file.write_text(run_id)
        socketio.emit("pipeline_started", {"niche_id": niche_id, "run_id": run_id})

        uv = os.path.expanduser("~/.local/bin/uv")
        cmd = [
            uv,
            "run",
            "--package",
            "genlab-core",
            "python",
            "-m",
            "genlab_core.pipeline",
            "--niche",
            niche_id,
            "--verbose",
        ]
        env = {
            **os.environ,
            "BACKLOG_CONFIG_PATH": str(
                GENLAB_ROOT / "genlab-core" / "config" / "lists_config.yaml"
            ),
        }

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(GENLAB_ROOT),
            timeout=1800,
            env=env,
        )

        success = result.returncode == 0
        socketio.emit(
            "pipeline_complete",
            {
                "niche_id": niche_id,
                "run_id": run_id,
                "success": success,
                "error": result.stderr[-500:] if not success and result.stderr else None,
            },
        )
    except Exception as exc:
        logger.error("Unified pipeline error for %s: %s", niche_id, exc)
        try:
            socketio.emit(
                "pipeline_complete",
                {
                    "niche_id": niche_id,
                    "run_id": run_id,
                    "success": False,
                    "error": str(exc),
                },
            )
        except Exception:
            pass
    finally:
        if lock_fd is not None:
            try:
                lock_fd.close()  # releases the flock
            except Exception:
                pass
        lock_file.unlink(missing_ok=True)
        with _express_lock:
            express_state["running"] = False
            express_state["current_step"] = None
        _status_cache["data"] = None
        _status_cache["ts"] = 0.0


# ── Endpoint 2: POST /api/v1/pipeline/trigger ────────────────────


@bp.route("/trigger", methods=["POST"])
def trigger():
    """Trigger the express pipeline in a background thread."""
    try:
        import fcntl
        import threading

        from server.review_server import express_state

        niche_id = (request.json or {}).get("niche_id") or "all"
        mode = (request.json or {}).get("mode", "full")

        VALID_NICHES = {"ai_creators", "gaming", "sports", "movies", "anime", "all"}
        if niche_id not in VALID_NICHES:
            return api_error(error=f"Unknown niche: {niche_id}", code=400)

        # Atomically acquire the per-niche pipeline lock. Two concurrent POSTs
        # used to race past a non-atomic existence check; flock is the only
        # safe way to single-thread this. The fd is leaked into the worker
        # thread which must close it (handled by `_run_unified_pipeline`).
        lock_file = TMP_DIR / f"{niche_id}_pipeline.lock"
        lock_fd = open(lock_file, "w")
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_fd.close()
            return api_error(error="Pipeline already running", code=409)

        # express_state is in-memory; on a worker restart it can desync from
        # the on-disk lock. The flock above is the source of truth — this
        # check is informational (and clears the dict so future code that
        # reads it doesn't loop forever on a stale value).
        if express_state.get("running"):
            started = express_state.get("started_at", 0)
            if _time.time() - started > 900:
                logger.warning("Pipeline stuck for >15 min — resetting in-memory state")
                express_state["running"] = False

        run_id = f"pipeline_{niche_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        express_state["started_at"] = _time.time()
        # Pass the locked fd into the worker; it owns the lock for the run's
        # lifetime and releases when closed.
        t = threading.Thread(
            target=_run_unified_pipeline, args=(run_id, niche_id, lock_fd), daemon=True
        )
        t.start()

        # Emit WebSocket event to niche room + global
        try:
            from server.review_server import socketio

            event_data = {"niche_id": niche_id, "mode": mode, "run_id": run_id}
            socketio.emit("pipeline_started", event_data, room=f"niche:{niche_id}")
            socketio.emit("pipeline_started", event_data)
        except Exception:
            pass

        # Clear status cache
        _status_cache["data"] = None
        _status_cache["ts"] = 0.0

        return api_success(data={"status": "ok", "run_id": run_id, "niche_id": niche_id})
    except ImportError as exc:
        logger.error("Pipeline trigger import error: %s", exc)
        return api_error(error="Pipeline trigger not available", code=500)
    except Exception as exc:
        logger.error("Pipeline trigger error: %s", exc)
        return api_error(error=str(exc), code=500)


# ── Endpoint 3: GET /api/v1/pipeline/runs ────────────────────────


@bp.route("/runs", methods=["GET"])
def list_runs():
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(int(request.args.get("per_page", 20)), 50)
    except (ValueError, TypeError):
        return api_error(error="Invalid page or per_page parameter")

    runs = _find_runs(limit=100)

    total = len(runs)
    start = (page - 1) * per_page
    page_data = runs[start : start + per_page]

    return api_success(
        data={
            "data": page_data,
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            },
        }
    )


@bp.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    if ".." in run_id or "/" in run_id:
        return api_error(error="Invalid run ID")
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        return api_not_found(message="Run not found")

    report_path = run_dir / "run_report.json"
    if report_path.exists():
        try:
            data = json.loads(report_path.read_text())
            data["run_id"] = run_id
            return api_success(data={"data": data})
        except json.JSONDecodeError:
            return api_error(error="Invalid report", code=500)

    data = {"run_id": run_id}
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.suffix == ".json":
            try:
                data[f.stem] = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                data[f.stem] = {"error": "Could not parse"}
    data["artifacts"] = [f.name for f in run_dir.iterdir() if f.is_file()]
    return api_success(data={"data": data})


# ── Endpoint: GET /api/v1/pipeline/logs ────────────────────────


@bp.route("/logs", methods=["GET"])
def get_logs():
    """Return recent pipeline log lines for a niche.

    Query params:
        niche_id: required (e.g. "gaming", "ai_creators")
        limit: max lines to return (default 200, max 500)
        after_ts: ISO timestamp — only return lines newer than this
    """
    niche_id = request.args.get("niche_id")
    if not niche_id:
        return api_error(error="niche_id is required")

    limit = min(int(request.args.get("limit", 200)), 500)
    after_ts = request.args.get("after_ts")

    log_path = LOGS_DIR / f"{niche_id}_pipeline_logs.jsonl"
    if not log_path.exists():
        return api_success(data={"data": [], "log_path": str(log_path)})

    try:
        from genlab_core.pipeline import read_recent_logs

        entries = read_recent_logs(log_path, limit=limit, after_ts=after_ts)
        return api_success(data={"data": entries})
    except ImportError:
        # Fallback: read directly without genlab-core
        entries = _read_jsonl_tail(log_path, limit, after_ts)
        return api_success(data={"data": entries})


@bp.route("/quality-stats", methods=["GET"])
def quality_stats():
    """Aggregate content quality stats from latest pipeline run reports per niche."""
    stats = {
        "hooks_generated": 0,
        "qc_passed": 0,
        "qc_failed": 0,
        "qc_total": 0,
        "videos_validated": 0,
        "videos_fixed": 0,
    }

    if not RUNS_DIR.exists():
        return api_success(data=stats)

    # Get latest run report per niche (max 5)
    seen_niches: set[str] = set()
    try:
        run_dirs = sorted(
            [d for d in RUNS_DIR.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for run_dir in run_dirs:
            report = run_dir / "run_report.json"
            if not report.exists():
                continue
            try:
                data = json.loads(report.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            niche = data.get("niche_id", "")
            if niche in seen_niches:
                continue
            seen_niches.add(niche)

            m = data.get("metrics", {})
            stats["hooks_generated"] += m.get("stories_count", 0) or 0
            qc = m.get("qc", {})
            stats["qc_passed"] += qc.get("passed", 0) or 0
            stats["qc_failed"] += qc.get("failed", 0) or 0
            stats["qc_total"] += qc.get("total", 0) or 0
            vv = m.get("video_validation", {})
            stats["videos_validated"] += vv.get("passed", 0) or 0
            stats["videos_fixed"] += vv.get("fixed", 0) or 0

            if len(seen_niches) >= 5:
                break
    except Exception as exc:
        logger.warning("quality-stats scan failed: %s", exc)

    return api_success(data=stats)


def _read_jsonl_tail(
    path: Path,
    limit: int,
    after_ts: str | None,
) -> list[dict]:
    """Fallback JSONL reader if genlab-core isn't available."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-(limit * 2) :]
    except OSError:
        return []

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if after_ts and entry.get("ts", "") <= after_ts:
            continue
        entries.append(entry)
    return entries[-limit:]
