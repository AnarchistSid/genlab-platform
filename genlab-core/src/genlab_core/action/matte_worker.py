"""Ask an off-box worker for SAM2 mattes, and never let the answer block a publish.

WHY OFF-BOX (measured on prod, 2026-09-17, 2 cores / 3.8 GB / no GPU):

    SAM2 propagation   20.66 s/frame  ->  138 min for a 384-frame reel
    budget                                 15 min
    birefnet foreground                    OOM-KILLED at 2,931 MB, box idle

The matte step does not fit. It runs on a machine with a GPU (the Mac's MPS did
96 frames in 2.2 min, ~9 min extrapolated for 384) and the pipeline asks for it.

THE RULE THAT MATTERS MORE THAN THE SPEED:

    worker unreachable or timed out  ->  that fire renders LEGACY

Craft is additive. FFmpeg producing a publishable reel standalone is the
standing guarantee (CLAUDE.md render-path rule), and a renderer that can be
blocked by an absent laptop would convert that guarantee into a dependency. So
every failure here returns None, promptly, with a reason -- never an exception,
never an unbounded wait.

PROTOCOL. Deliberately files over SSH rather than a broker: Redis on prod is
bound to 127.0.0.1, and reaching it from the Mac would mean exposing a port or
tunnelling. The worker POLLS, so nothing needs to connect inward to a laptop
behind NAT.

    <root>/queued/<job>.json     pipeline writes; worker claims
    <root>/running/<job>.json    worker claims by moving it (atomic rename)
    <root>/done/<job>.json       worker writes result + mask paths
    <root>/failed/<job>.json     worker writes a reason it could not finish
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path(os.environ.get("GENLAB_MATTE_QUEUE", "/opt/genlab/.matte_jobs"))
# §2: 20 minutes. The measured Mac run is ~9 min for 384 frames, so this is
# roughly 2x headroom -- long enough to absorb a slow pull, short enough that a
# dead worker costs one fire rather than the publish window.
DEFAULT_TIMEOUT_S = 1200
POLL_INTERVAL_S = 5.0
# Older than this and a worker is not running, whatever the queue looks like.
WORKER_STALE_AFTER_S = 180


class SkipReason:
    """Values recorded as ``craft_skipped`` in the run report."""

    WORKER_UNAVAILABLE = "worker_unavailable"
    TIMEOUT = "worker_timeout"
    FAILED = "worker_failed"
    QUEUE_UNWRITABLE = "queue_unwritable"


@dataclass(frozen=True)
class MatteRequest:
    clip_path: str
    frames_dir: str
    annotations: list[dict] = field(default_factory=list)
    cuts: list[int] = field(default_factory=list)
    niche_id: str = ""
    blueprint_id: str = ""


@dataclass(frozen=True)
class MatteResult:
    job_id: str
    mask_dir: str
    frames: int
    seconds: float

    @property
    def ok(self) -> bool:
        return self.frames > 0


def _dirs(root: Path) -> dict[str, Path]:
    return {n: root / n for n in ("queued", "running", "done", "failed")}


def worker_alive(root: Path | None = None, *, now: float | None = None) -> bool:
    """Has a worker checked in recently?

    Asked BEFORE queueing, so an absent worker costs a heartbeat read rather
    than the full timeout. A missing heartbeat is not an error -- it is the
    normal state when the laptop is closed.
    """
    root = Path(root or DEFAULT_ROOT)
    hb = root / "worker.heartbeat"
    try:
        age = (now or time.time()) - hb.stat().st_mtime
    except OSError:
        return False
    return age <= WORKER_STALE_AFTER_S


def request_matte(req: MatteRequest, *, root: Path | None = None,
                  timeout_s: float = DEFAULT_TIMEOUT_S,
                  poll_s: float = POLL_INTERVAL_S,
                  sleep=time.sleep, now=time.time) -> tuple[MatteResult | None, str]:
    """Queue a matte job and wait for it. Returns ``(result, reason)``.

    ``result`` is None on every failure path and ``reason`` is one of
    ``SkipReason``. This function does not raise: the caller is a render stage
    whose fallback is the legacy renderer, and an exception there would take a
    publishable reel down with it.
    """
    root = Path(root or DEFAULT_ROOT)
    d = _dirs(root)

    if not worker_alive(root, now=now()):
        logger.warning("[matte] no worker heartbeat in %ds — rendering legacy this fire",
                       WORKER_STALE_AFTER_S)
        return None, SkipReason.WORKER_UNAVAILABLE

    job_id = f"{req.niche_id or 'job'}-{uuid.uuid4().hex[:10]}"
    payload = {"job_id": job_id, "clip_path": req.clip_path,
               "frames_dir": req.frames_dir, "annotations": req.annotations,
               "cuts": req.cuts, "niche_id": req.niche_id,
               "blueprint_id": req.blueprint_id, "queued_at": now()}
    try:
        for p in d.values():
            p.mkdir(parents=True, exist_ok=True)
        tmp = d["queued"] / f".{job_id}.tmp"
        tmp.write_text(json.dumps(payload))
        tmp.rename(d["queued"] / f"{job_id}.json")   # atomic: no half-read job
    except OSError as exc:
        logger.warning("[matte] cannot write the queue at %s (%s) — rendering legacy",
                       root, exc)
        return None, SkipReason.QUEUE_UNWRITABLE

    logger.info("[matte] queued %s (timeout %.0fs)", job_id, timeout_s)
    deadline = now() + timeout_s
    while now() < deadline:
        done = d["done"] / f"{job_id}.json"
        failed = d["failed"] / f"{job_id}.json"
        if done.exists():
            try:
                r = json.loads(done.read_text())
                res = MatteResult(job_id=job_id, mask_dir=r["mask_dir"],
                                  frames=int(r.get("frames", 0)),
                                  seconds=float(r.get("seconds", 0.0)))
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("[matte] %s completed with an unreadable result (%s)",
                               job_id, exc)
                return None, SkipReason.FAILED
            logger.info("[matte] %s done: %d frames in %.1fs", job_id, res.frames,
                        res.seconds)
            return (res, "") if res.ok else (None, SkipReason.FAILED)
        if failed.exists():
            reason = ""
            try:
                reason = json.loads(failed.read_text()).get("reason", "")
            except (OSError, ValueError):
                pass
            logger.warning("[matte] %s failed on the worker: %s", job_id, reason[:120])
            return None, SkipReason.FAILED
        sleep(poll_s)

    logger.warning("[matte] %s timed out after %.0fs — rendering legacy this fire",
                   job_id, timeout_s)
    return None, SkipReason.TIMEOUT
