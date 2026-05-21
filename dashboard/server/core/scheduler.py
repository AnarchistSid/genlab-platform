"""GenLabScheduler — APScheduler-based job scheduler with SQLite persistence.

All recurring operational tasks (publish ticks, token health, analytics,
engagement polling, quota monitoring, daily intelligence) are registered here.
This replaces the ad-hoc launchd plists for jobs that can run in-process.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# ── Default SQLite DB path ────────────────────────────────────────────────────
_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "scheduler.db"

# ── Singleton ─────────────────────────────────────────────────────────────────
_scheduler_instance: GenLabScheduler | None = None


def get_scheduler() -> GenLabScheduler:
    """Return the singleton GenLabScheduler, creating it on first call."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = GenLabScheduler()
        _scheduler_instance.start()
    return _scheduler_instance


# ── Job stubs ─────────────────────────────────────────────────────────────────


def _publish_tick() -> None:
    """Check for blueprints that are due for publishing."""
    logger.info("[scheduler] would run publish_tick — check due blueprints")


def _token_health() -> None:
    """Run platform token health checks."""
    logger.info("[scheduler] would run token_health — check all platform tokens")


def _analytics() -> None:
    """Collect analytics from all platforms."""
    logger.info("[scheduler] would run analytics — collect platform metrics")


def _engagement_poll() -> None:
    """Poll engagement (comments, mentions) from all platforms."""
    logger.info("[scheduler] would run engagement_poll — poll comments & mentions")


def _quota_monitor() -> None:
    """Monitor API quotas and evict media if needed."""
    logger.info("[scheduler] would run quota_monitor — check API quotas")


def _daily_intel() -> None:
    """Run morning intelligence briefing."""
    logger.info("[scheduler] would run daily_intel — morning briefing")


# ── Scheduler ─────────────────────────────────────────────────────────────────


class GenLabScheduler:
    """APScheduler wrapper with SQLite-backed persistence.

    Job schedules match existing launchd plist / cron times:
    - publish_tick:    every 5 min  (checks for due blueprints)
    - token_health:    daily 20:30 UTC
    - analytics:       every 6 h
    - engagement_poll: every 5 min
    - quota_monitor:   daily 00:05 UTC
    - daily_intel:     daily 06:00 UTC
    """

    def __init__(self, db_path: str | None = None) -> None:
        db = db_path or str(_DEFAULT_DB)
        # Ensure parent directory exists
        Path(db).parent.mkdir(parents=True, exist_ok=True)

        jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{db}"),
        }
        executors = {
            "default": ThreadPoolExecutor(max_workers=4),
        }
        job_defaults = {
            "coalesce": True,  # collapse missed runs into one
            "max_instances": 1,
        }
        self._scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler and register all jobs."""
        self._scheduler.start()
        self._register_jobs()
        logger.info("GenLabScheduler started with %d jobs", len(self._scheduler.get_jobs()))

    def shutdown(self, wait: bool = True) -> None:
        """Gracefully stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("GenLabScheduler stopped")

    # ── Job registration ───────────────────────────────────────────────────────

    def _register_jobs(self) -> None:
        """Register all operational jobs (idempotent — replaces existing)."""
        sched = self._scheduler

        # publish_tick — every 5 minutes
        if not sched.get_job("publish_tick"):
            sched.add_job(
                _publish_tick,
                trigger="interval",
                minutes=5,
                id="publish_tick",
                name="Publish Tick",
                replace_existing=True,
            )

        # token_health — daily at 20:30 UTC (matches launchd plist)
        if not sched.get_job("token_health"):
            sched.add_job(
                _token_health,
                trigger="cron",
                hour=20,
                minute=30,
                id="token_health",
                name="Token Health",
                replace_existing=True,
            )

        # analytics — every 6 hours
        if not sched.get_job("analytics"):
            sched.add_job(
                _analytics,
                trigger="interval",
                hours=6,
                id="analytics",
                name="Analytics Collection",
                replace_existing=True,
            )

        # engagement_poll — every 5 minutes
        if not sched.get_job("engagement_poll"):
            sched.add_job(
                _engagement_poll,
                trigger="interval",
                minutes=5,
                id="engagement_poll",
                name="Engagement Poll",
                replace_existing=True,
            )

        # quota_monitor — daily at 00:05 UTC
        if not sched.get_job("quota_monitor"):
            sched.add_job(
                _quota_monitor,
                trigger="cron",
                hour=0,
                minute=5,
                id="quota_monitor",
                name="Quota Monitor",
                replace_existing=True,
            )

        # daily_intel — daily at 06:00 UTC
        if not sched.get_job("daily_intel"):
            sched.add_job(
                _daily_intel,
                trigger="cron",
                hour=6,
                minute=0,
                id="daily_intel",
                name="Daily Intelligence",
                replace_existing=True,
            )

    # ── Job control ────────────────────────────────────────────────────────────

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return list of job dicts with id, next_run_time, state."""
        result = []
        for job in self._scheduler.get_jobs():
            # APScheduler marks a job paused when next_run_time is None
            if job.next_run_time is None:
                state = "paused"
            else:
                state = "scheduled"
            result.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": (job.next_run_time.isoformat() if job.next_run_time else None),
                    "state": state,
                }
            )
        return result

    def pause_job(self, job_id: str) -> None:
        """Pause a job by ID."""
        self._scheduler.pause_job(job_id)
        logger.info("Job %s paused", job_id)

    def resume_job(self, job_id: str) -> None:
        """Resume a paused job by ID."""
        self._scheduler.resume_job(job_id)
        logger.info("Job %s resumed", job_id)

    def trigger_job(self, job_id: str) -> None:
        """Trigger a job to run immediately (one-shot, does not affect schedule)."""
        # modify_job with next_run_time=None would pause; instead use run_job pattern
        # APScheduler doesn't have a direct trigger; we modify next_run_time to now
        job = self._scheduler.get_job(job_id)
        if job is None:
            raise KeyError(f"Job not found: {job_id}")
        # Use reschedule with a one-off date trigger at "now"
        import datetime as _dt

        _dt.datetime.now(_dt.UTC)
        # Run in a thread to avoid blocking
        import threading

        threading.Thread(target=job.func, daemon=True).start()
        logger.info("Job %s triggered immediately", job_id)
