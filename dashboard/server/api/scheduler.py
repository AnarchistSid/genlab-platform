"""Scheduler API endpoints.

Provides REST control over the GenLabScheduler:
  GET  /api/v1/scheduler/status
  POST /api/v1/scheduler/jobs/<job_id>/pause
  POST /api/v1/scheduler/jobs/<job_id>/resume
  POST /api/v1/scheduler/trigger/<job_id>
"""
from __future__ import annotations

import logging

from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)

scheduler_bp = Blueprint("scheduler", __name__, url_prefix="/api/v1/scheduler")


@scheduler_bp.route("/status")
def scheduler_status():
    """Return current status of all scheduled jobs."""
    from server.core.scheduler import get_scheduler
    sched = get_scheduler()
    return api_success(data={"jobs": sched.list_jobs()})


@scheduler_bp.route("/jobs/<job_id>/pause", methods=["POST"])
def pause_job(job_id: str):
    """Pause a scheduled job."""
    from server.core.scheduler import get_scheduler
    try:
        get_scheduler().pause_job(job_id)
        return api_success(message=f"Job {job_id} paused")
    except Exception as exc:
        logger.warning("pause_job(%s) failed: %s", job_id, exc)
        return api_error(error=exc, message=f"Failed to pause job {job_id}", code=400)


@scheduler_bp.route("/jobs/<job_id>/resume", methods=["POST"])
def resume_job(job_id: str):
    """Resume a paused job."""
    from server.core.scheduler import get_scheduler
    try:
        get_scheduler().resume_job(job_id)
        return api_success(message=f"Job {job_id} resumed")
    except Exception as exc:
        logger.warning("resume_job(%s) failed: %s", job_id, exc)
        return api_error(error=exc, message=f"Failed to resume job {job_id}", code=400)


@scheduler_bp.route("/trigger/<job_id>", methods=["POST"])
def trigger_job(job_id: str):
    """Trigger a job to run immediately."""
    from server.core.scheduler import get_scheduler
    try:
        get_scheduler().trigger_job(job_id)
        return api_success(message=f"Job {job_id} triggered")
    except KeyError as exc:
        return api_error(error=exc, message=f"Job not found: {job_id}", code=404)
    except Exception as exc:
        logger.warning("trigger_job(%s) failed: %s", job_id, exc)
        return api_error(error=exc, message=f"Failed to trigger job {job_id}", code=400)
