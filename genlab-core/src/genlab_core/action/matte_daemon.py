"""The worker loop: claim a matte job, run it, push the masks back, heartbeat.

Runs where there is a GPU. The matte function is INJECTED, so this module holds
no torch, no SAM2, and no rembg -- the loop can be tested in milliseconds and
the heavy imports stay in the script that starts it.

Two properties the loop is built around:

* **The heartbeat is written even when idle, and even when a job fails.** It
  answers "is a worker running", not "did the last job succeed". Tying it to
  success would make one bad clip look like a dead laptop and silently route
  every subsequent fire to legacy.
* **A job that raises is reported as failed, not retried forever.** The pipeline
  is waiting with a deadline; a worker stuck re-running a poisoned clip is
  indistinguishable from a dead one, except that it also burns the GPU.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from genlab_core.action.matte_queue import Transport

logger = logging.getLogger(__name__)

IDLE_POLL_S = 5.0
HEARTBEAT_EVERY_S = 30.0


@dataclass
class WorkerStats:
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0


def _beat(transport: Transport) -> bool:
    """Heartbeat, guarded. A missed beat routes the next fire to legacy -- that
    is the designed behaviour -- so log and keep working rather than exit."""
    try:
        transport.heartbeat()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[matte-worker] heartbeat failed: %s", exc)
        return False


def run_once(
    transport: Transport, matte_fn: Callable[[dict], dict], stats: WorkerStats | None = None
) -> bool:
    """Claim and run at most one job. Returns True if one was processed."""
    stats = stats or WorkerStats()
    names = transport.list_queued()
    if not names:
        return False
    for name in names:
        job = transport.claim(name)
        if job is None:
            continue  # another worker won the rename
        stats.claimed += 1
        job_id = job.get("job_id") or Path(name).stem
        t0 = time.time()
        try:
            result = matte_fn(job)
            result.setdefault("seconds", round(time.time() - t0, 1))
            ok = int(result.get("frames", 0)) > 0
            if not ok:
                result.setdefault("reason", "matte produced zero frames")
            transport.finish(job_id, result, ok=ok)
            stats.succeeded += int(ok)
            stats.failed += int(not ok)
            logger.info(
                "[matte-worker] %s %s in %.1fs", job_id, "ok" if ok else "FAILED", time.time() - t0
            )
        except Exception as exc:  # noqa: BLE001 — one bad clip must not end the loop
            logger.warning("[matte-worker] %s raised: %s", job_id, exc, exc_info=True)
            transport.finish(
                job_id,
                {
                    "reason": f"{type(exc).__name__}: {exc}"[:300],
                    "traceback": traceback.format_exc()[-800:],
                    "seconds": round(time.time() - t0, 1),
                },
                ok=False,
            )
            stats.failed += 1
        return True
    return False


def serve(
    transport: Transport,
    matte_fn: Callable[[dict], dict],
    *,
    idle_poll_s: float = IDLE_POLL_S,
    heartbeat_every_s: float = HEARTBEAT_EVERY_S,
    max_iterations: int | None = None,
    sleep=time.sleep,
    now=time.time,
) -> WorkerStats:
    """Poll forever (or ``max_iterations`` times, for tests)."""
    stats = WorkerStats()
    last_beat = 0.0
    i = 0
    # Guarded: an unreachable queue at startup must not kill the worker
    # before it ever polls.
    _beat(transport)
    while max_iterations is None or i < max_iterations:
        i += 1
        if now() - last_beat >= heartbeat_every_s:
            if _beat(transport):
                last_beat = now()
        try:
            did = run_once(transport, matte_fn, stats)
        except Exception as exc:  # noqa: BLE001 — the loop outlives the queue
            logger.warning("[matte-worker] poll failed: %s", exc, exc_info=True)
            did = False
        if not did:
            sleep(idle_poll_s)
    return stats
