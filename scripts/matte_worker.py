#!/usr/bin/env python3
"""The matte worker: runs on the Mac, serves matte jobs posted from prod.

RENDER-01 Part 4 section 3.

WHY OFF-BOX
-----------
SAM2 cannot run on prod. Measured: 138 minutes for a 96-frame segment, and it
OOMs on the 4 GB VPS. It runs on this laptop's MPS at ~1.8 s/frame. So craft's
mattes are produced here and posted back, and prod waits a bounded time.

WHY THAT IS SAFE
----------------
Craft never blocks a publish. If this worker is asleep, unreachable, or slow,
`request_matte` returns a skip reason and prod renders legacy exactly as it does
today. The worst outcome is a legacy reel plus `craft_skipped=worker_unavailable`
in the run report -- which is the fallback working, not an incident.

    python scripts/matte_worker.py --host genlab-prod --root /opt/genlab/.runtime/mattes
    python scripts/matte_worker.py --local /tmp/mattes --once      # test loop
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "genlab-core" / "src"))

from genlab_core.action.matte_daemon import serve  # noqa: E402
from genlab_core.action.matte_queue import LocalTransport, SSHTransport  # noqa: E402

logger = logging.getLogger("matte_worker")
JOB_LOG = REPO / ".runtime" / "matte_worker_jobs.jsonl"


def _area_band(masks: dict) -> tuple[float, float, float]:
    """Min / mean / max of the subject's share of frame, as percentages.

    The number that says whether a matte is a PERSON or a garment: the ACTION
    work found that above ~55% the tracker has latched onto clothing, and an
    empty-frame count alone never showed it.
    """
    import numpy as np

    if not masks:
        return 0.0, 0.0, 0.0
    fr = [float((np.asarray(m) > 0.5).mean()) * 100.0 for m in masks.values()]
    return round(min(fr), 2), round(sum(fr) / len(fr), 2), round(max(fr), 2)


def make_matte_fn(*, device: str = "mps"):
    """Build the real matte callable. Imports are deferred so a worker started
    without torch still reports a clean reason rather than dying at import."""

    def matte_fn(job: dict) -> dict:
        t0 = time.time()
        clip = job.get("clip_path") or job.get("clip") or ""
        n_frames = int(job.get("n_frames") or 0)
        job_id = job.get("job_id", "?")
        logger.info("[matte-worker] job %s: %s (%d frames)", job_id, clip, n_frames)

        from genlab_core.action.matte import build_mattes  # noqa: F401

        try:
            from genlab_core.action import sam2_backend as backend  # type: ignore
        except ImportError:
            # No backend wired yet. Report it as a REASON rather than raising --
            # prod must see "worker cannot matte", not a traceback, and must
            # render legacy either way.
            return {
                "frames": 0,
                "reason": "sam2_backend not available on this worker",
                "seconds": round(time.time() - t0, 1),
            }

        masks, report = backend.mattes_for(job, device=device)
        lo, mean, hi = _area_band(masks)
        empty = sum(1 for m in masks.values() if float((m > 0.5).mean()) < 0.005)
        result = {
            "frames": len(masks),
            "empty_frames": empty,
            "area_pct_min": lo,
            "area_pct_mean": mean,
            "area_pct_max": hi,
            "seconds": round(time.time() - t0, 1),
            "seconds_per_frame": round((time.time() - t0) / max(len(masks), 1), 2),
            "report": getattr(report, "__dict__", {}),
        }
        _log_job(job_id, clip, result)
        return result

    return matte_fn


def _log_job(job_id: str, clip: str, result: dict) -> None:
    """One line per job: frames, seconds, empty frames, area band.

    A worker that silently produces garment mattes for a week looks exactly like
    a worker that is working, so the numbers go on disk per job rather than only
    into a log level nobody reads.
    """
    JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": int(time.time()), "job_id": job_id, "clip": clip, **result}
    with JOB_LOG.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", help="ssh host holding the queue (e.g. genlab-prod)")
    ap.add_argument("--root", default="/opt/genlab/.runtime/mattes")
    ap.add_argument("--local", help="serve a LOCAL queue directory instead")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--once", action="store_true", help="one poll, then exit")
    ap.add_argument("--iterations", type=int, default=None)
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.local:
        transport = LocalTransport(Path(a.local))
        logger.info("[matte-worker] serving LOCAL queue %s", a.local)
    elif a.host:
        transport = SSHTransport(
            a.host, a.root, ssh_opts=("-o", "ConnectTimeout=10", "-o", "BatchMode=yes")
        )
        logger.info("[matte-worker] serving %s:%s", a.host, a.root)
    else:
        ap.error("one of --host or --local is required")

    stats = serve(
        transport, make_matte_fn(device=a.device), max_iterations=1 if a.once else a.iterations
    )
    logger.info(
        "[matte-worker] claimed=%d ok=%d failed=%d", stats.claimed, stats.succeeded, stats.failed
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
