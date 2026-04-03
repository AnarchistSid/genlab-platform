"""Tests for GenLabScheduler."""
from __future__ import annotations

import tempfile
from pathlib import Path


def test_scheduler_registers_all_jobs():
    from server.core.scheduler import GenLabScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_scheduler.db"
        sched = GenLabScheduler(db_path=str(db_path))
        sched.start()
        jobs = sched.list_jobs()
        job_ids = [j["id"] for j in jobs]
        assert "publish_tick" in job_ids
        assert "token_health" in job_ids
        assert "analytics" in job_ids
        assert "engagement_poll" in job_ids
        sched.shutdown()


def test_scheduler_pause_resume():
    from server.core.scheduler import GenLabScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_scheduler.db"
        sched = GenLabScheduler(db_path=str(db_path))
        sched.start()
        sched.pause_job("token_health")
        jobs = {j["id"]: j for j in sched.list_jobs()}
        assert jobs["token_health"]["state"] == "paused"
        sched.resume_job("token_health")
        jobs = {j["id"]: j for j in sched.list_jobs()}
        assert jobs["token_health"]["state"] != "paused"
        sched.shutdown()
