"""The worker loop. Heavy imports are injected, so this runs in milliseconds."""

import json

import pytest
from genlab_core.action.matte_daemon import WorkerStats, run_once, serve
from genlab_core.action.matte_queue import LocalTransport


@pytest.fixture
def t(tmp_path):
    return LocalTransport(tmp_path)


def enqueue(t, job_id="j1", **extra):
    p = t.root / "queued" / f"{job_id}.json"
    p.write_text(json.dumps({"job_id": job_id, "clip_path": "/c.mp4", **extra}))
    return job_id


def good(job):
    return {"mask_dir": f"/masks/{job['job_id']}", "frames": 384}


def test_a_job_is_claimed_run_and_marked_done(t):
    enqueue(t)
    assert run_once(t, good) is True
    done = json.loads((t.root / "done" / "j1.json").read_text())
    assert done["frames"] == 384 and "seconds" in done
    assert not (t.root / "running" / "j1.json").exists()


def test_an_empty_queue_is_not_an_error(t):
    assert run_once(t, good) is False


def test_a_raising_job_is_marked_failed_and_the_loop_survives(t):
    enqueue(t, "boom")

    def explode(job):
        raise RuntimeError("CUDA out of memory")

    assert run_once(t, explode) is True
    failed = json.loads((t.root / "failed" / "boom.json").read_text())
    assert "CUDA out of memory" in failed["reason"]
    assert "traceback" in failed


def test_zero_frames_is_recorded_as_a_failure_with_a_reason(t):
    enqueue(t, "empty")
    run_once(t, lambda job: {"mask_dir": "/m", "frames": 0})
    failed = json.loads((t.root / "failed" / "empty.json").read_text())
    assert "zero frames" in failed["reason"]


def test_claiming_is_atomic_so_two_workers_cannot_both_run_a_job(t):
    enqueue(t, "race")
    first = t.claim("race.json")
    second = t.claim("race.json")
    assert first is not None and second is None


def test_the_result_file_appears_atomically(t):
    """The pipeline polls for done/<id>.json and must never read a partial."""
    enqueue(t)
    run_once(t, good)
    leftovers = [p.name for p in (t.root / "done").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_the_heartbeat_is_written_while_idle(t):
    serve(t, good, max_iterations=1, sleep=lambda s: None, now=lambda: 0.0)
    assert (t.root / "worker.heartbeat").exists()


def test_the_heartbeat_still_beats_after_a_failing_job(t):
    """It answers 'is a worker running', not 'did the last job succeed'. Tying
    it to success would make one bad clip look like a dead laptop."""
    enqueue(t, "bad")
    clock = {"t": 0.0}

    def boom(job):
        raise RuntimeError("nope")

    serve(
        t,
        boom,
        max_iterations=3,
        heartbeat_every_s=1,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + 10),
        now=lambda: clock["t"],
    )
    assert (t.root / "worker.heartbeat").exists()
    assert (t.root / "failed" / "bad.json").exists()


def test_a_failing_heartbeat_does_not_kill_the_loop(t, monkeypatch):
    enqueue(t)

    def boom():
        raise OSError("ssh unreachable")

    monkeypatch.setattr(t, "heartbeat", boom)
    stats = serve(t, good, max_iterations=2, sleep=lambda s: None, now=lambda: 0.0)
    assert stats.succeeded == 1, "a missed heartbeat stopped the worker"


def test_a_poisoned_job_is_not_retried_forever(t):
    """A worker stuck re-running one clip is indistinguishable from a dead one."""
    enqueue(t, "poison")
    calls = []

    def boom(job):
        calls.append(1)
        raise RuntimeError("bad clip")

    serve(t, boom, max_iterations=5, sleep=lambda s: None, now=lambda: 0.0)
    assert len(calls) == 1, f"retried a poisoned job {len(calls)} times"


def test_stats_count_what_happened(t):
    enqueue(t, "a")
    enqueue(t, "b")
    stats = WorkerStats()
    run_once(t, good, stats)
    run_once(t, good, stats)
    assert stats.claimed == 2 and stats.succeeded == 2 and stats.failed == 0
