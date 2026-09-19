"""Craft is additive: a missing worker costs a fire, never a publish.

The matte step cannot run on prod -- measured 2026-09-17 on the 2-core/3.8GB
box: SAM2 at 20.66 s/frame (138 min for 384 frames against a 15-minute budget)
and birefnet OOM-killed at 2,931 MB with the box idle. So it runs off-box, and
every failure path here must return control promptly to the legacy renderer.
"""

import json
import time

import pytest
from genlab_core.action.matte_worker import (
    WORKER_STALE_AFTER_S,
    CropPlan,
    CropRow,
    HSVSpec,
    MatteRequest,
    SkipReason,
    request_matte,
    worker_alive,
)


@pytest.fixture
def q(tmp_path):
    for n in ("queued", "running", "done", "failed"):
        (tmp_path / n).mkdir()
    return tmp_path


def beat(root, age=0.0):
    hb = root / "worker.heartbeat"
    hb.write_text("1")
    if age:
        import os

        t = time.time() - age
        os.utime(hb, (t, t))


# subject_spec and crop_plan are REQUIRED now: the worker refuses a partial
# spec at runtime, so the request type refuses to express one at all.
REQ = MatteRequest(
    clip_path="/clips/a.mp4",
    frames_dir="/frames/a",
    subject_spec=HSVSpec(hue_deg=235.0, hue_tol=25.0, sat_min=0.25, val_min=0.10),
    crop_plan=CropPlan(rows=(CropRow(out=0, mag=2.0361, cx=0.65, cy=0.5, src_h=943),)),
    niche_id="sports",
    blueprint_id="bp1",
)


def test_no_heartbeat_returns_immediately_and_does_not_queue(q):
    """The laptop being closed is the normal case, not an error."""
    calls = []
    res, reason = request_matte(REQ, root=q, sleep=lambda s: calls.append(s))
    assert res is None and reason == SkipReason.WORKER_UNAVAILABLE
    assert calls == [], "waited on a worker that was never there"
    assert list((q / "queued").glob("*.json")) == []


def test_a_stale_heartbeat_counts_as_no_worker(q):
    beat(q, age=WORKER_STALE_AFTER_S + 30)
    res, reason = request_matte(REQ, root=q, sleep=lambda s: None)
    assert res is None and reason == SkipReason.WORKER_UNAVAILABLE


def test_a_timeout_returns_a_reason_and_never_hangs(q):
    """§2's pin: a simulated timeout produces a legacy reel and a log line."""
    beat(q)
    clock = {"t": 1000.0}
    res, reason = request_matte(
        REQ,
        root=q,
        timeout_s=60,
        poll_s=5,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        now=lambda: clock["t"],
    )
    assert res is None and reason == SkipReason.TIMEOUT
    assert clock["t"] <= 1000.0 + 60 + 5, "waited past the deadline"


def test_a_completed_job_returns_the_mask_dir(q):
    beat(q)
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
        for p in (q / "queued").glob("*.json"):
            jid = json.loads(p.read_text())["job_id"]
            (q / "done" / f"{jid}.json").write_text(
                json.dumps({"mask_dir": "/masks/a", "frames": 384, "seconds": 540.0})
            )

    res, reason = request_matte(
        REQ, root=q, timeout_s=600, poll_s=5, sleep=sleep, now=lambda: clock["t"]
    )
    assert reason == "" and res is not None
    assert res.mask_dir == "/masks/a" and res.frames == 384 and res.ok


def test_a_worker_side_failure_falls_back_rather_than_raising(q):
    beat(q)
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
        for p in (q / "queued").glob("*.json"):
            jid = json.loads(p.read_text())["job_id"]
            (q / "failed" / f"{jid}.json").write_text(json.dumps({"reason": "OOM"}))

    res, reason = request_matte(
        REQ, root=q, timeout_s=600, poll_s=5, sleep=sleep, now=lambda: clock["t"]
    )
    assert res is None and reason == SkipReason.FAILED


def test_a_zero_frame_result_is_a_failure_not_a_success(q):
    """An empty matte set would render a craft reel with no effects at all."""
    beat(q)
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
        for p in (q / "queued").glob("*.json"):
            jid = json.loads(p.read_text())["job_id"]
            (q / "done" / f"{jid}.json").write_text(
                json.dumps({"mask_dir": "/masks/a", "frames": 0})
            )

    res, reason = request_matte(
        REQ, root=q, timeout_s=600, poll_s=5, sleep=sleep, now=lambda: clock["t"]
    )
    assert res is None and reason == SkipReason.FAILED


def test_an_unreadable_result_file_does_not_raise(q):
    beat(q)
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
        for p in (q / "queued").glob("*.json"):
            jid = json.loads(p.read_text())["job_id"]
            (q / "done" / f"{jid}.json").write_text("{ not json")

    res, reason = request_matte(
        REQ, root=q, timeout_s=600, poll_s=5, sleep=sleep, now=lambda: clock["t"]
    )
    assert res is None and reason == SkipReason.FAILED


def test_an_unwritable_queue_falls_back_instead_of_raising(q, monkeypatch):
    beat(q)
    import pathlib

    def boom(self, *a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    res, reason = request_matte(REQ, root=q, sleep=lambda s: None)
    assert res is None and reason == SkipReason.QUEUE_UNWRITABLE


def test_the_job_file_appears_atomically(q):
    """A worker must never claim a half-written job."""
    beat(q)
    seen = []

    def sleep(s):
        for p in (q / "queued").iterdir():
            seen.append(p.name)
        for p in (q / "queued").glob("*.json"):
            jid = json.loads(p.read_text())["job_id"]
            (q / "done" / f"{jid}.json").write_text(json.dumps({"mask_dir": "/m", "frames": 1}))

    clock = {"t": 0.0}
    request_matte(
        REQ,
        root=q,
        timeout_s=60,
        poll_s=1,
        sleep=sleep,
        now=lambda: clock.__setitem__("t", clock["t"] + 1) or clock["t"],
    )
    assert seen and all(n.endswith(".json") and not n.startswith(".") for n in seen)


def test_worker_alive_is_false_when_the_queue_does_not_exist(tmp_path):
    assert worker_alive(tmp_path / "nope") is False


def test_every_skip_reason_is_a_distinct_string():
    vals = [
        SkipReason.WORKER_UNAVAILABLE,
        SkipReason.TIMEOUT,
        SkipReason.FAILED,
        SkipReason.QUEUE_UNWRITABLE,
    ]
    assert len(set(vals)) == 4 and all(isinstance(v, str) for v in vals)
